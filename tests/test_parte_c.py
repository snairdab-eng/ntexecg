"""PARTE C — limpieza del Config (UI-only) + guardarraíles SIEMPRE-ON.

Candados:
  1. Los SERVICIOS de filtros/régimen (quality_scorer, hmm_service) siguen
     importables y VIVOS — el Lab los reusa (banner informativo presente).
  2. La ficha (Config) YA NO expone filtros / régimen / EST-1/EST-2 ni los
     toggles enforce_*; el guardarraíl de ruteo queda visible como SIEMPRE-ON.
  3. Guardarraíl SIEMPRE-ON adversarial: un perfil viejo con enforce_*=False
     persistido en pipeline_config_json → el resolver lo IGNORA y fuerza True.
  4. SIN migración destructiva: las llaves JSON viejas (filters/regime/guardrails
     enforce_*) siguen persistiendo y round-trip — solo quedan huérfanas de UI.
"""
import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.normalized_signal import NormalizedSignal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services.config_resolver import ConfigResolver
from app.services.filter_pipeline import FilterPipeline

SID = "ES5m_ParteC"


def _pipe_signal(timeframe, ticker="MES") -> NormalizedSignal:
    return NormalizedSignal(
        raw_signal_id=uuid.uuid4(), strategy_id="pc_pipe",
        ticker_received=ticker, mapped_symbol="MESU2025",
        action="buy", sentiment="long", price=5500.0, timeframe=timeframe,
        signal_ts=datetime.now(timezone.utc), dedupe_key=uuid.uuid4().hex)


def _pipe_strategy() -> Strategy:
    return Strategy(strategy_id="pc_pipe", name="PCpipe",
                    asset_symbol="MES", status="live", enabled=True)


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_partec")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


async def _seed(db: AsyncSession, cfg: dict | None = None) -> None:
    db.add(Strategy(strategy_id=SID, name="PC", asset_symbol="ES",
                    status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id=SID, pipeline_config_json=cfg or {}))
    await db.commit()


# ---------------------------------------------------------------------------
# 1 — servicios conservados (el Lab los importa) + banner coherente
# ---------------------------------------------------------------------------

def test_servicios_filtros_regimen_siguen_importables() -> None:
    import app.services.quality_scorer as qs
    import app.services.hmm_service as hmm
    # símbolos que el Lab / diagnóstico reusan
    assert hasattr(qs, "active_filter_names")
    assert hasattr(qs, "score_signal") or hasattr(qs, "compute_score") \
        or callable(getattr(qs, "QualityScorer", None))
    assert hasattr(hmm, "classify_regime")


@pytest.mark.asyncio
async def test_lab_renderiza_con_banner(client: AsyncClient) -> None:
    r = await client.get("/ui/lab")
    assert r.status_code == 200
    html = r.text
    # el banner de L6 sigue y su texto es coherente con esta limpieza
    assert "dormidos en producción" in html
    assert "quality_scorer.py" in html and "hmm_service.py" in html
    assert "el Lab los importa" in html


# ---------------------------------------------------------------------------
# 2 — la ficha ya no expone filtros / régimen / EST / toggles enforce
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_config_sin_filtros_regimen_ni_toggles(
    client: AsyncClient, db: AsyncSession
) -> None:
    # perfil con la config vieja completa: la UI NO debe reflejar nada de esto
    await _seed(db, {
        "filters": {"volume_relative": {"enabled": True, "weight": 1.0}},
        "regime": {"enabled": True, "timeframe": "1h",
                   "allowed_regimes": ["trending_bull"]},
        "guardrails": {"enforce_symbol_match": True,
                       "enforce_timeframe_match": True},
    })
    html = (await client.get(f"/ui/strategies/{SID}")).text

    # secciones retiradas
    assert "Filtros de calidad — Nivel 4" not in html
    assert "Régimen de mercado — Nivel 4" not in html
    assert 'name="f_volume_relative_enabled"' not in html   # form de filtros
    assert 'name="regime_enabled"' not in html              # form de régimen
    assert 'name="enforce_symbol_match"' not in html        # toggle enforce
    assert 'name="enforce_timeframe_match"' not in html
    assert "Probar ahora" not in html                       # EST-1 UI

    # lo que SÍ queda: el guardarraíl visible como siempre-ON + staleness
    assert "Guardarraíles (Anexo 08)" in html
    assert "siempre activos" in html
    assert 'name="signal_max_age_entry_seconds"' in html    # staleness intacto


