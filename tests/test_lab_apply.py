"""Lote calibración — CLIs auditados del Laboratorio (cancel_after + régimen).

apply_cancel_after: estrategia → instrumento lab + pierna límite más profunda
ACTIVA (qty>0 en algún destino EFECTIVO — resolve_destinations vivo, no el
JSON crudo) → redondeo hacia arriba en la grilla del lab → cancel_after de
diseño (meta.pullback del cache del camino A). MISMO estimador, una sola
caducidad (NX-17/NX-28); escribe vía apply_suggestion (merge + audit).

apply_regime_gate: escribe pipeline_config_json["regime"] (la MISMA clave que
consume el L4 y edita la UI), merge no reemplazo, reversible con --disable.

Adversariales: fallan sin la implementación.
"""
from datetime import timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services.config_resolver import ConfigResolver
from app.services.dispatch_profiles import resolve_destinations
from scripts.apply_cancel_after import (
    deepest_active_level,
    grid_round_up,
    lab_instrument,
    plan_row,
)
from scripts.apply_regime_gate import (
    apply_gate,
    disabled_regime_cfg,
    merged_regime_cfg,
)
from scripts.pullback_timing import apply_suggestion

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Mapeo estrategia → instrumento del lab (micros reales del server)
# ---------------------------------------------------------------------------

def test_lab_instrument_micros():
    # MJY es el micro REAL de 6J en el server (no "M6J")
    assert lab_instrument("MJY") == "6J"
    assert lab_instrument("MES") == "ES"
    assert lab_instrument("m2k") == "RTY"          # case-insensitive
    assert lab_instrument("ES") == "ES"            # macro directo también
    assert lab_instrument("XXX") is None
    assert lab_instrument(None) is None


# ---------------------------------------------------------------------------
# Redondeo hacia arriba en la grilla del lab (PULLBACK_LEVELS)
# ---------------------------------------------------------------------------

def test_grid_round_up():
    assert grid_round_up(1.25) == (1.5, False)     # ES_ConfNormal real
    assert grid_round_up(0.75) == (0.75, False)    # exacto NO sube
    assert grid_round_up(5.0) == (5.0, False)      # NQ real, exacto
    assert grid_round_up(0.1) == (0.25, False)
    assert grid_round_up(6.0) == (6.0, False)      # B5.2: la grilla llega a 10×
    # más profundo que la grilla: clampa al máximo y lo MARCA
    assert grid_round_up(12.0) == (10.0, True)


# ---------------------------------------------------------------------------
# Pierna límite más profunda ACTIVA — sobre destinos EFECTIVOS
# ---------------------------------------------------------------------------

def _dests(cfg: dict) -> list[dict]:
    return resolve_destinations(cfg)


def test_deepest_market_only_rty_real():
    """RTY real: base [3,0,0] y perfil [5,0,0] — TODO market. max(levels)
    ingenuo daría 1.5; la pierna activa correcta es NINGUNA (no se toca)."""
    cfg = {
        "scale_entry": {"mode": "execute", "levels": [0.5, 1.5],
                        "quantities": [3, 0, 0]},
        "traderspost_webhook_url": "https://base",
        "profiles": [{"name": "APEXsim", "enabled": True,
                      "webhook_url": "https://p", "quantities": [5, 0, 0]}],
        "dry_run": True, "traderspost_enabled": False,
    }
    assert deepest_active_level(_dests(cfg)) is None


def test_deepest_nq_real():
    """NQ real: base [0,2,2] sobre levels [4,5] → la más profunda es 5.0."""
    cfg = {
        "scale_entry": {"mode": "execute", "levels": [4, 5],
                        "quantities": [0, 2, 2]},
        "traderspost_webhook_url": "https://base",
        "profiles": [{"name": "APEXsim", "enabled": True,
                      "webhook_url": "https://p", "quantities": [5, 0, 0]}],
        "dry_run": True, "traderspost_enabled": False,
    }
    assert deepest_active_level(_dests(cfg)) == 5.0


