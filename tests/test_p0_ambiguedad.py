"""LOTE P0-AMBIGUEDAD — A-1/A-4 (despacho principal) + B-1 (RearmJob).

Auditoría final 2026-07-19, los dos P0:
  A-1 · el cliente reintentaba ENTRADAS tras un intento AMBIGUO (timeout: la
        orden pudo llegar) → posible doble orden a mercado. Ahora el intento
        ambiguo CORTA los reintentos de la entrada; EXITS conservan sus 10
        intentos (no cerrar es peor que cerrar dos veces).
  A-4 · entrada con FAILED totalmente ambiguo ⇒ UNKNOWN (no FLAT: la orden
        pudo quedar viva; L3 bloquea entradas hasta revisión), con AuditLog.
  B-1 · el RearmJob enviaba ANTES de persistir el ciclo: un crash/error de DB
        post-envío re-enviaba la misma pierna al minuto con la anterior viva.
        Ahora la INTENCIÓN se persiste y comitea antes del HTTP (intent-first,
        diseño §2); un intent sin desenlace al releer MATA la pierna
        (fail-closed), jamás re-envía.

Los tests A-1 usan excepciones httpx REALES (cierra también la mitad de E-3:
la clasificación del cliente deja de depender de flags puestos a mano).
"""
import uuid
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.models.audit_log import AuditLog
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.position_state import PositionState
from app.models.raw_signal import RawSignal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.models.symbol_map import SymbolMap
from app.models.webhook_delivery import WebhookDelivery
from app.services.market_data_service import MarketDataService
from app.services.traderspost_client import (
    TradersPostClient,
    WebhookDeliveryResult,
    failed_ambiguo,
)

UTC = timezone.utc
_URL = "https://app.traderspost.io/trading/webhook/abc?token=S"
_PAYLOAD = {"ticker": "MESU2025", "action": "buy"}


# ═══════════════════════════════════════════════════════════════════════════
# PARTE 1a — A-1: el cliente, con excepciones httpx REALES
# ═══════════════════════════════════════════════════════════════════════════

class _FakeResponse:
    def __init__(self, status_code: int, text: str = "ok") -> None:
        self.status_code = status_code
        self.text = text


