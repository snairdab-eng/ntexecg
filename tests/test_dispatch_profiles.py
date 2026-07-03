"""NX-19 — suite de dispatch_profiles (perfiles de riesgo / multi-destino).

Cubre: herencia base↔perfil, cap_quantities, dedupe por webhook_url, tags, y la
semántica de capas del kill-switch POR PERFIL (NX-02): un perfil solo puede
RESTRINGIR el envío (dry_run hereda con OR, traderspost_enabled con AND), nunca
abrirlo por encima de la base. Incluye además la capa env DRY_RUN (NX-03) en
resolve_effective_dry_run.

Los tests de NX-02/NX-03 son adversariales: fallan sin el fix.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.api.webhooks_luxalgo import resolve_effective_dry_run
from app.services import dispatch_profiles as dp


def _cfg(**over) -> dict:
    """Config base ya fusionada (como la entrega ConfigResolver)."""
    base = {
        "traderspost_webhook_url": "https://tp/base",
        "scale_entry": {
            "mode": "execute",
            "levels": [0.75, 1.25],
            "quantities": [0, 1, 4],
            "max_micro_contracts": 5,
        },
        "sl_atr_multiplier": 2.5,
        "tp_atr_multiplier": 6.0,
        "dry_run": False,
        "traderspost_enabled": True,
        "profiles": [],
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# cap_quantities
# ---------------------------------------------------------------------------

def test_cap_quantities_trims_farthest_leg_first():
    assert dp.cap_quantities([0, 2, 2], 3) == [0, 2, 1]
    assert dp.cap_quantities([3, 0, 0], 2) == [2, 0, 0]


def test_cap_quantities_no_cap_or_empty_passthrough():
    assert dp.cap_quantities([1, 1, 1], None) == [1, 1, 1]
    assert dp.cap_quantities([1, 1, 1], 0) == [1, 1, 1]
    assert dp.cap_quantities([], 3) == []


# ---------------------------------------------------------------------------
# resolve_destinations — base y herencia
# ---------------------------------------------------------------------------

def test_no_profiles_single_base_destination():
    dests = dp.resolve_destinations(_cfg())
    assert len(dests) == 1
    assert dests[0]["name"] is None
    assert dests[0]["webhook_url"] == "https://tp/base"


def test_disabled_profiles_are_ignored():
    cfg = _cfg(profiles=[{"name": "off", "enabled": False,
                          "webhook_url": "https://tp/off"}])
    dests = dp.resolve_destinations(cfg)
    assert [d["name"] for d in dests] == [None]


def test_profile_inherits_base_quantities_levels_sl_tp():
    cfg = _cfg(profiles=[{"name": "agresivo", "enabled": True,
                          "webhook_url": "https://tp/agr"}])
    dests = dp.resolve_destinations(cfg)
    assert len(dests) == 2
    prof = dests[1]
    assert prof["name"] == "agresivo"
    assert prof["scale_entry"]["quantities"] == [0, 1, 4]
    assert prof["scale_entry"]["levels"] == [0.75, 1.25]
    assert prof["scale_entry"]["mode"] == "execute"  # hereda el modo base
    assert prof["sl_atr_multiplier"] == 2.5
    assert prof["tp_atr_multiplier"] == 6.0


def test_profile_overrides_quantities_and_cap_applies():
    cfg = _cfg(profiles=[{"name": "conservador", "enabled": True,
                          "webhook_url": "https://tp/con",
                          "quantities": [0, 2, 2], "max_contracts": 3}])
    prof = dp.resolve_destinations(cfg)[1]
    assert prof["scale_entry"]["quantities"] == [0, 2, 1]


def test_profile_overrides_sl_tp_multipliers():
    cfg = _cfg(profiles=[{"name": "p", "enabled": True,
                          "webhook_url": "https://tp/p",
                          "sl_atr_multiplier": 4.0, "tp_atr_multiplier": None}])
    prof = dp.resolve_destinations(cfg)[1]
    assert prof["sl_atr_multiplier"] == 4.0
    # tp explícitamente presente en el perfil (aunque None) reemplaza a la base
    assert prof["tp_atr_multiplier"] is None


# ---------------------------------------------------------------------------
# Dedupe por webhook_url / exclusión de la base
# ---------------------------------------------------------------------------

def test_profile_reusing_base_webhook_is_deduped():
    cfg = _cfg(profiles=[{"name": "dup", "enabled": True,
                          "webhook_url": "https://tp/base"}])
    dests = dp.resolve_destinations(cfg)
    assert len(dests) == 1 and dests[0]["name"] is None


def test_profile_without_webhook_inherits_base_and_dedupes():
    cfg = _cfg(profiles=[{"name": "sinwh", "enabled": True}])
    dests = dp.resolve_destinations(cfg)
    # hereda el webhook base → se deduplica contra la base (gana la base)
    assert len(dests) == 1 and dests[0]["name"] is None


def test_base_excluded_only_when_no_webhook_and_profiles_enabled():
    cfg = _cfg(traderspost_webhook_url=None,
               profiles=[{"name": "solo", "enabled": True,
                          "webhook_url": "https://tp/solo"}])
    dests = dp.resolve_destinations(cfg)
    assert [d["name"] for d in dests] == ["solo"]


# ---------------------------------------------------------------------------
# delivery_tag / profile_from_tag
# ---------------------------------------------------------------------------

def test_delivery_tag_roundtrip():
    assert dp.delivery_tag(None) == "traderspost"
    assert dp.delivery_tag("agresivo") == "traderspost:agresivo"
    assert dp.profile_from_tag("traderspost:agresivo") == "agresivo"
    assert dp.profile_from_tag("traderspost") is None
    assert dp.profile_from_tag(None) is None


# ---------------------------------------------------------------------------
# NX-02 — kill-switch por capas a nivel PERFIL (adversariales)
# ---------------------------------------------------------------------------

def test_profile_cannot_escape_base_dry_run():
    """Base (global OR estrategia) en dry_run → un perfil con dry_run=False NO abre."""
    cfg = _cfg(dry_run=True,
               profiles=[{"name": "p", "enabled": True,
                          "webhook_url": "https://tp/p", "dry_run": False}])
    prof = dp.resolve_destinations(cfg)[1]
    assert prof["dry_run"] is True


def test_profile_can_restrict_to_dry_run():
    cfg = _cfg(dry_run=False,
               profiles=[{"name": "p", "enabled": True,
                          "webhook_url": "https://tp/p", "dry_run": True}])
    prof = dp.resolve_destinations(cfg)[1]
    assert prof["dry_run"] is True


def test_profile_cannot_enable_traderspost_over_base():
    """Base traderspost_enabled=False → perfil True NO puede abrirlo (AND)."""
    cfg = _cfg(traderspost_enabled=False,
               profiles=[{"name": "p", "enabled": True,
                          "webhook_url": "https://tp/p",
                          "traderspost_enabled": True}])
    prof = dp.resolve_destinations(cfg)[1]
    assert prof["traderspost_enabled"] is False


def test_profile_can_disable_traderspost():
    cfg = _cfg(profiles=[{"name": "p", "enabled": True,
                          "webhook_url": "https://tp/p",
                          "traderspost_enabled": False}])
    prof = dp.resolve_destinations(cfg)[1]
    assert prof["traderspost_enabled"] is False


def test_profile_inherits_gates_when_unspecified():
    cfg = _cfg(profiles=[{"name": "p", "enabled": True,
                          "webhook_url": "https://tp/p"}])
    prof = dp.resolve_destinations(cfg)[1]
    assert prof["dry_run"] is False
    assert prof["traderspost_enabled"] is True


def test_gate_per_destination_respects_layering():
    """El gate evaluado con la config proyectada del perfil sigue cerrado si la
    base pedía dry_run — aunque el perfil intente abrirlo."""
    cfg = _cfg(dry_run=True,
               profiles=[{"name": "p", "enabled": True,
                          "webhook_url": "https://tp/p", "dry_run": False}])
    prof = dp.resolve_destinations(cfg)[1]
    dest_cfg = dp.make_dest_config(cfg, prof)
    st = SimpleNamespace(TRADERSPOST_ENABLED=True, DRY_RUN=False)
    assert resolve_effective_dry_run(st, dest_cfg) is True


# ---------------------------------------------------------------------------
# NX-03 — env DRY_RUN es una capa real del gate (adversarial)
# ---------------------------------------------------------------------------

def test_env_dry_run_forces_dry_even_fully_armed():
    st = SimpleNamespace(TRADERSPOST_ENABLED=True, DRY_RUN=True)
    cfg = {"traderspost_enabled": True, "dry_run": False}
    assert resolve_effective_dry_run(st, cfg) is True


def test_env_dry_run_false_allows_real_send():
    st = SimpleNamespace(TRADERSPOST_ENABLED=True, DRY_RUN=False)
    cfg = {"traderspost_enabled": True, "dry_run": False}
    assert resolve_effective_dry_run(st, cfg) is False


def test_env_dry_run_absent_does_not_force_dry():
    # Documentado: si el settings no define DRY_RUN, no fuerza dry-run (las demás
    # capas siguen mandando). Mantiene compatibilidad con settings parciales.
    st = SimpleNamespace(TRADERSPOST_ENABLED=True)
    cfg = {"traderspost_enabled": True, "dry_run": False}
    assert resolve_effective_dry_run(st, cfg) is False