def test_deepest_base_sin_webhook_cae():
    """Base sin webhook + perfil habilitado: la base NO despacha (regla viva
    de resolve_destinations) — sus piernas límite no cuentan."""
    cfg = {
        "scale_entry": {"mode": "execute", "levels": [4, 5],
                        "quantities": [0, 2, 2]},
        "traderspost_webhook_url": None,
        "profiles": [{"name": "APEXsim", "enabled": True,
                      "webhook_url": "https://p", "quantities": [5, 0, 0]}],
        "dry_run": True, "traderspost_enabled": False,
    }
    assert deepest_active_level(_dests(cfg)) is None


def test_deepest_perfil_overridea_levels():
    """Un perfil puede overridear levels (delta): la pierna del PERFIL manda
    si es más profunda que la de la base."""
    cfg = {
        "scale_entry": {"mode": "execute", "levels": [0.5],
                        "quantities": [0, 1]},
        "traderspost_webhook_url": "https://base",
        "profiles": [{"name": "hondo", "enabled": True,
                      "webhook_url": "https://p",
                      "levels": [2.5], "quantities": [0, 1]}],
        "dry_run": True, "traderspost_enabled": False,
    }
    assert deepest_active_level(_dests(cfg)) == 2.5


def test_deepest_ignora_pierna_sin_nivel_y_modo_off():
    # pierna 3 con qty pero sin nivel definido → se ignora (payload_builder)
    cfg = {
        "scale_entry": {"mode": "execute", "levels": [4],
                        "quantities": [0, 2, 2]},
        "traderspost_webhook_url": "https://base",
        "dry_run": True, "traderspost_enabled": False,
    }
    assert deepest_active_level(_dests(cfg)) == 4.0
    # modo design/None → el motor cae a entrada única: sin pierna límite
    cfg["scale_entry"]["mode"] = "design_only"
    assert deepest_active_level(_dests(cfg)) is None
    # sin scale_entry (6J real) → None
    assert deepest_active_level(_dests({
        "traderspost_webhook_url": "https://base",
        "dry_run": True, "traderspost_enabled": False,
    })) is None


# ---------------------------------------------------------------------------
# plan_row — estados y lectura del cancel_after de diseño
# ---------------------------------------------------------------------------

_PB_META = {  # meta.pullback como lo cachea el camino A (claves str)
    "0.75": {"fill_rate": 97.2, "t_med": 5.0, "t_p90": 10.0,
             "cancel_after": 660},
    "5.0": {"fill_rate": 40.0, "t_med": 60.0, "t_p90": None,
            "cancel_after": None},
}


def test_plan_row_paths():
    dests = _dests({
        "scale_entry": {"mode": "execute", "levels": [0.5, 0.7],
                        "quantities": [0, 0, 3]},
        "traderspost_webhook_url": "https://base",
        "dry_run": True, "traderspost_enabled": False,
    })
    # feliz: pierna 0.7 → grilla 0.75 → cancel_after de diseño 660
    row = plan_row("GC5m_X", "MGC", dests, _PB_META, current=None)
    assert (row["instrument"], row["deepest"], row["grid"]) == ("GC", 0.7, 0.75)
    assert row["cancel_after"] == 660 and row["status"] == "aplicar"
    # nivel presente en la grilla pero SIN datos (cancel_after None) → no aplica
    dests5 = _dests({
        "scale_entry": {"mode": "execute", "levels": [5.0],
                        "quantities": [0, 1]},
        "traderspost_webhook_url": "https://base",
        "dry_run": True, "traderspost_enabled": False,
    })
    row5 = plan_row("NQ5m_X", "MNQ", dests5, _PB_META, current=3600)
    assert row5["cancel_after"] is None and row5["status"] != "aplicar"
    # sin instrumento del lab → no revienta, estado claro
    rowx = plan_row("X", "ZZZ", dests, _PB_META, current=None)
    assert rowx["status"] == "sin instrumento lab" and rowx["cancel_after"] is None
    # sin cache del lab → pide regenerar, no aplica
    rowc = plan_row("GC5m_X", "MGC", dests, None, current=None)
    assert rowc["cancel_after"] is None and "cache" in rowc["status"]
    # market-only → intacta
    rowm = plan_row("6J5m_X", "MJY", _dests({
        "traderspost_webhook_url": "https://base",
        "dry_run": True, "traderspost_enabled": False,
    }), _PB_META, current=None)
    assert rowm["status"] == "sin pierna límite activa"


