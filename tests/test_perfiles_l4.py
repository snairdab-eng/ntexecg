"""LOTE L4 — Panel de Perfiles: helpers compartidos (position_sizing) + panel
read-only (sizing/peor-caso/caps/webhook enmascarado) + Export con el
payload_builder REAL (R-T8)."""
from app.models.strategy import Strategy
from app.services import dispatch_profiles as dprof
from app.services.position_sizing import (
    alloc_from, micros_that_fit, scale_alloc, size_for_caps, worst_case_loss,
)
from app.web.routes_strategies import (
    _export_payloads, _mask_webhook, _perfiles_panel,
)


# ---------------------------------------------------------------------------
# Helpers compartidos (los reusa la regla 3 del Portafolio — sin deps de Luxy)
# ---------------------------------------------------------------------------

def test_scale_alloc_fronteras():
    assert scale_alloc([5, 3, 2], 1.0) == [5, 3, 2]        # sin reescalar
    assert scale_alloc([5, 3, 2], 0.5) == [3, 1, 1]        # mitad, mayor residuo
    assert scale_alloc([5, 3, 2], 0.05) == [1, 0, 0]       # C1 ≥ 1 siempre
    assert sum(scale_alloc([5, 3, 2], 0.7, max_contracts=4)) == 4  # tope


def test_alloc_from_fronteras():
    assert alloc_from([1.0, 0.0, 0.0]) == [10, 0, 0]
    assert alloc_from([1.0, 1.0, 0.0]) == [5, 5, 0]
    a = alloc_from([1.0, 1.0, 1.0])
    assert sum(a) == 10 and a[0] >= 1
    assert alloc_from([0.0, 1.0, 1.0])[0] >= 1             # C1 forzado


def test_worst_case_loss():
    # 5·(80−0)·5 + 3·(80−8)·5 + 2·(80−16)·5 = 2000+1080+640 = 3720
    assert worst_case_loss(80, [0, 8, 16], [5, 3, 2], 5.0) == 3720.0
    # pierna más profunda que el SL no aporta negativo (se recorta a 0)
    assert worst_case_loss(10, [0, 20], [1, 1], 5.0) == 1 * 10 * 5


def test_size_for_caps_baja_por_max_loss():
    """max_loss_per_trade excedido → el tamaño BAJA hasta cumplir."""
    r = size_for_caps([5, 3, 2], sl=80, levels=[0, 8, 16], pv_micro=5.0,
                      max_loss_per_trade=500.0)
    assert r["limited_by"] == "max_loss_per_trade"
    assert r["total"] < 10 and r["worst_case"] <= 500.0
    # sin caps → hereda tal cual
    r2 = size_for_caps([5, 3, 2], sl=80, levels=[0, 8, 16], pv_micro=5.0)
    assert r2["alloc"] == [5, 3, 2] and r2["limited_by"] is None


def test_micros_that_fit():
    assert micros_that_fit(80, 5.0, 500.0) == 1           # 400/micro → 1
    assert micros_that_fit(80, 5.0, None) is None


# ---------------------------------------------------------------------------
# Panel read-only
# ---------------------------------------------------------------------------

def test_mask_webhook():
    m = _mask_webhook("https://webhooks.traderspost.io/trading/webhook/abcdef123456")
    assert m == "…123456"
    assert "abcdef" not in m                              # nunca el token completo
    assert _mask_webhook(None) is None


def _strategy():
    return Strategy(strategy_id="X", name="X", asset_symbol="MES",
                    status="paper", enabled=True)


def _config():
    return {
        "backstop_points": 80.0,
        "scale_entry": {"quantities": [5, 3, 2], "levels": [8, 16],
                        "mode": "execute"},
        "traderspost_webhook_url": "https://tp/base/aaa111",
        "profiles": [{"enabled": True, "name": "apex", "quantities": [3, 2, 1],
                      "max_contracts": 6, "max_loss_per_trade": 500.0,
                      "max_daily_loss": 1000.0,
                      "webhook_url": "https://tp/apex/zzz999"}],
        "dry_run": True, "traderspost_enabled": False,
    }


def _luxy():
    return {"usd_por_punto": 50.0, "dashboard": {
        "pv": 50.0, "ref_price": 5000.0, "units": {"atr_med_pts": 10.0},
        "reco": {"alloc": [5, 3, 2], "sl_pts": 80.0, "l2_pts": 8.0,
                 "l3_pts": 16.0}}}


def test_perfiles_panel_sizing_y_caps():
    panel = _perfiles_panel(_strategy(), _config(), _luxy())
    assert panel is not None
    names = [r["name"] for r in panel["rows"]]
    assert "principal" in names and "apex" in names
    main = next(r for r in panel["rows"] if r["is_main"])
    assert main["alloc"] == [5, 3, 2] and main["worst_case"] == 3720.0
    apex = next(r for r in panel["rows"] if r["name"] == "apex")
    # el cap de pérdida baja el tamaño (peor-caso ≤ tope)
    assert apex["limited_by"] == "max_loss_per_trade"
    assert apex["worst_case"] <= 500.0 and apex["total_micros"] < 6
    assert apex["insight"] and "solo aguanta" in apex["insight"]
    # webhook enmascarado por perfil
    assert apex["webhook_masked"] == "…zzz999"


def test_perfiles_panel_sin_estudio_none():
    assert _perfiles_panel(_strategy(), _config(), None) is None
    assert _perfiles_panel(_strategy(), _config(), {"dashboard": {}}) is None


def test_export_r_t8_builder_real():
    """El Export usa el payload_builder REAL: precios ABSOLUTOS (stopPrice) +
    guarda P0; NUNCA el formato del andamio (action:add / offsets)."""
    cfg = _config()
    dest = dprof.resolve_destinations(cfg)[0]              # base
    pls = _export_payloads(_strategy(), cfg, dest, 5000.0, 10.0, True)
    assert pls, "el builder real debía producir payloads"
    sl_abs = 5000.0 - 80.0                                 # backstop absoluto
    assert pls[0]["stopLoss"]["stopPrice"] == sl_abs       # precio ABSOLUTO
    assert pls[0]["action"] == "buy"
    for pl in pls:
        assert "add" not in str(pl.get("action"))          # no es el andamio
        assert "amount" not in pl                          # sin offsets
    # sin bracket computable → vacío (fail-closed honesto)
    cfg2 = dict(cfg); cfg2["backstop_points"] = None
    cfg2["scale_entry"] = dict(cfg2["scale_entry"])
    dest2 = dprof.resolve_destinations(cfg2)[0]
    assert _export_payloads(_strategy(), cfg2, dest2, 5000.0, None, True) == []
