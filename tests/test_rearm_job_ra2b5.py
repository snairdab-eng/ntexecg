"""RA-2b SUB-PASO 5 — RearmJob: plomería sobre el cerebro blindado.

Tests con fakes de market data y DRY_RUN global (cero HTTP): no-op sin
posiciones · caso feliz (delivery con client_id -r2, cancelAfter, precio
re-snapped al tick) · IDEMPOTENCIA del barrido (el candado anti-doble-orden)
· EXITING/UNKNOWN ⇒ el motor mata (R-RA5) · enabled apagado a media vida ⇒
skip · perfil cambiado ⇒ qty recalculada · perfil sin qty ⇒ pierna muere ·
excepción en A no bloquea B (transacción por posición) · kill-switch ⇒ nada
sale (todo DRY_RUN, jamás SENT) · el job no toca la posición (invariante d)
· AuditLog por camino.
"""
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.models.audit_log import AuditLog
from app.models.position_state import PositionState
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.models.symbol_map import SymbolMap
from app.models.webhook_delivery import WebhookDelivery
from app.services.market_data_service import MarketDataService
from app.services.rearm_job import rearm_sweep

# Tiempos (ET jul = UTC−4): abierto 09:30 ET, envío inicial 09:30, barrido a
# las 12:00 ET → 9000 s desde el envío ≥ ciclo 3720 → re-envío debido.
_OPEN_UTC = "2026-07-14T13:30:00+00:00"
_NOW = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)      # 12:00 ET
_SID = "ra2b5_es"
_SIGNAL = uuid.uuid4()


class _Bars:
    """Provider fake: barras 5m ET continuas 09:35–12:00 que NO tocan nada
    (limit 5492 / sl 5488 / tp 5620 quedan fuera de [5496, 5504])."""

    def __init__(self, atr=8.0):
        self._atr = atr

    async def get_bars(self, symbol, timeframe, limit=300):
        from datetime import timedelta
        t = datetime(2026, 7, 14, 9, 35)
        out = []
        while t <= datetime(2026, 7, 14, 12, 0):
            out.append({"time": t.strftime("%Y-%m-%dT%H:%M:%S"),
                        "open": 5500.0, "high": 5504.0, "low": 5496.0,
                        "close": 5500.0, "volume": 10})
            t += timedelta(minutes=5)
        return out

    async def get_atr(self, symbol, timeframe, period=14):
        return self._atr

    async def is_active(self, symbol):
        return True


_MD = MarketDataService(_Bars())


def _cfg(rearm=None, quantities=(4, 3, 3), ttl=3600):
    cfg = {"backstop_points": 12.0, "tp_nominal_long": 15.0,
           "entry_reserve_timeout_seconds": ttl,
           "scale_entry": {"mode": "execute", "quantities": list(quantities),
                           "levels": [1.0, 2.0], "max_micro_contracts": 10}}
    if rearm is not None:
        cfg["scale_entry"]["rearm"] = rearm
    return cfg