# ---------------------------------------------------------------------------
# apply_suggestion — actor/reason parametrizables (audit honesto del CLI)
# ---------------------------------------------------------------------------

async def _seed(db: AsyncSession, sid: str, pipeline_config=None):
    db.add(Strategy(strategy_id=sid, name=sid, asset_symbol="M2K",
                    timeframe="15m", status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id=sid, mode="paper",
                           pipeline_config_json=pipeline_config))
    await db.commit()


@pytest.mark.asyncio
async def test_apply_suggestion_actor_override(db: AsyncSession):
    await _seed(db, "ca2")
    await apply_suggestion(db, "ca2", 660, actor="apply_cancel_after",
                           reason="cancel_after de diseño (lab F3)")
    await db.commit()
    audit = (await db.execute(select(AuditLog).where(
        AuditLog.actor == "apply_cancel_after"))).scalars().first()
    assert audit is not None
    assert "diseño" in (audit.reason or "")


# ---------------------------------------------------------------------------
# apply_regime_gate — merge, reversión y clave viva
# ---------------------------------------------------------------------------

def test_merged_regime_cfg_preserva_lo_demas():
    pj = {"scale_entry": {"mode": "execute"}, "windows": []}
    out = merged_regime_cfg(pj, "1h", ["trending_bull", "trending_bear"])
    assert out["regime"] == {"enabled": True, "timeframe": "1h",
                             "allowed_regimes": ["trending_bull",
                                                 "trending_bear"]}
    assert out["scale_entry"] == {"mode": "execute"}   # merge, no reemplazo
    assert pj.get("regime") is None                    # no muta el original
    # reversión: quita SOLO la clave regime
    back = disabled_regime_cfg(out)
    assert "regime" not in back and back["windows"] == []
    # None de entrada no revienta
    assert merged_regime_cfg(None, "1h", ["ranging"])["regime"]["timeframe"] == "1h"


@pytest.mark.asyncio
async def test_apply_gate_end_to_end_y_revert(db: AsyncSession):
    await _seed(db, "rty_t", pipeline_config={
        "scale_entry": {"mode": "execute", "levels": [0.5, 1.5],
                        "quantities": [3, 0, 0]},
    })
    old = await apply_gate(db, "rty_t", timeframe="1h",
                           allowed=["trending_bull", "trending_bear"])
    await db.commit()
    assert old is None                                 # no había gate
    db.expire_all()
    prof = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == "rty_t"))).scalar_one()
    cfg = prof.pipeline_config_json
    assert cfg["regime"]["allowed_regimes"] == ["trending_bull", "trending_bear"]
    assert cfg["scale_entry"]["levels"] == [0.5, 1.5]  # merge: conserva el resto
    # la MISMA clave llega al pipeline vía ConfigResolver (L4 la consume)
    eff = await ConfigResolver().resolve(db, "rty_t", "M2K")
    assert eff["regime"]["enabled"] is True
    assert eff["regime"]["timeframe"] == "1h"
    # audit honesto
    audit = (await db.execute(select(AuditLog).where(
        AuditLog.actor == "apply_regime_gate"))).scalars().first()
    assert audit is not None
    # reversión limpia con disable
    old2 = await apply_gate(db, "rty_t", disable=True)
    await db.commit()
    assert old2["allowed_regimes"] == ["trending_bull", "trending_bear"]
    db.expire_all()
    prof2 = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == "rty_t"))).scalar_one()
    assert "regime" not in (prof2.pipeline_config_json or {})
    assert prof2.pipeline_config_json["scale_entry"]["levels"] == [0.5, 1.5]


@pytest.mark.asyncio
async def test_apply_gate_valida_regimenes(db: AsyncSession):
    await _seed(db, "rty_v")
    with pytest.raises(ValueError):
        await apply_gate(db, "rty_v", timeframe="1h", allowed=["bogus"])
    with pytest.raises(ValueError):
        await apply_gate(db, "rty_v", timeframe="1h", allowed=[])
