"""MR-1 — Motor de Riesgo: ingesta + persistencia (tests con respuesta conocida).

Unitarios puros (métricas USD, sesión, fecha) + integración sobre los DATOS
REALES del repo (export ES + HOLC), como test_lab_consistency. El criterio de
aceptación de MR-1: la línea base CUADRA AL DÓLAR contra el `PyG acumuladas
USD` final del export (ES 2026-06-27: $32,237.50 · 122 trades).
"""
import asyncio
import glob
import json
from datetime import datetime
from pathlib import Path

import pytest

_ES_CSV = sorted(glob.glob("ListaDeOperaciones/*_ES1!_*.csv"))
_ES_HOLC = Path("NINJATRADER/HOLC/ES_5m.csv")

_HAY_DATOS = bool(_ES_CSV) and _ES_HOLC.exists()


# ---------------------------------------------------------------------------
# Unitarios puros (sin datos externos)
# ---------------------------------------------------------------------------

def test_metrics_usd_respuesta_conocida():
    from scripts.nt_riesgo import metrics_usd

    # equity: 100, 50, 250, 225, 200 → pico 250; DD máx 50 (250→200)
    m = metrics_usd([100.0, -50.0, 200.0, -25.0, -25.0])
    assert m["n"] == 5
    assert m["ganadores"] == 2
    assert m["wr_pct"] == 40.0
    assert m["pf"] == 3.0                       # 300 / 100
    assert m["ganancia_bruta_usd"] == 300.0
    assert m["perdida_bruta_usd"] == 100.0
    assert m["net_usd"] == 200.0
    assert m["max_dd_usd"] == 50.0
    assert m["max_dd_pct_hwm"] == 20.0          # 50 / 250 (HWM del periodo)
    assert m["peor_trade_usd"] == -50.0


def test_metrics_usd_vacio():
    from scripts.nt_riesgo import metrics_usd

    assert metrics_usd([]) == {"n": 0}


def test_sesion_et():
    from scripts.nt_riesgo import sesion_et

    assert sesion_et(datetime(2026, 6, 1, 9, 30)) == "RTH"
    assert sesion_et(datetime(2026, 6, 1, 15, 59)) == "RTH"
    assert sesion_et(datetime(2026, 6, 1, 16, 0)) == "tarde"
    assert sesion_et(datetime(2026, 6, 1, 19, 0)) == "asia"
    assert sesion_et(datetime(2026, 6, 1, 2, 0)) == "asia"
    assert sesion_et(datetime(2026, 6, 1, 3, 0)) == "europa"
    assert sesion_et(datetime(2026, 6, 1, 9, 29)) == "europa"


def test_fecha_export_del_nombre():
    from scripts.nt_riesgo import _fecha_export

    p = Path("LuxAlgo_x_CME_MINI_ES1!_2026-06-27_ec244.csv")
    assert _fecha_export(p, None) == "2026-06-27"
    assert _fecha_export(p, "2026-07-04") == "2026-07-04"


def test_grids_fingerprint_estable():
    from scripts.nt_riesgo import grids_fingerprint

    fp = grids_fingerprint()
    assert fp.startswith("lab-") and len(fp) == 12
    assert fp == grids_fingerprint()            # determinista


# ── Guardarraíl de FRESCURA (reemplaza el fail-closed de la costura jubilada) ──

def _bars_hasta(last: datetime, n: int = 20) -> dict:
    from datetime import timedelta
    return {last - timedelta(minutes=5 * i): (1.0, 1.0, 1.0, 1.0, 0.0)
            for i in range(n)}


def test_frescura_rechaza_holc_viejo():
    """El HOLC más viejo que el último trade → FALLA con mensaje accionable
    (no cose cola dudosa)."""
    from datetime import timedelta
    from types import SimpleNamespace
    from scripts.nt_riesgo import _guardar_frescura

    last_bar = datetime(2026, 7, 1, 11, 35)
    bars = _bars_hasta(last_bar)
    trades = [SimpleNamespace(entry_ts=datetime(2026, 7, 1, 10, 0),
                              exit_ts=last_bar + timedelta(hours=2))]
    with pytest.raises(SystemExit, match="HOLC DESACTUALIZADO"):
        _guardar_frescura(trades, bars, 0, "ES")


def test_frescura_pasa_si_el_holc_cubre_la_lista():
    """El HOLC que cubre hasta el último trade (dentro del margen 5m) → OK."""
    from datetime import timedelta
    from types import SimpleNamespace
    from scripts.nt_riesgo import _guardar_frescura

    last_bar = datetime(2026, 7, 1, 11, 35)
    bars = _bars_hasta(last_bar)
    trades = [SimpleNamespace(entry_ts=datetime(2026, 7, 1, 10, 0),
                              exit_ts=last_bar - timedelta(minutes=30))]
    _guardar_frescura(trades, bars, 0, "ES")            # no levanta


