"""Lote 2 — Config conectada de verdad (NX-10, NX-11, NX-12).

NX-10: `dedup_seconds` del perfil gobierna la ventana del Deduplicator (antes
       hardcode 60 s aunque la ficha guardara otro valor).
NX-11: guardar niveles/cantidades de Scale Entry en la UI o la API ya NO baja
       `mode=execute` a `design_only` (antes lo apagaba en silencio).
NX-12: `score_minimum` per-estrategia editable desde la UI (antes el form de
       alta lo descartaba y el Config tab no tenía campo).

Adversariales: fallan sin el fix correspondiente.
"""
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhooks_luxalgo import process_signal
from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.normalized_signal import NormalizedSignal
from app.models.raw_signal import RawSignal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services.config_resolver import ConfigResolver
from app.services.market_data_service import MarketDataService
from app.services.signal_normalizer import make_dedupe_key


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_lote2")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


async def _seed_strategy(db: AsyncSession, sid: str = "ES5m",
                         pipeline_config: dict | None = None) -> None:
    db.add(Strategy(strategy_id=sid, name=sid, asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    if pipeline_config is not None:
        db.add(StrategyProfile(strategy_id=sid, mode="paper",
                               pipeline_config_json=pipeline_config))
    await db.commit()


async def _profile_cfg(db: AsyncSession, sid: str = "ES5m") -> dict:
    db.expire_all()
    p = (await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == sid)
    )).scalar_one()
    return p.pipeline_config_json or {}


# ---------------------------------------------------------------------------
# NX-10 — dedup_seconds gobierna la ventana de dedupe (ADVERSARIAL)
# ---------------------------------------------------------------------------

class _MockMD:
    async def get_bars(self, *a, **kw):
        return []

    async def get_atr(self, *a, **kw):
        return 8.0

    async def is_active(self, symbol: str) -> bool:
        return True


_MD = MarketDataService(_MockMD())

_PAYLOAD = {"ticker": "MES", "action": "buy", "sentiment": "long",
            "quantity": "1", "price": "5500.00", "interval": "5"}


async def _fire(db: AsyncSession, sid: str):
    raw = RawSignal(source="luxalgo", strategy_id=sid, payload_json=_PAYLOAD,
                    token_valid=True)
    db.add(raw)
    await db.flush()
    decision = await process_signal(db, sid, raw.id, dict(_PAYLOAD), _MD)
    await db.flush()
    return decision


async def _age_first_signal(db: AsyncSession, sid: str, seconds: int) -> None:
    key = make_dedupe_key(sid, "MES", "buy", "long", "5500.00", "5")
    row = (await db.execute(
        select(NormalizedSignal).where(NormalizedSignal.dedupe_key == key)
    )).scalar_one()
    row.created_at = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    await db.flush()


@pytest.mark.asyncio
async def test_dedup_seconds_from_profile_extends_window(db: AsyncSession) -> None:
    """Perfil con dedup_seconds=300: una repetición a los 120 s ES duplicado.
    Sin el fix (ventana fija 60 s) salía procesada de nuevo."""
    await _seed_strategy(db, "dst", pipeline_config={"dedup_seconds": 300})
    await _fire(db, "dst")
    await _age_first_signal(db, "dst", seconds=120)

    decision = await _fire(db, "dst")

    assert decision.outcome == "IGNORE_DUPLICATE", (
        f"esperaba IGNORE_DUPLICATE (ventana 300s), salió {decision.outcome} "
        "(el bug NX-10: ventana hardcodeada en 60s)"
    )


@pytest.mark.asyncio
async def test_dedup_default_window_stays_60(db: AsyncSession) -> None:
    """Sin dedup_seconds en el perfil, la ventana sigue siendo 60 s: una
    repetición a los 120 s NO es duplicado (se procesa con rekey)."""
    await _seed_strategy(db, "dst60", pipeline_config={})
    await _fire(db, "dst60")
    await _age_first_signal(db, "dst60", seconds=120)

    decision = await _fire(db, "dst60")

    assert decision.outcome != "IGNORE_DUPLICATE"


@pytest.mark.asyncio
async def test_dedup_seconds_visible_in_effective_config(db: AsyncSession) -> None:
    await _seed_strategy(db, "dcfg", pipeline_config={"dedup_seconds": 300})
    cfg = await ConfigResolver().resolve(db, "dcfg", "MES")
    assert cfg["dedup_seconds"] == 300


# ---------------------------------------------------------------------------
# NX-11 — Scale Entry preserva mode=execute (ADVERSARIAL)
# ---------------------------------------------------------------------------

_EXEC_SE = {"mode": "execute", "levels": [0.75, 1.25], "quantities": [0, 1, 4],
            "max_micro_contracts": 5, "stop_mode": "common_position_stop"}


