"""RA-2b SUB-PASO 6 — E3b + adversariales E2E (el cierre del RearmJob).

E3b: caso MIXTO (un destino ok + intento AMBIGUO en el mismo re-envío) ⇒
tras registrar las deliveries, la pierna se MATA (envio_ambiguo_parcial) —
las órdenes enviadas viven hasta su cancelAfter; se prohíbe el FUTURO
re-envío. El inequívoco NO contamina.

Adversariales sobre el pipeline real con fakes: (a) restart a mitad de ciclo
⇒ continúa en su ciclo, jamás "ciclo 1 otra vez"; (b) post-exit no revive;
(c) kill-switch por capas ⇒ jamás un envío real; (d) enabled apagado entre
ciclos; (e) UNKNOWN por exit fallido ⇒ R-RA5; (f) feed muerto a mitad de
vida ⇒ skip sin matar y al revivir continúa; (g) TTL editado por fuera tras
la siembra ⇒ ttl_incoherente.

RECONSTRUCCIÓN: una vida completa se re-arma SOLO desde AuditLog +
WebhookDeliveries y coincide con el estado final — la demo audita el
re-armado sin leer risk_plan_json.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.audit_log import AuditLog
from app.models.strategy_profile import StrategyProfile
from app.models.webhook_delivery import WebhookDelivery
from app.services.market_data_service import MarketDataService
from app.services.position_service import PositionService
from app.services.rearm_job import rearm_sweep
from tests.test_rearm_job_ra2b5 import (  # harness del sub-paso 5
    _NOW,
    _ON,
    _SID,
    _SIGNAL,
    _cfg,
    _estado,
    _pos,
    _seed,
    _send_fake,
    _uno,
    factory,  # noqa: F401 — fixture re-exportada
)

_NOW2 = datetime(2026, 7, 14, 17, 5, tzinfo=timezone.utc)   # 13:05 ET (>13:02)


class _BarsGuion:
    """Barras 5m ET 09:35→fin con dips puntuales {dt_et: low} (guion)."""

    def __init__(self, fin_et: datetime, dips: dict | None = None, atr=8.0):
        self._fin, self._dips, self._atr = fin_et, dips or {}, atr

    async def get_bars(self, symbol, timeframe, limit=300):
        t = datetime(2026, 7, 14, 9, 35)
        out = []
        while t <= self._fin:
            lo = self._dips.get(t, 5496.0)
            out.append({"time": t.strftime("%Y-%m-%dT%H:%M:%S"),
                        "open": 5500.0, "high": 5504.0, "low": lo,
                        "close": 5500.0, "volume": 10})
            t += timedelta(minutes=5)
        return out

    async def get_atr(self, symbol, timeframe, period=14):
        return self._atr

    async def is_active(self, symbol):
        return True


def _md(fin_et: datetime, dips: dict | None = None) -> MarketDataService:
    return MarketDataService(_BarsGuion(fin_et, dips))


_MD_1200 = _md(datetime(2026, 7, 14, 12, 0))
_MD_1305 = _md(datetime(2026, 7, 14, 13, 5))


async def _update_cfg(fac, sid, cfg):
    async with fac() as db:
        (prof,) = list((await db.execute(select(StrategyProfile).where(
            StrategyProfile.strategy_id == sid))).scalars().all())
        prof.pipeline_config_json = cfg
        await db.commit()


# ═══════════════════════════════════════════════════════════════════════════
# 1) E3b — caso MIXTO
# ═══════════════════════════════════════════════════════════════════════════

def _cfg_dos_destinos(rearm=_ON):
    cfg = _cfg(rearm)
    cfg["profiles"] = [{"name": "fa", "enabled": True,
                        "webhook_url": "https://webhooks.traderspost.io/x/fa",
                        "quantities": [1, 1, 1]}]
    return cfg


def _send_secuencia(monkeypatch, resultados):
    """Fake de send que responde en secuencia (destino base, perfil, ...)."""
    from app.services.traderspost_client import TradersPostClient
    llamadas = {"n": 0}

    async def _send(self, url, payload, **kw):
        r = resultados[min(llamadas["n"], len(resultados) - 1)](payload)
        llamadas["n"] += 1
        return r

    monkeypatch.setattr(TradersPostClient, "send", _send)
    return llamadas


def _res(status, code=None, err=None, ambiguo=False):
    from app.services.traderspost_client import WebhookDeliveryResult
    return lambda p: WebhookDeliveryResult(
        status=status, payload_json=p, url_masked="x",
        response_status_code=code, error_message=err, attempts=1,
        any_ambiguous_attempt=ambiguo)


@pytest.mark.asyncio
async def test_e3b_mixto_sent_mas_ambiguo_mata_y_no_reenvia(factory,
                                                            monkeypatch):
    await _seed(factory, rearm=None, estado=_estado())
    await _update_cfg(factory, _SID, _cfg_dos_destinos())
    llamadas = _send_secuencia(monkeypatch, [
        _res("SENT", code=200),                              # base: entró
        _res("FAILED", err="ReadTimeout", ambiguo=True)])    # perfil: ambiguo
    r = await rearm_sweep(settings, _MD_1200, session_factory=factory,
                          now=_NOW)
    assert r.get("kill") == 1
    pos = await _pos(factory)
    leg = pos.risk_plan_json["rearm"]["legs"][0]
    assert leg["state"] == "dead"
    assert leg["death_reason"] == "envio_ambiguo_parcial"
    # las deliveries del ciclo QUEDARON registradas (la orden base vive)
    assert len(await _uno(factory, WebhookDelivery, strategy_id=_SID)) == 2
    (k,) = await _uno(factory, AuditLog, action="REARM_KILL")
    assert k.new_value_json["regla"] == "envio_ambiguo_parcial"
    assert "orden fantasma" in k.new_value_json["detalle"]
    n1 = llamadas["n"]
    await rearm_sweep(settings, _MD_1305, session_factory=factory, now=_NOW2)
    assert llamadas["n"] == n1                    # muerta: el futuro NO existe


@pytest.mark.asyncio
async def test_e3b_mixto_sent_mas_fallido_inequivoco_avanza(factory,
                                                            monkeypatch):
    await _seed(factory, rearm=None, estado=_estado())
    await _update_cfg(factory, _SID, _cfg_dos_destinos())
    _send_secuencia(monkeypatch, [
        _res("SENT", code=200),                              # base: entró
        _res("FAILED", code=500, err="http_500")])           # perfil: 500 claro
    r = await rearm_sweep(settings, _MD_1200, session_factory=factory,
                          now=_NOW)
    assert r.get("reenviado") == 1                # el inequívoco NO contamina
    pos = await _pos(factory)
    leg = pos.risk_plan_json["rearm"]["legs"][0]
    assert leg["state"] == "working" and leg["cycle_n"] == 2
    (a,) = await _uno(factory, AuditLog, action="REARM_LEG")
    assert a.new_value_json["ciclo"] == 2


@pytest.mark.asyncio
async def test_e3b_sent_tras_intento_ambiguo_mismo_destino_tambien_mata(
        factory, monkeypatch):
    """Extensión coherente de E3b: un SENT que necesitó un intento ambiguo
    ANTES (timeout en intento 1, entró en el 2) puede coexistir con la orden
    fantasma del intento 1 EN EL MISMO destino ⇒ igual de mixto ⇒ MATAR."""
    await _seed(factory, rearm=_ON, estado=_estado())
    _send_fake(monkeypatch, _res("SENT", code=200, ambiguo=True))
    r = await rearm_sweep(settings, _MD_1200, session_factory=factory,
                          now=_NOW)
    assert r.get("kill") == 1
    pos = await _pos(factory)
    assert pos.risk_plan_json["rearm"]["legs"][0]["death_reason"] == \
        "envio_ambiguo_parcial"


# ═══════════════════════════════════════════════════════════════════════════
# 2) Adversariales E2E
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_a_restart_a_mitad_de_ciclo_continua_jamas_ciclo_1(factory):
    """Diseño §2 (candado anti-doble): el 'restart' ES releer de DB — el
    barrido siguiente (sesiones nuevas, job nuevo) continúa en el ciclo
    persistido, jamás re-arranca en 1."""
    await _seed(factory, rearm=_ON, estado=_estado())
    await rearm_sweep(settings, _MD_1200, session_factory=factory, now=_NOW)
    pos = await _pos(factory)
    assert pos.risk_plan_json["rearm"]["legs"][0]["cycle_n"] == 2
    # ── RESTART: proceso nuevo = solo queda la DB; el sweep relee ──
    await rearm_sweep(settings, _MD_1305, session_factory=factory, now=_NOW2)
    pos = await _pos(factory)
    leg = pos.risk_plan_json["rearm"]["legs"][0]
    assert leg["cycle_n"] == 3                    # continúa — JAMÁS "1 otra vez"
    assert leg["last_client_id"] == f"{_SIGNAL}-r3"
    ds = await _uno(factory, WebhookDelivery, strategy_id=_SID)
    assert sorted(d.payload_json["extras"]["rearm_cycle"] for d in ds) == [2, 3]


@pytest.mark.asyncio
async def test_b_post_exit_no_revive(factory):
    """Tras el exit (cancel:true ya canceló las límite), la posición queda
    FLAT ⇒ el barrido MATA los re-armados (R-RA5) y no despacha nada."""
    await _seed(factory, rearm=_ON, estado=_estado())
    ps = PositionService()
    async with factory() as db:
        await ps.on_exit_approved(db, _SID, "paper_default", "MESU2025")
        await ps.on_delivery_confirmed(db, _SID, "paper_default", "MESU2025")
        await db.commit()
    r = await rearm_sweep(settings, _MD_1200, session_factory=factory,
                          now=_NOW)
    assert r.get("kill") == 1
    pos = await _pos(factory)
    assert pos.state == "FLAT"                    # el job no la tocó
    assert pos.risk_plan_json["rearm"]["legs"][0]["death_reason"] == "R-RA5"
    assert await _uno(factory, WebhookDelivery, strategy_id=_SID) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("capa", ["env_enabled", "env_dry", "cfg_enabled",
                                  "cfg_dry"])
async def test_c_kill_switch_por_capas_jamas_envio_real(factory, monkeypatch,
                                                        capa):
    """Cada capa CERRADA (las otras tres abiertas) ⇒ el gate resuelve dry_run
    True y el envío jamás es real — espía sobre client.send."""
    from app.services.traderspost_client import TradersPostClient
    cfg = _cfg(_ON)
    cfg["traderspost_enabled"] = capa != "cfg_enabled"
    cfg["dry_run"] = capa == "cfg_dry"
    await _seed(factory, rearm=None, estado=_estado())
    await _update_cfg(factory, _SID, cfg)
    monkeypatch.setattr(settings, "TRADERSPOST_ENABLED",
                        capa != "env_enabled", raising=False)
    monkeypatch.setattr(settings, "DRY_RUN", capa == "env_dry", raising=False)
    visto = {}

    async def _spy(self, url, payload, **kw):
        visto["dry_run"] = kw.get("dry_run")
        from app.services.traderspost_client import WebhookDeliveryResult
        return WebhookDeliveryResult(status="DRY_RUN", payload_json=payload,
                                     url_masked="x")

    monkeypatch.setattr(TradersPostClient, "send", _spy)
    await rearm_sweep(settings, _MD_1200, session_factory=factory, now=_NOW)
    assert visto["dry_run"] is True               # la capa cerrada corta
    ds = await _uno(factory, WebhookDelivery, strategy_id=_SID)
    assert all(d.status == "DRY_RUN" and d.sent_at is None for d in ds)


@pytest.mark.asyncio
async def test_d_enabled_apagado_entre_ciclos(factory):
    await _seed(factory, rearm=_ON, estado=_estado())
    await rearm_sweep(settings, _MD_1200, session_factory=factory, now=_NOW)
    pos = await _pos(factory)
    assert pos.risk_plan_json["rearm"]["legs"][0]["cycle_n"] == 2
    await _update_cfg(factory, _SID,
                      _cfg({"enabled": False, "max_ciclos": 3}))
    r = await rearm_sweep(settings, _MD_1305, session_factory=factory,
                          now=_NOW2)
    assert r.get("skip:disabled") == 1
    ds = await _uno(factory, WebhookDelivery, strategy_id=_SID)
    assert len(ds) == 1                           # solo el ciclo 2; nada nuevo


@pytest.mark.asyncio
async def test_e_unknown_por_exit_fallido_rra5(factory):
    await _seed(factory, rearm=_ON, estado=_estado())
    ps = PositionService()
    async with factory() as db:
        await ps.on_exit_approved(db, _SID, "paper_default", "MESU2025")
        await ps.on_exit_failed(db, _SID, "paper_default", "MESU2025")
        await db.commit()
    r = await rearm_sweep(settings, _MD_1200, session_factory=factory,
                          now=_NOW)
    assert r.get("kill") == 1
    pos = await _pos(factory)
    assert pos.state == "UNKNOWN"
    assert pos.risk_plan_json["rearm"]["legs"][0]["death_reason"] == "R-RA5"


@pytest.mark.asyncio
async def test_f_feed_muerto_skip_sin_matar_y_al_revivir_continua(factory):
    class _Muerto:
        async def get_bars(self, *a, **k): return []
        async def get_atr(self, *a, **k): return None
        async def is_active(self, *a, **k): return False
    await _seed(factory, rearm=_ON, estado=_estado())
    r1 = await rearm_sweep(settings, MarketDataService(_Muerto()),
                           session_factory=factory, now=_NOW)
    assert r1.get("skip:R-RA1") == 1
    pos = await _pos(factory)
    leg = pos.risk_plan_json["rearm"]["legs"][0]
    assert leg["state"] == "working" and leg["cycle_n"] == 1   # sin matar
    # el feed revive → el MISMO barrido siguiente continúa y re-envía
    r2 = await rearm_sweep(settings, _MD_1200, session_factory=factory,
                           now=_NOW)
    assert r2.get("reenviado") == 1
    pos = await _pos(factory)
    assert pos.risk_plan_json["rearm"]["legs"][0]["cycle_n"] == 2


@pytest.mark.asyncio
async def test_g_ttl_editado_por_fuera_tras_la_siembra(factory):
    await _seed(factory, rearm=_ON, ttl=3600, estado=_estado())
    await _update_cfg(factory, _SID, _cfg(_ON, ttl=1800))   # editado por fuera
    r = await rearm_sweep(settings, _MD_1200, session_factory=factory,
                          now=_NOW)
    assert r.get("skip:ttl_incoherente") == 1
    pos = await _pos(factory)
    assert pos.risk_plan_json["rearm"]["legs"][0]["state"] == "working"
    assert await _uno(factory, WebhookDelivery, strategy_id=_SID) == []
    (s,) = await _uno(factory, AuditLog, action="REARM_SKIP")
    assert s.new_value_json["motivo"] == "ttl_incoherente"


# ═══════════════════════════════════════════════════════════════════════════
# 3) RECONSTRUCCIÓN — la demo audita sin leer risk_plan_json
# ═══════════════════════════════════════════════════════════════════════════

def _estado_dos_piernas():
    e = _estado()
    e["legs"].append(dict(e["legs"][0], leg_index=3, level_atr=2.0,
                          limit_price=5484.0))
    return e


@pytest.mark.asyncio
async def test_reconstruccion_desde_auditlog_y_deliveries(factory):
    """Vida completa: siembra (C2/C3) → re-envío de ambas (ciclo 2) → C2
    ASSUMED en ciclo 2 (toque en ventana viva) → C3 muere por R-RA6 (stop
    tocado). La historia reconstruida SOLO desde AuditLog + WebhookDeliveries
    coincide llave por llave con el estado final."""
    await _seed(factory, rearm=_ON, estado=_estado_dos_piernas())
    # S1 12:00 — ambas piernas re-envían (ciclo 2)
    await rearm_sweep(settings, _MD_1200, session_factory=factory, now=_NOW)
    # S2 12:30 — dip a 5491.5 (12:20, ventana VIVA del ciclo 2) toca C2
    await rearm_sweep(
        settings, _md(datetime(2026, 7, 14, 12, 30),
                      {datetime(2026, 7, 14, 12, 20): 5491.5}),
        session_factory=factory,
        now=datetime(2026, 7, 14, 16, 30, tzinfo=timezone.utc))
    # S3 12:45 — crash a 5487 (12:40) cruza el backstop 5488 → C3 huérfana
    await rearm_sweep(
        settings, _md(datetime(2026, 7, 14, 12, 45),
                      {datetime(2026, 7, 14, 12, 20): 5491.5,
                       datetime(2026, 7, 14, 12, 40): 5487.0}),
        session_factory=factory,
        now=datetime(2026, 7, 14, 16, 45, tzinfo=timezone.utc))

    # ── reconstrucción SOLO desde deliveries + audit ──
    rec: dict[int, dict] = {}
    for d in await _uno(factory, WebhookDelivery, strategy_id=_SID):
        ex = d.payload_json["extras"]
        li = ex["leg_index"]
        rec.setdefault(li, {"cycle_n": 1, "state": "working",
                            "death_reason": None})
        rec[li]["cycle_n"] = max(rec[li]["cycle_n"], ex["rearm_cycle"])
    for a in await _uno(factory, AuditLog, action="REARM_ASSUMED"):
        rec[a.new_value_json["leg_index"]].update(
            state="assumed_filled", death_reason=None)
    for a in await _uno(factory, AuditLog, action="REARM_KILL"):
        rec[a.new_value_json["leg_index"]].update(
            state="dead", death_reason=a.new_value_json["regla"])

    pos = await _pos(factory)
    final = {l["leg_index"]: {"cycle_n": l["cycle_n"], "state": l["state"],
                              "death_reason": l["death_reason"]}
             for l in pos.risk_plan_json["rearm"]["legs"]}
    assert rec == final                            # bit a bit, sin leer el JSON
    assert final[2] == {"cycle_n": 2, "state": "assumed_filled",
                        "death_reason": None}
    assert final[3] == {"cycle_n": 2, "state": "dead",
                        "death_reason": "R-RA6"}