# ---------------------------------------------------------------------------
# Integración con datos reales (se salta limpio sin data — CI)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAY_DATOS, reason="datos reales ES no disponibles")
class TestIntegrarESReal:

    @pytest.fixture(scope="class")
    def integrado(self, tmp_path_factory):
        import scripts.nt_riesgo as nr

        motor_dir = tmp_path_factory.mktemp("MotorRiesgo")
        original = nr.MOTOR_DIR
        nr.MOTOR_DIR = motor_dir
        try:
            manifest = asyncio.run(nr.integrar(
                Path(_ES_CSV[-1]), codigo="test"))
        finally:
            nr.MOTOR_DIR = original
        return motor_dir, manifest

    def test_cuadre_al_dolar(self, integrado):
        _, m = integrado
        assert m["cuadre"]["ok"] is True
        assert m["cuadre"]["pnl_parseado"] == m["cuadre"]["pnl_export"]
        # respuesta conocida del export ES 2026-06-27 (si es otro export,
        # el cuadre interno sigue siendo el criterio)
        if "2026-06-27" in _ES_CSV[-1]:
            assert m["cuadre"]["pnl_parseado"] == 32237.5
            assert m["trades"]["n"] == 122

    def test_linea_base_consistente(self, integrado):
        _, m = integrado
        b = m["linea_base_usd"]
        assert b["net_usd"] == m["cuadre"]["pnl_export"]
        assert b["ganancia_bruta_usd"] - b["perdida_bruta_usd"] == pytest.approx(
            b["net_usd"], abs=0.01)
        assert b["max_dd_usd"] > 0 and b["peor_trade_usd"] < 0

    def test_usd_por_punto_es(self, integrado):
        _, m = integrado
        assert m["usd_por_punto"]["config"] == 50.0
        assert m["usd_por_punto"]["inferido"] == pytest.approx(50.0, rel=0.01)
        assert m["usd_por_punto"]["ok"] is True

    def test_persistencia_completa(self, integrado):
        motor_dir, m = integrado
        base = motor_dir / "ES_test"
        assert (base / "master.csv").exists()
        assert (base / "enriched.csv").exists()
        assert (base / "manifest.json").exists()
        assert (base / "snapshots" / m["export"]["snapshot"]).exists()
        # master == snapshot == export original (byte a byte, por hash)
        from scripts.nt_riesgo import _sha256
        assert _sha256(base / "master.csv") == m["export"]["sha256_master"]
        assert (_sha256(base / "snapshots" / m["export"]["snapshot"])
                == m["export"]["sha256_master"])

    def test_enriched_filas_y_atr(self, integrado):
        import csv as _csv
        motor_dir, m = integrado
        with open(motor_dir / "ES_test" / "enriched.csv",
                  encoding="utf-8", newline="") as fh:
            rows = list(_csv.DictReader(fh))
        assert len(rows) == m["trades"]["n"]
        con_atr = [r for r in rows if r["atr_entry"]]
        estimados = [r for r in rows if r["atr_estimado"] == "1"]
        # HOLC truncado (2026-06-22) + export posterior → cola estimada
        assert len(estimados) == m["holc"]["atr_estimado"]
        assert len(con_atr) == m["trades"]["n"] - m["holc"]["sin_cobertura"]
        assert all(r["sesion"] in ("RTH", "tarde", "asia", "europa")
                   for r in rows)

    def test_manifest_reforzado(self, integrado):
        _, m = integrado
        assert m["version"] == 1
        assert m["holc"]["ultima_barra"]            # última barra registrada
        assert m["holc"]["stitch_db"] is False
        assert m["grids_version"].startswith("lab-")
        assert m["tz"]["sanity"] >= 0.70            # pasó la guarda bloqueante
        assert m["integrado"]                        # fecha determinista

    def test_estado_lee_manifest(self, integrado, capsys):
        import scripts.nt_riesgo as nr
        motor_dir, m = integrado
        original = nr.MOTOR_DIR
        nr.MOTOR_DIR = motor_dir
        try:
            out = nr.estado()
        finally:
            nr.MOTOR_DIR = original
        assert len(out) == 1 and out[0]["codigo"] == "test"
        texto = capsys.readouterr().out
        assert "ES_test" in texto and "línea base" in texto

    def test_reintegrar_es_idempotente(self, integrado):
        """Reintegrar el MISMO export sobrescribe sin quejas (superconjunto)
        y produce el mismo manifest (menos el commit, que es del repo)."""
        import scripts.nt_riesgo as nr
        motor_dir, m1 = integrado
        original = nr.MOTOR_DIR
        nr.MOTOR_DIR = motor_dir
        try:
            m2 = asyncio.run(nr.integrar(
                Path(_ES_CSV[-1]), codigo="test"))
        finally:
            nr.MOTOR_DIR = original
        assert m2["trades"]["superconjunto_ok"] is True
        for k in ("linea_base_usd", "cuadre", "trades", "holc", "tz",
                  "grids_version", "export"):
            assert m1[k] == m2[k], k
