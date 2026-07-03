"""Lote 7 — observabilidad/metadata (NX-17, NX-20, NX-24). No toca dispatch.

NX-17: cancel_after por estrategia = p90 del tiempo al pullback + colchón
       (tope 3600), guardado como `entry_reserve_timeout_seconds` en
       pipeline_config_json — la MISMA clave que gobierna la liberación de la
       reserva (NX-28): una sola fecha de caducidad para pierna límite,
       reserva de símbolo y cancel_after de TradersPost. Editable en la ficha.
NX-20: clonar copia pipeline_config_json SANEADO (sin `profiles`, scale_entry
       degradado a design_only) y el clon nace desarmado (dry_run, candidate).
NX-24: los renames no parten las series: Analytics agrupa por id canónico
       (mapa de alias desde los AuditLog de rename_strategy) y marca retiradas.

Adversariales: fallan sin el fix.
"""
import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.audit_log import AuditLog
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.raw_signal import RawSignal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services.config_resolver import ConfigResolver
from scripts.pullback_timing import apply_suggestion, suggest_cancel_after

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_lote7")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


# ---------------------------------------------------------------------------
# NX-17 — sugerencia + aplicación + ficha (ADVERSARIAL en ficha/apply)
# ---------------------------------------------------------------------------

def test_suggest_cancel_after_formula():
    # p90 de [10]*10 = 10 min → 600 s + 60 de colchón = 660
    assert suggest_cancel_after([10.0] * 10) == 660
    # tope 3600
    assert suggest_cancel_after([100.0] * 5) == 3600
    # sin datos → None
    assert suggest_cancel_after([]) is None
    # colchón configurable
    assert suggest_cancel_after([10.0] * 10, cushion_seconds=120) == 720


async def _seed_strategy(db: AsyncSession, sid="ca", pipeline_config=None):
    db.add(Strategy(strategy_id=sid, name=sid, asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id=sid, mode="paper",
                           pipeline_config_json=pipeline_config))
    await db.commit()


async def _profile_cfg(db: AsyncSession, sid: str) -> dict:
    db.expire_all()
    p = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == sid))).scalar_one()
    return p.pipeline_config_json or {}


@pytest.mark.asyncio
async def test_apply_suggestion_writes_reserve_timeout(db: AsyncSession):
    await _seed_strategy(db, "ca", pipeline_config={"windows": []})
    old = await apply_suggestion(db, "ca", 660)
    await db.commit()

    cfg = await _profile_cfg(db, "ca")
    assert cfg.get("entry_reserve_timeout_seconds") == 660
    assert cfg.get("windows") == []          # merge, no reemplazo
    assert old is None                        # no había valor previo
    audit = (await db.execute(select(AuditLog).where(
        AuditLog.actor == "pullback_timing"))).scalars().first()
    assert audit is not None
    # y es la MISMA clave que consume la liberación de reserva (NX-28)
    eff = await ConfigResolver().resolve(db, "ca", "MES")
    assert eff["entry_reserve_timeout_seconds"] == 660


@pytest.mark.asyncio
async def test_ficha_saves_cancel_after(client: AsyncClient, db: AsyncSession):
    await _seed_strategy(db, "cf")
    r = await client.post("/ui/strategies/cf/ficha",
                          data={"entry_reserve_timeout_seconds": "900"})
    assert r.status_code in (200, 303)
    cfg = await _profile_cfg(db, "cf")
    assert cfg.get("entry_reserve_timeout_seconds") == 900, (
        "la ficha no guardó cancel_after/reserva (bug NX-17)")
    eff = await ConfigResolver().resolve(db, "cf", "MES")
    assert eff["entry_reserve_timeout_seconds"] == 900


@pytest.mark.asyncio
async def test_ficha_empty_cancel_after_removes_override(
    client: AsyncClient, db: AsyncSession
):
    await _seed_strategy(db, "ce", pipeline_config={
        "entry_reserve_timeout_seconds": 900})
    r = await client.post("/ui/strategies/ce/ficha",
                          data={"entry_reserve_timeout_seconds": ""})
    assert r.status_code in (200, 303)
    assert "entry_reserve_timeout_seconds" not in await _profile_cfg(db, "ce")


# ---------------------------------------------------------------------------
# NX-20 — clone copia config saneada (ADVERSARIAL)
# ---------------------------------------------------------------------------

_FULL_CFG = {
    "windows": [{"days": [1, 2, 3, 4, 5], "start": "09:30", "end": "15:45"}],
    "filters": {"volume_relative": {"enabled": True, "weight": 25}},
    "regime": {"enabled": True, "timeframe": "1h",
               "allowed_regimes": ["ranging"]},
    "guardrails": {"enforce_symbol_match": True,
                   "signal_max_age_entry_seconds": 120},
    "score_minimum": 55,
    "scale_entry": {"mode": "execute", "levels": [0.75],
                    "quantities": [0, 2], "max_micro_contracts": 3},
    "profiles": [{"name": "apex", "enabled": True,
                  "webhook_url": "https://tp/apex"}],
}