def _estado(cycle_n=1, state="working"):
    return {"legs": [{"leg_index": 2, "side": "long", "level_atr": 1.0,
                      "limit_price": 5492.1,     # fuera de rejilla a propósito
                      "qty": 3, "cycle_n": cycle_n, "last_client_id": None,
                      "last_sent_at": _OPEN_UTC, "state": state,
                      "death_reason": None}],
            "signal_atr": 8.0, "sl_price": 5488.0, "tp_price": 5620.0,
            "updated_at": _OPEN_UTC}


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                 connect_args={"check_same_thread": False},
                                 poolclass=StaticPool)
    fac = async_sessionmaker(engine, class_=AsyncSession,
                             expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield fac
    await engine.dispose()


async def _seed(fac, *, sid=_SID, rearm=None, quantities=(4, 3, 3),
                ttl=3600, pos_state="LONG", estado=None, symbol="MESU2025",
                tv="MES"):
    async with fac() as db:
        db.add(SymbolMap(tv_symbol=tv, mapped_symbol=symbol, exchange="CME",
                         contract_type="futures_micro", tick_size="0.25",
                         pine_script_config="x", active=True))
        db.add(Strategy(strategy_id=sid, name=sid, asset_symbol=tv,
                        status="live", enabled=True))
        db.add(StrategyProfile(
            strategy_id=sid,
            traderspost_webhook_url="https://webhooks.traderspost.io/x/t",
            pipeline_config_json=_cfg(rearm, quantities, ttl)))
        plan = {"opened_at": _OPEN_UTC, "entry_style": "market"}
        if estado is not None:
            plan["rearm"] = estado
        db.add(PositionState(strategy_id=sid, account_id="paper_default",
                             symbol=symbol, state=pos_state, direction="long",
                             quantity=10, entry_price=5500.0,
                             entry_signal_id=_SIGNAL,
                             risk_plan_json=plan))
        await db.commit()


async def _uno(fac, model, **where):
    async with fac() as db:
        q = select(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return list((await db.execute(q)).scalars().all())


async def _pos(fac, sid=_SID):
    (p,) = await _uno(fac, PositionState, strategy_id=sid)
    return p


_ON = {"enabled": True, "max_ciclos": 3}


# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_sin_posiciones_no_op(factory):
    r = await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert r == {"posiciones": 0, "errores": 0}


@pytest.mark.asyncio
async def test_caso_feliz_reenvia_con_correlacion_y_rejilla(factory):
    await _seed(factory, rearm=_ON, estado=_estado())
    r = await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert r.get("reenviado") == 1 and r["errores"] == 0
    (d,) = await _uno(factory, WebhookDelivery, strategy_id=_SID)
    p = d.payload_json
    assert p["orderType"] == "limit"
    assert p["limitPrice"] == pytest.approx(5492.0)       # re-snap tick 0.25
    assert p["cancelAfter"] == 3600
    assert p["quantity"] == 3                             # quantities[1]
    assert p["stopLoss"]["stopPrice"] == pytest.approx(5488.0)
    assert p["takeProfit"]["limitPrice"] == pytest.approx(5620.0)
    assert p["extras"]["rearm_cycle"] == 2
    assert p["extras"]["client_id"] == f"{_SIGNAL}-r2"    # correlación §5
    # estado avanzado y persistido
    pos = await _pos(factory)
    leg = pos.risk_plan_json["rearm"]["legs"][0]
    assert leg["cycle_n"] == 2 and leg["state"] == "working"
    assert leg["last_client_id"] == f"{_SIGNAL}-r2"
    # AuditLog REARM_LEG
    logs = await _uno(factory, AuditLog, action="REARM_LEG")
    assert len(logs) == 1 and logs[0].new_value_json["ciclo"] == 2


@pytest.mark.asyncio
async def test_idempotencia_dos_barridos_sin_avanzar_reloj(factory):
    """El candado anti-doble-orden: el 2º barrido con el MISMO reloj ve el
    ciclo recién avanzado (last_sent_at = now) → timing ⇒ ESPERAR, cero
    deliveries nuevas."""
    await _seed(factory, rearm=_ON, estado=_estado())
    await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    ds = await _uno(factory, WebhookDelivery, strategy_id=_SID)
    assert len(ds) == 1                                    # SOLO el primero
    pos = await _pos(factory)
    assert pos.risk_plan_json["rearm"]["legs"][0]["cycle_n"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("estado_pos", ["EXITING", "UNKNOWN", "FLAT"])
async def test_posicion_no_abierta_el_motor_mata(factory, estado_pos):
    await _seed(factory, rearm=_ON, estado=_estado(), pos_state=estado_pos)
    r = await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert r.get("kill") == 1
    pos = await _pos(factory)
    leg = pos.risk_plan_json["rearm"]["legs"][0]
    assert leg["state"] == "dead" and leg["death_reason"] == "R-RA5"
    (k,) = await _uno(factory, AuditLog, action="REARM_KILL")
    assert k.new_value_json["regla"] == "R-RA5"
    assert await _uno(factory, WebhookDelivery, strategy_id=_SID) == []


@pytest.mark.asyncio
async def test_enabled_apagado_a_media_vida_skip(factory):
    # estado sembrado (la entrada iba con rearm) pero la config vigente lo
    # apagó → RE-VERIFICACIÓN del barrido: no re-armar más.
    await _seed(factory, rearm={"enabled": False, "max_ciclos": 3},
                estado=_estado())
    r = await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert r.get("skip:disabled") == 1
    (s,) = await _uno(factory, AuditLog, action="REARM_SKIP")
    assert s.new_value_json["motivo"] == "disabled"
    pos = await _pos(factory)
    assert pos.risk_plan_json["rearm"]["legs"][0]["state"] == "working"
    assert await _uno(factory, WebhookDelivery, strategy_id=_SID) == []


@pytest.mark.asyncio
async def test_e1_ttl_del_config_vigente_skip(factory):
    await _seed(factory, rearm=_ON, ttl=1800, estado=_estado())
    r = await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert r.get("skip:ttl_incoherente") == 1
    assert await _uno(factory, WebhookDelivery, strategy_id=_SID) == []


@pytest.mark.asyncio
async def test_estado_ilegible_skip(factory):
    roto = _estado()
    roto["legs"][0].pop("qty")                    # shape mutilado ⇒ ilegible
    await _seed(factory, rearm=_ON, estado=roto)
    r = await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert r.get("skip:estado_ilegible") == 1
    (s,) = await _uno(factory, AuditLog, action="REARM_SKIP")
    assert s.new_value_json["motivo"] == "estado_ilegible"


@pytest.mark.asyncio
async def test_perfil_cambiado_qty_recalculada(factory):
    # el perfil vigente ahora asigna 5 micros a C2 (la siembra decía 3)
    await _seed(factory, rearm=_ON, quantities=(1, 5, 1), estado=_estado())
    await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    (d,) = await _uno(factory, WebhookDelivery, strategy_id=_SID)
    assert d.payload_json["quantity"] == 5                 # recalculada


@pytest.mark.asyncio
async def test_perfil_sin_qty_mata_la_pierna(factory):
    await _seed(factory, rearm=_ON, quantities=(4, 0, 6), estado=_estado())
    r = await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert r.get("kill") == 1
    pos = await _pos(factory)
    leg = pos.risk_plan_json["rearm"]["legs"][0]
    assert leg["state"] == "dead" and leg["death_reason"] == "perfil_sin_qty"
    assert await _uno(factory, WebhookDelivery, strategy_id=_SID) == []


@pytest.mark.asyncio
async def test_feed_ciego_skip_rra1(factory):
    class _Ciego:
        async def get_bars(self, *a, **k): return []
        async def get_atr(self, *a, **k): return None
        async def is_active(self, *a, **k): return False
    await _seed(factory, rearm=_ON, estado=_estado())
    r = await rearm_sweep(settings, MarketDataService(_Ciego()),
                          session_factory=factory, now=_NOW)
    assert r.get("skip:R-RA1") == 1
    assert await _uno(factory, WebhookDelivery, strategy_id=_SID) == []


@pytest.mark.asyncio
async def test_excepcion_en_a_no_bloquea_b(factory, monkeypatch):
    """Transacción POR posición: A revienta (fake), B re-envía igual; el
    error de A queda auditado REARM_SKIP{error} en sesión fresca."""
    import app.services.rearm_job as rj
    await _seed(factory, sid="ra2b5_a", rearm=_ON, estado=_estado(),
                symbol="AAA", tv="AAA")
    await _seed(factory, sid="ra2b5_b", rearm=_ON, estado=_estado(),
                symbol="BBB", tv="BBB")
    real = rj.obtener_inferencia

    async def _boom(md, symbol, **kw):
        if symbol == "AAA":
            raise RuntimeError("feed reventado")
        return await real(md, symbol, **kw)

    monkeypatch.setattr(rj, "obtener_inferencia", _boom)
    r = await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert r["errores"] == 1 and r.get("reenviado") == 1   # B pasó
    ds = await _uno(factory, WebhookDelivery, strategy_id="ra2b5_b")
    assert len(ds) == 1
    assert await _uno(factory, WebhookDelivery, strategy_id="ra2b5_a") == []
    errores = [a for a in await _uno(factory, AuditLog, action="REARM_SKIP")
               if a.new_value_json.get("motivo") == "error"]
    assert len(errores) == 1 and "feed reventado" in errores[0].new_value_json["error"]


@pytest.mark.asyncio
async def test_kill_switch_nada_sale_y_posicion_intacta(factory):
    """DRY_RUN global (kill-switch por capas, como una entrada): la delivery
    queda DRY_RUN — nada llega al broker — y el ciclo AVANZA (decisión
    documentada: el mundo paper observa el MISMO timing sin-solape que el
    vivo; si no avanzara, el job re-'enviaría' cada 60 s). Y el job JAMÁS
    toca la posición (invariante d)."""
    await _seed(factory, rearm=_ON, estado=_estado())
    antes = await _pos(factory)
    foto = (antes.state, antes.direction, antes.quantity,
            float(antes.entry_price))
    await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    (d,) = await _uno(factory, WebhookDelivery, strategy_id=_SID)
    assert d.status == "DRY_RUN" and d.sent_at is None     # nada salió
    pos = await _pos(factory)
    assert (pos.state, pos.direction, pos.quantity,
            float(pos.entry_price)) == foto
    assert pos.risk_plan_json["rearm"]["legs"][0]["cycle_n"] == 2


# ═══════════════════════════════════════════════════════════════════════════
# E3 — FAILED ambiguo ⇒ MATAR, jamás reintentar
# ═══════════════════════════════════════════════════════════════════════════

def _send_fake(monkeypatch, result_factory):
    """Sustituye TradersPostClient.send por un fake que devuelve el resultado
    dado y cuenta llamadas."""
    from app.services.traderspost_client import TradersPostClient
    llamadas = {"n": 0}

    async def _send(self, url, payload, **kw):
        llamadas["n"] += 1
        return result_factory(payload)

    monkeypatch.setattr(TradersPostClient, "send", _send)
    return llamadas


@pytest.mark.asyncio
async def test_e3a_failed_inequivoco_500_reintenta(factory, monkeypatch):
    """(a) Todos los destinos FAILED con respuesta HTTP (500) — INEQUÍVOCO:
    la orden seguro no existe → el ciclo NO avanza y el SIGUIENTE barrido
    reintenta (comportamiento previo, ahora restringido a lo inequívoco)."""
    from app.services.traderspost_client import WebhookDeliveryResult
    await _seed(factory, rearm=_ON, estado=_estado())
    llamadas = _send_fake(monkeypatch, lambda p: WebhookDeliveryResult(
        status="FAILED", payload_json=p, url_masked="x",
        response_status_code=500, error_message="http_500", attempts=3,
        any_ambiguous_attempt=False))
    r = await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert r.get("skip:envio_fallido") == 1
    pos = await _pos(factory)
    leg = pos.risk_plan_json["rearm"]["legs"][0]
    assert leg["state"] == "working" and leg["cycle_n"] == 1   # NO avanza
    n1 = llamadas["n"]
    await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert llamadas["n"] > n1                                  # reintenta


@pytest.mark.asyncio
async def test_e3b_failed_ambiguo_timeout_mata_y_no_reintenta(factory,
                                                              monkeypatch):
    """(b) FAILED con ≥1 intento AMBIGUO (timeout sin respuesta): la orden
    PUDO quedar viva → la pierna se MATA (REARM_KILL{envio_ambiguo}) y el
    siguiente barrido NO re-envía nada."""
    from app.services.traderspost_client import WebhookDeliveryResult
    await _seed(factory, rearm=_ON, estado=_estado())
    llamadas = _send_fake(monkeypatch, lambda p: WebhookDeliveryResult(
        status="FAILED", payload_json=p, url_masked="x",
        response_status_code=None, error_message="ReadTimeout", attempts=3,
        any_ambiguous_attempt=True))
    r = await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert r.get("kill") == 1
    pos = await _pos(factory)
    leg = pos.risk_plan_json["rearm"]["legs"][0]
    assert leg["state"] == "dead" and leg["death_reason"] == "envio_ambiguo"
    (k,) = await _uno(factory, AuditLog, action="REARM_KILL")
    assert k.new_value_json["regla"] == "envio_ambiguo"
    assert "posible orden viva" in k.new_value_json["detalle"]
    n1 = llamadas["n"]
    await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert llamadas["n"] == n1                                 # muerta: nada


@pytest.mark.asyncio
async def test_e3_timeout_en_intento_previo_contamina_aunque_el_ultimo_responda(
        factory, monkeypatch):
    """El caso que el ÚLTIMO intento no delata: intento 1 timeout (ambiguo),
    intento final con respuesta 500 — el flag por-intento del cliente manda
    ⇒ MATAR igual (E3: 'si CUALQUIER intento terminó ambiguo')."""
    from app.services.traderspost_client import WebhookDeliveryResult
    await _seed(factory, rearm=_ON, estado=_estado())
    _send_fake(monkeypatch, lambda p: WebhookDeliveryResult(
        status="FAILED", payload_json=p, url_masked="x",
        response_status_code=500, error_message="http_500", attempts=3,
        any_ambiguous_attempt=True))                # 1º intento fue timeout
    r = await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert r.get("kill") == 1
    pos = await _pos(factory)
    assert pos.risk_plan_json["rearm"]["legs"][0]["death_reason"] == \
        "envio_ambiguo"


@pytest.mark.asyncio
async def test_e3_clasificacion_por_intento_del_cliente(monkeypatch):
    """Unidad del cliente: ReadTimeout ⇒ ambiguo; ConnectError (canal jamás
    establecido) ⇒ inequívoco. Sin HTTP real (httpx.AsyncClient fakeado)."""
    import httpx as _httpx
    from datetime import datetime, timezone
    from app.services.traderspost_client import TradersPostClient

    def _fake_ac(exc):
        class _AC:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, *a, **k): raise exc
        return _AC

    client = TradersPostClient(settings)
    for exc, ambiguo in ((_httpx.ReadTimeout("t"), True),
                         (_httpx.ConnectTimeout("t"), True),
                         (_httpx.ConnectError("refused"), False)):
        monkeypatch.setattr("app.services.traderspost_client.httpx.AsyncClient",
                            _fake_ac(exc))
        r = await client.send("https://x/hook?token=t", {"a": 1},
                              signal_role="entry_long", dry_run=False,
                              signal_ts=datetime.now(timezone.utc),
                              retry_attempts=1)
        assert r.status == "FAILED"
        assert r.any_ambiguous_attempt is ambiguo, type(exc).__name__


@pytest.mark.asyncio
async def test_rra6_stop_tocado_mata_huerfana_e2e(factory):
    class _Toca(_Bars):
        async def get_bars(self, symbol, timeframe, limit=300):
            bars = await super().get_bars(symbol, timeframe, limit)
            bars[10]["low"] = 5487.0                       # cruza el sl 5488
            return bars
    await _seed(factory, rearm=_ON, estado=_estado())
    r = await rearm_sweep(settings, MarketDataService(_Toca()),
                          session_factory=factory, now=_NOW)
    assert r.get("kill") == 1
    (k,) = await _uno(factory, AuditLog, action="REARM_KILL")
    assert k.new_value_json["regla"] == "R-RA6"


@pytest.mark.asyncio
async def test_rra2_toque_con_orden_viva_assumed_y_posicion_intacta(factory):
    class _TocaNivel(_Bars):
        async def get_bars(self, symbol, timeframe, limit=300):
            bars = await super().get_bars(symbol, timeframe, limit)
            bars[5]["low"] = 5491.0        # toca el límite 5492 a +25 min (viva)
            return bars
    await _seed(factory, rearm=_ON, estado=_estado())
    antes = await _pos(factory)
    foto = (antes.state, antes.direction, antes.quantity)
    r = await rearm_sweep(settings, MarketDataService(_TocaNivel()),
                          session_factory=factory, now=_NOW)
    assert r.get("assumed") == 1
    pos = await _pos(factory)
    leg = pos.risk_plan_json["rearm"]["legs"][0]
    assert leg["state"] == "assumed_filled"
    assert (pos.state, pos.direction, pos.quantity) == foto   # E2
    (a,) = await _uno(factory, AuditLog, action="REARM_ASSUMED")
    assert a.new_value_json["regla"] == "R-RA2"
    assert await _uno(factory, WebhookDelivery, strategy_id=_SID) == []