class _FakeAsyncClient:
    """httpx.AsyncClient fake con guion de respuestas/excepciones; el último
    elemento se repite si el guion se agota."""
    _script: list = []
    calls: int = 0

    def __init__(self, *a, **kw) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, content=None, headers=None):
        idx = _FakeAsyncClient.calls
        _FakeAsyncClient.calls += 1
        item = _FakeAsyncClient._script[min(idx, len(_FakeAsyncClient._script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _fast(*a, **kw):
        return None
    monkeypatch.setattr("app.services.traderspost_client.asyncio.sleep", _fast)


@pytest.fixture
def _patch_httpx(monkeypatch):
    def _apply(script: list):
        _FakeAsyncClient._script = script
        _FakeAsyncClient.calls = 0
        monkeypatch.setattr(
            "app.services.traderspost_client.httpx.AsyncClient", _FakeAsyncClient
        )
    return _apply


def _client():
    from types import SimpleNamespace
    return TradersPostClient(SimpleNamespace(entry_signal_timeout_secs=30))


@pytest.mark.asyncio
async def test_a1_entrada_timeout_un_solo_intento_resultado_ambiguo(_patch_httpx):
    """La espec del lote: entrada con ReadTimeout ⇒ 1 SOLO intento (jamás se
    re-POSTea sobre una orden que pudo existir) y resultado marcado ambiguo."""
    _patch_httpx([httpx.ReadTimeout("10s")])
    r = await _client().send(_URL, _PAYLOAD, "entry_long", dry_run=False,
                             signal_ts=datetime.now(UTC))
    assert r.status == "FAILED"
    assert r.attempts == 1                       # cortó: ni un reintento
    assert _FakeAsyncClient.calls == 1           # y de verdad hubo UN solo POST
    assert r.any_ambiguous_attempt is True
    assert failed_ambiguo(r) is True


@pytest.mark.asyncio
async def test_a1_exit_timeout_conserva_los_10_intentos(_patch_httpx):
    """EXITS: el timeout NO corta — no cerrar es peor que cerrar dos veces
    (flatten idempotente). Los 10 intentos se conservan."""
    _patch_httpx([httpx.ReadTimeout("10s")])
    r = await _client().send(_URL, _PAYLOAD, "exit_long", dry_run=False)
    assert r.status == "FAILED"
    assert r.attempts == 10
    assert _FakeAsyncClient.calls == 10
    assert r.any_ambiguous_attempt is True       # honesto: hubo ambigüedad


@pytest.mark.asyncio
async def test_a1_entrada_corta_en_el_intento_ambiguo_no_antes(_patch_httpx):
    """Un 500 (inequívoco) reintenta; el ReadTimeout del intento 2 corta ahí:
    attempts == 2 y el resultado queda contaminado (el flag por-intento)."""
    _patch_httpx([_FakeResponse(500), httpx.ReadTimeout("10s"),
                  _FakeResponse(200)])
    r = await _client().send(_URL, _PAYLOAD, "entry_long", dry_run=False)
    assert r.status == "FAILED"                  # jamás llegó al 200 del guion
    assert r.attempts == 2
    assert _FakeAsyncClient.calls == 2
    assert r.any_ambiguous_attempt is True


@pytest.mark.asyncio
async def test_a1_entrada_connecterror_agota_reintentos_inequivoco(_patch_httpx):
    """ConnectError (el canal jamás se estableció): la orden seguro NO existe
    → la entrada agota sus 3 intentos y el FAILED es INEQUÍVOCO (⇒ FLAT)."""
    _patch_httpx([httpx.ConnectError("refused")])
    r = await _client().send(_URL, _PAYLOAD, "entry_long", dry_run=False)
    assert r.status == "FAILED"
    assert r.attempts == 3
    assert r.any_ambiguous_attempt is False
    assert failed_ambiguo(r) is False


def test_failed_ambiguo_clasificacion():
    def _res(**kw):
        base = dict(status="FAILED", payload_json={}, url_masked="x")
        base.update(kw)
        return WebhookDeliveryResult(**base)
    assert failed_ambiguo(_res(any_ambiguous_attempt=True)) is True
    assert failed_ambiguo(_res(response_status_code=500,
                               error_message="http_500")) is False
    assert failed_ambiguo(_res(error_message="ConnectError")) is False
    assert failed_ambiguo(_res(error_message="no_webhook_url_configured")) is False
    assert failed_ambiguo(_res(error_message="ReadTimeout")) is True


# ═══════════════════════════════════════════════════════════════════════════
# PARTE 1b — A-4: FAILED totalmente ambiguo ⇒ UNKNOWN (no FLAT)
# ═══════════════════════════════════════════════════════════════════════════

async def _preparar_despacho(db, *, sid="p0a4", action="buy"):
    """Cadena mínima persistida para llamar _dispatch_approved directo."""
    from app.services.filter_pipeline import PipelineResult
    raw = RawSignal(source="luxalgo", strategy_id=sid, ticker_received="MES",
                    action=action, sentiment="long", quantity_raw="1",
                    payload_json={}, token_valid=True)
    db.add(raw)
    await db.flush()
    norm = NormalizedSignal(
        raw_signal_id=raw.id, source="luxalgo", strategy_id=sid,
        ticker_received="MES", mapped_symbol="MESU2025", action=action,
        sentiment="long", quantity=1, price=5500.0,
        signal_ts=datetime.now(UTC), signal_role="entry_long",
        dedupe_key=uuid.uuid4().hex, status="processed")
    db.add(norm)
    await db.flush()
    decision = StrategyDecision(normalized_signal_id=norm.id, strategy_id=sid,
                                outcome="APPROVE")
    db.add(decision)
    await db.flush()
    result = PipelineResult(outcome="APPROVE", score=100, sl_price=5484.0,
                            tp_price=5520.0, atr_value=8.0,
                            market_data_provider="Mock")
    config = {"traderspost_webhook_url": "https://x/hook?token=t",
              "tick_size": 0.25}
    return norm, config, result, decision


def _send_fake(monkeypatch, result_factory):
    llamadas = {"n": 0}

    async def _send(self, url, payload, **kw):
        llamadas["n"] += 1
        return result_factory(payload)

    monkeypatch.setattr(TradersPostClient, "send", _send)
    return llamadas


async def _estado_pos(db, symbol="MESU2025"):
    res = await db.execute(
        select(PositionState).where(PositionState.symbol == symbol))
    return res.scalar_one_or_none()


@pytest.mark.asyncio
async def test_a4_entrada_failed_ambiguo_unknown_con_audit(db: AsyncSession,
                                                           monkeypatch):
    """FAILED totalmente ambiguo (timeout) ⇒ UNKNOWN, no FLAT — la orden pudo
    quedar viva; L3 bloquea entradas hasta revisión. AuditLog explícito."""
    from app.api.webhooks_luxalgo import _dispatch_approved
    norm, config, result, decision = await _preparar_despacho(db)
    _send_fake(monkeypatch, lambda p: WebhookDeliveryResult(
        status="FAILED", payload_json=p, url_masked="x",
        response_status_code=None, error_message="ReadTimeout", attempts=1,
        any_ambiguous_attempt=True))
    await _dispatch_approved(db, norm, None, config, result, decision)
    pos = await _estado_pos(db)
    assert pos is not None and pos.state == "UNKNOWN"
    assert pos.direction == "long" and pos.quantity == 1   # evidencia intacta
    logs = (await db.execute(select(AuditLog).where(
        AuditLog.action == "DELIVERY_FAILED"))).scalars().all()
    assert len(logs) == 1
    assert logs[0].new_value_json == {"state": "UNKNOWN",
                                      "cause": "entry_delivery_ambiguous"}


@pytest.mark.asyncio
async def test_a4_entrada_failed_inequivoco_sigue_yendo_a_flat(db: AsyncSession,
                                                               monkeypatch):
    """Contraste NX-08 conservado: todos los rechazos INEQUÍVOCOS (respuesta
    HTTP) ⇒ la orden seguro no existe ⇒ FLAT, como siempre."""
    from app.api.webhooks_luxalgo import _dispatch_approved
    norm, config, result, decision = await _preparar_despacho(db)
    _send_fake(monkeypatch, lambda p: WebhookDeliveryResult(
        status="FAILED", payload_json=p, url_masked="x",
        response_status_code=500, error_message="http_500", attempts=3,
        any_ambiguous_attempt=False))
    await _dispatch_approved(db, norm, None, config, result, decision)
    pos = await _estado_pos(db)
    assert pos is not None and pos.state == "FLAT"
    logs = (await db.execute(select(AuditLog).where(
        AuditLog.action == "DELIVERY_FAILED"))).scalars().all()
    assert len(logs) == 1
    assert logs[0].new_value_json["cause"] == "entry_delivery_failed"


@pytest.mark.asyncio
async def test_a4_on_entry_ambiguous_solo_desde_pending(db: AsyncSession):
    """El transicionador no pisa estados confirmados de otro flujo."""
    from app.services.position_service import PositionService
    svc = PositionService()
    db.add(PositionState(strategy_id="s", account_id="a", symbol="X",
                         state="LONG", state_source="estimated", quantity=2,
                         direction="long"))
    await db.flush()
    pos = await svc.on_entry_ambiguous(db, "s", "a", "X")
    assert pos.state == "LONG"                   # intacto: no era PENDING_*


# ═══════════════════════════════════════════════════════════════════════════
# PARTE 2 — B-1: intent-first en el RearmJob
# (fixtures gemelas de test_rearm_job_ra2b5 — mismo escenario ES)
# ═══════════════════════════════════════════════════════════════════════════

_OPEN_UTC = "2026-07-14T13:30:00+00:00"
_NOW = datetime(2026, 7, 14, 16, 0, tzinfo=UTC)          # 12:00 ET
_SID = "p0b1_es"
_SIGNAL = uuid.uuid4()


class _Bars:
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
        return 8.0

    async def is_active(self, symbol):
        return True


_MD = MarketDataService(_Bars())
_ON = {"enabled": True, "max_ciclos": 3}


def _cfg():
    return {"backstop_points": 12.0, "tp_nominal_long": 15.0,
            "entry_reserve_timeout_seconds": 3600,
            "scale_entry": {"mode": "execute", "quantities": [4, 3, 3],
                            "levels": [1.0, 2.0], "max_micro_contracts": 10,
                            "rearm": _ON}}


def _estado(enviando=None):
    leg = {"leg_index": 2, "side": "long", "level_atr": 1.0,
           "limit_price": 5492.1, "qty": 3, "cycle_n": 1,
           "last_client_id": None, "last_sent_at": _OPEN_UTC,
           "state": "working", "death_reason": None}
    if enviando is not None:
        leg["enviando"] = enviando
    return {"legs": [leg], "signal_atr": 8.0, "sl_price": 5488.0,
            "tp_price": 5620.0, "updated_at": _OPEN_UTC}


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


async def _seed(fac, estado):
    async with fac() as db:
        db.add(SymbolMap(tv_symbol="MES", mapped_symbol="MESU2025",
                         exchange="CME", contract_type="futures_micro",
                         tick_size="0.25", pine_script_config="x",
                         active=True))
        db.add(Strategy(strategy_id=_SID, name=_SID, asset_symbol="MES",
                        status="live", enabled=True))
        db.add(StrategyProfile(
            strategy_id=_SID,
            traderspost_webhook_url="https://webhooks.traderspost.io/x/t",
            pipeline_config_json=_cfg()))
        db.add(PositionState(strategy_id=_SID, account_id="paper_default",
                             symbol="MESU2025", state="LONG",
                             direction="long", quantity=10,
                             entry_price=5500.0, entry_signal_id=_SIGNAL,
                             risk_plan_json={"opened_at": _OPEN_UTC,
                                             "entry_style": "market",
                                             "rearm": estado}))
        await db.commit()


async def _rows(fac, model, **where):
    async with fac() as db:
        q = select(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return list((await db.execute(q)).scalars().all())


async def _leg(fac):
    (pos,) = await _rows(fac, PositionState, strategy_id=_SID)
    return pos.risk_plan_json["rearm"]["legs"][0]


@pytest.mark.asyncio
async def test_b1_intent_persistido_y_comiteado_antes_del_envio(factory,
                                                                monkeypatch):
    """En el MOMENTO del HTTP, la DB (sesión fresca) ya contiene el marcador
    'enviando' con ciclo/client_id/sent_at — el orden intent→envío del diseño
    §2, probado desde dentro del propio send. Tras resolver, el marcador se
    retira del estado persistido."""
    from app.services.rearm_job import rearm_sweep
    await _seed(factory, _estado())
    visto = {}

    async def _send(self, url, payload, **kw):
        async with factory() as db2:
            res = await db2.execute(select(PositionState).where(
                PositionState.strategy_id == _SID))
            leg = res.scalar_one().risk_plan_json["rearm"]["legs"][0]
            visto["enviando"] = leg.get("enviando")
        return WebhookDeliveryResult(status="SENT", payload_json=payload,
                                     url_masked="x", response_status_code=200,
                                     attempts=1)

    monkeypatch.setattr(TradersPostClient, "send", _send)
    r = await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert r.get("reenviado") == 1
    assert visto["enviando"] == {"cycle_n": 2,
                                 "client_id": f"{_SIGNAL}-r2",
                                 "sent_at": _NOW.isoformat()}
    leg = await _leg(factory)                    # desenlace resuelto y limpio
    assert leg["cycle_n"] == 2 and leg["state"] == "working"
    assert "enviando" not in leg


@pytest.mark.asyncio
async def test_b1_crash_post_envio_el_siguiente_barrido_no_reenvia(factory,
                                                                   monkeypatch):
    """La espec del lote: error de DB DESPUÉS del envío (la persistencia del
    desenlace revienta) ⇒ el intent queda huérfano en la DB y el siguiente
    barrido NO re-envía — mata la pierna fail-closed con audit. Sin el fix,
    el barrido 2 re-enviaba con la orden del barrido 1 viva 3600 s."""
    from app.services.position_service import PositionService
    from app.services.rearm_job import rearm_sweep
    await _seed(factory, _estado())
    llamadas = _send_fake(monkeypatch, lambda p: WebhookDeliveryResult(
        status="SENT", payload_json=p, url_masked="x",
        response_status_code=200, attempts=1))
    real = PositionService.set_rearm_state
    n_write = {"n": 0}

    async def _crash(self, db, *a, **kw):
        n_write["n"] += 1
        if n_write["n"] == 2:                    # 1=intent · 2=desenlace: crash
            raise RuntimeError("db reventada post-envío")
        return await real(self, db, *a, **kw)

    monkeypatch.setattr(PositionService, "set_rearm_state", _crash)
    r1 = await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert r1["errores"] == 1 and llamadas["n"] == 1
    leg = await _leg(factory)                    # el intent sobrevivió al crash
    assert leg["state"] == "working" and leg["cycle_n"] == 1
    assert leg["enviando"]["cycle_n"] == 2

    r2 = await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert llamadas["n"] == 1                    # JAMÁS re-envía
    assert r2.get("kill") == 1
    leg = await _leg(factory)
    assert leg["state"] == "dead"
    assert leg["death_reason"] == "intent_sin_desenlace"
    kills = await _rows(factory, AuditLog, action="REARM_KILL")
    assert len(kills) == 1
    assert kills[0].new_value_json["regla"] == "intent_sin_desenlace"
    assert kills[0].new_value_json["intent"]["client_id"] == f"{_SIGNAL}-r2"

    r3 = await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert llamadas["n"] == 1 and "kill" not in r3   # muerta: silencio


@pytest.mark.asyncio
async def test_b1_intent_huerfano_sembrado_fail_closed_auditado(factory,
                                                                monkeypatch):
    """Estado releído con 'enviando' huérfano (restart a mitad de envío, el
    zombie 'sent-uncommitted' de la auditoría §B.2): cero HTTP, pierna muerta,
    audit con el intent como evidencia forense."""
    from app.services.rearm_job import rearm_sweep
    intent = {"cycle_n": 2, "client_id": f"{_SIGNAL}-r2",
              "sent_at": _OPEN_UTC}
    await _seed(factory, _estado(enviando=intent))
    llamadas = _send_fake(monkeypatch, lambda p: WebhookDeliveryResult(
        status="SENT", payload_json=p, url_masked="x",
        response_status_code=200, attempts=1))
    r = await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert r.get("kill") == 1
    assert llamadas["n"] == 0                    # fail-closed: ni un POST
    assert await _rows(factory, WebhookDelivery, strategy_id=_SID) == []
    leg = await _leg(factory)
    assert leg["state"] == "dead"
    assert leg["death_reason"] == "intent_sin_desenlace"
    assert leg["enviando"] == intent             # evidencia forense intacta
    (k,) = await _rows(factory, AuditLog, action="REARM_KILL")
    assert k.new_value_json["intent"] == intent
    assert "fail-closed" in k.new_value_json["detalle"]


@pytest.mark.asyncio
async def test_b1_fallido_inequivoco_limpia_intent_y_reintenta(factory,
                                                               monkeypatch):
    """El desenlace 'fallido' (todos FAILED inequívocos) RESUELVE el intent:
    el marcador limpio se persiste (si no, el barrido siguiente lo mataría
    como huérfano) y el reintento legítimo del siguiente barrido sigue vivo."""
    from app.services.rearm_job import rearm_sweep
    await _seed(factory, _estado())
    llamadas = _send_fake(monkeypatch, lambda p: WebhookDeliveryResult(
        status="FAILED", payload_json=p, url_masked="x",
        response_status_code=500, error_message="http_500", attempts=3,
        any_ambiguous_attempt=False))
    r1 = await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert r1.get("skip:envio_fallido") == 1
    leg = await _leg(factory)
    assert leg["state"] == "working" and leg["cycle_n"] == 1
    assert "enviando" not in leg                 # intent RESUELTO, no huérfano
    r2 = await rearm_sweep(settings, _MD, session_factory=factory, now=_NOW)
    assert llamadas["n"] == 2                    # reintento legítimo VIVO
    assert r2.get("skip:envio_fallido") == 1
    leg = await _leg(factory)
    assert leg["state"] == "working"             # jamás muerta por su propio intent