@pytest.mark.asyncio
async def test_clone_copies_sanitized_config(client: AsyncClient, db: AsyncSession):
    db.add(Strategy(strategy_id="src", name="SRC", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id="src", mode="paper",
                           sl_atr_multiplier=2.5, tp_atr_multiplier=6.0,
                           traderspost_webhook_url="https://tp/src",
                           dry_run=False, traderspost_enabled=True,
                           pipeline_config_json=dict(_FULL_CFG)))
    await db.commit()

    r = await client.post("/ui/strategies/src/clone",
                          data={"new_strategy_id": "clon1"})
    assert r.status_code in (200, 303), r.text

    db.expire_all()
    strat = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == "clon1"))).scalar_one()
    prof = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == "clon1"))).scalar_one()
    cfg = prof.pipeline_config_json or {}

    # copia la calibración del pipeline (antes nacía desnudo)
    assert cfg.get("windows") == _FULL_CFG["windows"], (
        "el clon nació sin pipeline_config_json (bug NX-20)")
    assert cfg.get("filters") == _FULL_CFG["filters"]
    assert cfg.get("regime") == _FULL_CFG["regime"]
    assert cfg.get("guardrails") == _FULL_CFG["guardrails"]
    assert cfg.get("score_minimum") == 55
    # saneado: sin webhooks de cuentas y sin ejecución escalonada armada
    assert "profiles" not in cfg
    assert cfg["scale_entry"]["mode"] == "design_only"
    assert cfg["scale_entry"]["levels"] == [0.75]
    # el clon nace desarmado
    assert prof.dry_run is True
    assert prof.traderspost_enabled is False
    assert strat.status == "candidate"
    assert strat.webhook_token_hash       # token propio (hasheado, NX-22)


# ---------------------------------------------------------------------------
# NX-24 — alias de strategy_id legacy en Analytics (ADVERSARIAL)
# ---------------------------------------------------------------------------

async def _decision(db: AsyncSession, sid: str) -> None:
    raw = RawSignal(source="luxalgo", strategy_id=sid, payload_json={},
                    token_valid=True)
    db.add(raw)
    await db.flush()
    norm = NormalizedSignal(
        raw_signal_id=raw.id, strategy_id=sid, ticker_received="MES",
        action="buy", sentiment="long", signal_ts=datetime.now(UTC),
        dedupe_key=uuid.uuid4().hex,
    )
    db.add(norm)
    await db.flush()
    db.add(StrategyDecision(normalized_signal_id=norm.id, strategy_id=sid,
                            outcome="APPROVE", score=100))


def _rename_audit(old: str, new: str) -> AuditLog:
    """La huella que deja scripts/rename_strategy.py al aplicar."""
    return AuditLog(actor="rename_strategy", action="UPDATE",
                    object_type="Strategy", object_id=new,
                    old_value_json={"renamed_from": old, "old_deleted": False},
                    new_value_json={}, reason="normalize strategy_id")


@pytest.mark.asyncio
async def test_alias_map_resolves_chains(db: AsyncSession):
    from app.services.strategy_aliases import get_alias_map

    db.add(_rename_audit("vieja_a", "media_b"))
    db.add(_rename_audit("media_b", "nueva_c"))
    await db.commit()

    amap = await get_alias_map(db)
    assert amap.get("vieja_a") == "nueva_c"
    assert amap.get("media_b") == "nueva_c"


@pytest.mark.asyncio
async def test_analytics_merges_renamed_series(client: AsyncClient, db: AsyncSession):
    """Decisiones bajo el id viejo y el nuevo → UNA serie con el id canónico."""
    db.add(Strategy(strategy_id="nueva_x", name="N", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    await _decision(db, "vieja_x")
    await _decision(db, "nueva_x")
    db.add(_rename_audit("vieja_x", "nueva_x"))
    await db.commit()

    r = await client.get("/ui/analytics")
    assert r.status_code == 200
    assert "nueva_x" in r.text
    assert "vieja_x" not in r.text, (
        "el id viejo sigue partiendo la serie en Analytics (bug NX-24)")


@pytest.mark.asyncio
async def test_analytics_marks_retired(client: AsyncClient, db: AsyncSession):
    db.add(Strategy(strategy_id="jubilada", name="J", asset_symbol="MES",
                    timeframe="5m", status="retired", enabled=False))
    await _decision(db, "jubilada")
    await db.commit()

    r = await client.get("/ui/analytics")
    assert r.status_code == 200
    assert "jubilada" in r.text
    assert "retirada" in r.text