# ---------------------------------------------------------------------------
# 3 — guardarraíl SIEMPRE-ON: el valor persistido (False) se ignora
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enforce_persistido_false_se_ignora(db: AsyncSession) -> None:
    await _seed(db, {"guardrails": {"enforce_symbol_match": False,
                                    "enforce_timeframe_match": False}})
    cfg = await ConfigResolver().resolve(db, SID, "ES")
    # el toggle viejo apagado NO desactiva el chequeo: siempre-ON
    assert cfg["enforce_symbol_match"] is True
    assert cfg["enforce_timeframe_match"] is True
    # y el activo base se resolvió → el gate del pipeline es efectivo
    assert cfg["expected_symbol"] == "ES"


@pytest.mark.asyncio
async def test_enforce_siempre_on_sin_perfil(db: AsyncSession) -> None:
    # estrategia sin StrategyProfile: el guardarraíl también aplica
    db.add(Strategy(strategy_id="ES5m_NoProf", name="NP", asset_symbol="ES",
                    status="paper", enabled=True))
    await db.commit()
    cfg = await ConfigResolver().resolve(db, "ES5m_NoProf", "ES")
    assert cfg["enforce_symbol_match"] is True
    assert cfg["enforce_timeframe_match"] is True


# ---------------------------------------------------------------------------
# 4 — sin migración destructiva: las llaves viejas siguen persistiendo
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llaves_json_viejas_siguen_persistiendo(db: AsyncSession) -> None:
    """Documentado como huérfano: filters / regime / guardrails.enforce_* ya no
    tienen UI, pero la columna JSON las conserva (sin migración destructiva)."""
    legacy = {
        "filters": {"volume_relative": {"enabled": True, "weight": 2.0}},
        "regime": {"enabled": True, "timeframe": "4h",
                   "allowed_regimes": ["ranging"]},
        "guardrails": {"enforce_symbol_match": False,
                       "enforce_timeframe_match": False,
                       "signal_max_age_entry_seconds": 90},
    }
    await _seed(db, legacy)
    prof = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == SID))).scalar_one()
    assert prof.pipeline_config_json == legacy      # round-trip intacto
    # y la staleness (que SÍ conserva UI) sigue resolviéndose
    cfg = await ConfigResolver().resolve(db, SID, "ES")
    assert cfg["signal_max_age_entry_seconds"] == 90


# ---------------------------------------------------------------------------
# 5 — guardarraíl de timeframe SIEMPRE-ON, fail-honest (opción 3 del arquitecto)
# ---------------------------------------------------------------------------

_TF_CFG = {"mode": "normal", "enforce_symbol_match": True,
           "expected_symbol": "MES", "enforce_timeframe_match": True,
           "expected_timeframe": "5m"}


@pytest.mark.asyncio
async def test_tf_interval_explicito_distinto_bloquea(
    db: AsyncSession, market_data_service
) -> None:
    """(a) interval PRESENTE que no coincide → interval_mismatch BLOCK."""
    result = await FilterPipeline(market_data_service).evaluate(
        db, _pipe_signal("15m"), _pipe_strategy(), dict(_TF_CFG))
    assert result.outcome == "BLOCK"
    assert result.block_reason == "interval_mismatch"
    assert result.block_level == 1


@pytest.mark.asyncio
async def test_tf_sin_interval_pasa_con_tf_not_verified(
    db: AsyncSession, market_data_service
) -> None:
    """(b) señal SIN interval (timeframe None) → NO bloquea por interval, y el
    nivel 1 anota tf_not_verified (fail-honest, participación intacta)."""
    result = await FilterPipeline(market_data_service).evaluate(
        db, _pipe_signal(None), _pipe_strategy(), dict(_TF_CFG))
    assert result.block_reason != "interval_mismatch"
    l1 = (result.pipeline_execution_json or {}).get("level_1", {})
    assert l1.get("tf_not_verified") is True


@pytest.mark.asyncio
async def test_tf_match_no_marca_tf_not_verified(
    db: AsyncSession, market_data_service
) -> None:
    """interval presente y coincidente → chequeo completo, sin anotación."""
    result = await FilterPipeline(market_data_service).evaluate(
        db, _pipe_signal("5m"), _pipe_strategy(), dict(_TF_CFG))
    assert result.block_reason != "interval_mismatch"
    l1 = (result.pipeline_execution_json or {}).get("level_1", {})
    assert "tf_not_verified" not in l1


@pytest.mark.asyncio
async def test_symbol_siempre_on_adversarial_pipeline(
    db: AsyncSession, market_data_service
) -> None:
    """(c) symbol siempre-ON intacto: ticker ≠ activo base → symbol_mismatch."""
    result = await FilterPipeline(market_data_service).evaluate(
        db, _pipe_signal("5m", ticker="ES"), _pipe_strategy(), dict(_TF_CFG))
    assert result.outcome == "BLOCK"
    assert result.block_reason == "symbol_mismatch"