@pytest.mark.asyncio
async def test_ui_scale_entry_save_preserves_execute_mode(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Guardar niveles/cantidades desde la UI sobre una estrategia en
    mode=execute NO la baja a design_only."""
    await _seed_strategy(db, pipeline_config={"scale_entry": dict(_EXEC_SE)})

    r = await client.post("/ui/strategies/ES5m/scale-entry", data={
        "scale_entry_mode": "design_only",
        "levels": "1.0, 2.0", "quantities": "0, 2, 2",
        "max_micro_contracts": "4",
    })
    assert r.status_code in (200, 303), r.text

    se = (await _profile_cfg(db)).get("scale_entry")
    assert se["mode"] == "execute", (
        f"mode quedó {se['mode']!r} (el bug NX-11: la UI apagaba execute)"
    )
    assert se["levels"] == [1.0, 2.0]
    assert se["quantities"] == [0, 2, 2]
    assert se["max_micro_contracts"] == 4


@pytest.mark.asyncio
async def test_ui_scale_entry_new_design_defaults_design_only(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed_strategy(db, pipeline_config={})
    r = await client.post("/ui/strategies/ES5m/scale-entry", data={
        "scale_entry_mode": "design_only",
        "levels": "0.75", "quantities": "0, 1", "max_micro_contracts": "3",
    })
    assert r.status_code in (200, 303)
    se = (await _profile_cfg(db)).get("scale_entry")
    assert se["mode"] == "design_only"


@pytest.mark.asyncio
async def test_ui_scale_entry_off_still_removes(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed_strategy(db, pipeline_config={"scale_entry": dict(_EXEC_SE)})
    r = await client.post("/ui/strategies/ES5m/scale-entry",
                          data={"scale_entry_mode": "off"})
    assert r.status_code in (200, 303)
    assert "scale_entry" not in await _profile_cfg(db)


@pytest.mark.asyncio
async def test_api_scale_entry_patch_preserves_execute_mode(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed_strategy(db, pipeline_config={"scale_entry": dict(_EXEC_SE)})

    r = await client.patch("/api/strategies/ES5m/scale-entry", json={
        "mode": "design_only", "levels": [1.0], "quantities": [0, 2],
        "max_micro_contracts": 4,
    })
    assert r.status_code == 200, r.text
    assert r.json()["scale_entry"]["mode"] == "execute"

    se = (await _profile_cfg(db)).get("scale_entry")
    assert se["mode"] == "execute"
    assert se["levels"] == [1.0]


# ---------------------------------------------------------------------------
# NX-12 — score_minimum editable desde la UI (ADVERSARIAL)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_filters_form_saves_score_minimum(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed_strategy(db, pipeline_config={})
    r = await client.post("/ui/strategies/ES5m/filters", data={
        "f_volume_relative_enabled": "1", "f_volume_relative_weight": "25",
        "score_minimum": "55",
    })
    assert r.status_code in (200, 303), r.text

    cfg = await _profile_cfg(db)
    assert cfg.get("score_minimum") == 55, (
        f"score_minimum quedó {cfg.get('score_minimum')!r} "
        "(el bug NX-12: la UI no lo guardaba)"
    )
    effective = await ConfigResolver().resolve(db, "ES5m", "MES")
    assert effective["score_minimum"] == 55


@pytest.mark.asyncio
async def test_filters_form_empty_score_minimum_removes_override(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed_strategy(db, pipeline_config={"score_minimum": 55})
    r = await client.post("/ui/strategies/ES5m/filters", data={
        "f_volume_relative_enabled": "1", "f_volume_relative_weight": "25",
        "score_minimum": "",
    })
    assert r.status_code in (200, 303)
    assert "score_minimum" not in await _profile_cfg(db)


@pytest.mark.asyncio
async def test_filters_form_out_of_range_score_ignored(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Valores fuera de 1..100 se descartan: score_minimum=150 bloquearía TODO
    (el score máximo es 100)."""
    await _seed_strategy(db, pipeline_config={})
    r = await client.post("/ui/strategies/ES5m/filters", data={
        "f_volume_relative_enabled": "1", "f_volume_relative_weight": "25",
        "score_minimum": "150",
    })
    assert r.status_code in (200, 303)
    assert "score_minimum" not in await _profile_cfg(db)


@pytest.mark.asyncio
async def test_create_form_score_minimum_NO_persisted(
    client: AsyncClient, db: AsyncSession
) -> None:
    """FILTROS-OFF (2026-07-17) — el N4 se apagó y el campo score_minimum se RETIRÓ
    del alta (NX-12 revertido): aunque el form lo mande, JAMÁS nace en la config."""
    r = await client.post("/ui/strategies/new", data={
        "strategy_id": "NUEVA1", "name": "Nueva", "asset_symbol": "MES",
        "timeframe": "5m", "score_minimum": "60",
    })
    assert r.status_code in (200, 303), r.text
    cfg = await _profile_cfg(db, "NUEVA1")
    assert "score_minimum" not in cfg          # ninguna estrategia nace con la llave
