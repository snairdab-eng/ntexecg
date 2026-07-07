"""Pestaña Riesgo — front-end del Motor de Riesgo.

Candados verificados:
  - la página renderiza la analítica del motor SIN recalcular (lee
    estudios_*.json / heatmap / manifest de MotorRiesgo/);
  - subida validada con el parser real; integrar corre vía el motor y sus
    errores (doble-prefijo, cuadre) llegan claros al UI;
  - estrategia NUEVA de primera clase: alta por UI = manifest confirmado;
  - calcular = job en segundo plano con polling (patrón del Lab);
  - aceptación (datos reales, gated): subir el export ES → Calcular → VER
    la línea base ($28,175), la comparación y el heatmap — sin terminal.
"""
import asyncio
import glob
import json
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.web.routes_lab as routes_lab
import app.web.routes_riesgo as rr
from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.strategy import Strategy

_ES_CSV = sorted(glob.glob("ListaDeOperaciones/*_ES1!_*.csv"))
_ES_HOLC = Path("NINJADATA_PLACEHOLDER")  # se resuelve abajo
_ES_HOLC = Path("NINJATRADER/HOLC/ES_5m.csv")
_HAY_DATOS = bool(_ES_CSV) and _ES_HOLC.exists()


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_riesgo")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


@pytest.fixture()
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "REPORTES").mkdir()
    (tmp_path / "ListaDeOperaciones").mkdir()
    (tmp_path / "MotorRiesgo").mkdir()
    monkeypatch.setattr(routes_lab, "LAB_DIR", tmp_path / "REPORTES")
    monkeypatch.setattr(routes_lab, "TRADES_DIR",
                        tmp_path / "ListaDeOperaciones")
    monkeypatch.setattr(rr, "MOTOR_DIR", tmp_path / "MotorRiesgo")
    monkeypatch.setattr(rr, "TRADES_DIR", tmp_path / "ListaDeOperaciones")
    rr.JOBS.clear()
    rr._INTEGRAR_LOCKS.clear()
    return tmp_path


def _write_lab_manifest(dirs: Path, entries: dict) -> None:
    (dirs / "REPORTES" / "lab_manifest.json").write_text(
        json.dumps({"version": 1, "entries": entries}), encoding="utf-8")


MOTOR_MAN = {
    "version": 1, "activo": "ES", "codigo": "Test",
    "integrado": "2026-07-04",
    "export": {"archivo": "Lux_ES1!_2026-07-04.csv",
               "sha256_master": "ab" * 32,
               "snapshot": "export_2026-07-04.csv"},
    "trades": {"n": 120, "desde": "2026-03-24T09:00:00",
               "hasta": "2026-07-03T15:00:00"},
    "usd_por_punto": {"usado": 50.0},
    "holc": {"archivo": "ES_5m.csv", "ultima_barra": "2026-06-22T22:30:00",
             "stitch_db": False, "sin_cobertura": 0, "atr_estimado": 18},
    "cuadre": {"ok": True},
}

ESTUDIO = {
    "linea_base": {
        "total": {"n": 120, "net_usd": 28175.0, "pf": 1.62, "wr_pct": 79.2,
                  "max_dd_usd": 11750.0, "peor_trade_usd": -10162.5},
        "in": {}, "out": {"pf": 0.92},
    },
    "recomendacion": {
        "config": "5+5 MES @ 0.25/0.5× + backstop",
        "escalera": {"anclaje": "precio_senal", "n_piernas": 2,
                     "piernas": [{"depth_atr": 0.25, "micros": 5},
                                 {"depth_atr": 0.5, "micros": 5}],
                     "total_micros": 10},
        "backstop": {"usd_por_mini": 4500.0, "pts": 90.0,
                     "usd_por_micro": 450.0,
                     "tipo": "stop_precio_fijo_desde_senal"},
        "tp_nominal_atr": {"long": 11.5, "short": 8.0},
        "confianza_oos": {"pf_out": 1.4, "delta_pf_out": 0.54,
                          "veredicto": "validado", "flags": [],
                          "nota": "número OOS"},
        "metricas": {"total": {"net_usd": 35478.0, "pf": 2.0,
                               "wr_pct": 78.0, "max_dd_usd": 7045.0,
                               "peor_trade_usd": -4408.0},
                     "out": {}, "participacion_pct": 90.8},
        "gestion_por_lado": "motor de LARGOS (cortos casi break-even)",
        "cancel_after_seconds": 2760,
        "corte": {"cancel_after_s_estudio": 3600.0,
                  "tope_natural_atr": 2.0, "nota": "reales"},
    },
    "corte_fills": {"cancel_after_s": 3600.0, "tope_natural_atr": 2.0,
                    "niveles": []},
    "ls": {"lectura": "motor de LARGOS (cortos casi break-even)"},
    "configs": [{"gate": {"estado": "aprobada"}}],
    "robustez": {"elegido": {"nombre": "5+5 MES @ 0.25/0.5× + backstop"}},
    "meta": {"fecha": "2026-07-05"},
}


# v2 — estudio de PROTECCIÓN DE CUENTA (in-sample) persistido por el motor;
# la selección por cuenta la hace la ruta con proteccion_para_cuenta (pura).
PROTECCION = {
    "suelo_atr": 3.6, "atr_mediana_pts": 14.7,
    "lado_candidato": None, "lado_muestra_chica": False, "lado_n_malo": None,
    "tp_nominal_atr": None,
    "umbral_alarma_pct": 10.0,
    "etiqueta": ("in-sample, sin validar OOS — para proteger la cuenta, "
                 "NO promesa a futuro"),
    "perdedores": [{"number": 46, "side": "short", "pnl_usd": -10162.5},
                   {"number": 12, "side": "long", "pnl_usd": -900.0}],
    "combos": [
        {"escalera": {"nombre": "entrada única a la señal",
                      "piernas": [{"depth_atr": 0.0, "micros": 10}]},
         "sl_atr": None, "backstop_usd": None,
         "tp_por_lado_atr": {"long": 11.5, "short": 8.0},
         "lado": None, "n_palancas": 0, "participacion_pct": 100.0,
         "ganadoras_cortadas_pct": 0.0,
         "metricas": {"n": 120, "net_usd": 28175.0, "pf": 1.62,
                      "wr_pct": 79.2, "max_dd_usd": 11750.0,
                      "peor_trade_usd": -10162.5}},
        {"escalera": {"nombre": "5+5 MES @ 0.25/0.5× + backstop",
                      "piernas": [{"depth_atr": 0.25, "micros": 5},
                                  {"depth_atr": 0.5, "micros": 5}]},
         "sl_atr": 4.0, "backstop_usd": None,
         "tp_por_lado_atr": {"long": 11.5, "short": 8.0},
         "lado": None, "n_palancas": 2, "participacion_pct": 100.0,
         "ganadoras_cortadas_pct": 2.1,
         "metricas": {"n": 120, "net_usd": 26400.0, "pf": 1.7,
                      "wr_pct": 77.5, "max_dd_usd": 6100.0,
                      "peor_trade_usd": -2940.0}},
    ],
}

LISTADO_CRUDO = {
    "metricas": {"n": 120, "net_usd": 28175.0, "pf": 1.62, "wr_pct": 79.2,
                 "max_dd_usd": 11750.0, "peor_trade_usd": -10162.5},
    "duracion_h": {"ganador_prom_h": 26.9, "perdedor_prom_h": 15.1,
                   "n_ganadores": 95, "n_perdedores": 25},
}


def _seed_motor(dirs: Path, clave: str = "ES_Test",
                con_estudio: bool = True, estudio: dict | None = None) -> None:
    base = dirs / "MotorRiesgo" / clave
    (base / "runs").mkdir(parents=True)
    (base / "snapshots").mkdir()
    (base / "snapshots" / "export_2026-07-04.csv").write_text(
        "x", encoding="utf-8")
    (base / "manifest.json").write_text(
        json.dumps(MOTOR_MAN), encoding="utf-8")
    if con_estudio:
        (base / "runs" / "estudios_2026-07-05.json").write_text(
            json.dumps(estudio or ESTUDIO), encoding="utf-8")
        (base / "runs" / "heatmap_ES_Test_2026-07-05.png").write_bytes(
            b"\x89PNG\r\n\x1a\nfakepng")
        (base / "runs" / "Riesgo_ES_Test_2026-07-05.md").write_text(
            "# Riesgo — ES", encoding="utf-8")


# CSV mínimo que el parser real acepta (una entrada+salida de LuxAlgo)
CSV_OK = (
    "Trade number,Tipo,Fecha y hora,Señal,Precio USD,Tamaño (cant.),"
    "Tamaño de la posición (valor),PyG netas USD,PyG netas %,"
    "Desviación favorable USD,Desviación favorable %,"
    "Desviación adversa USD,Desviación adversa %,"
    "PyG acumuladas USD,PyG acumuladas %\n"
    "1,Salida en largo,2026-03-16 14:30,Exit,6711.5,1,335150,425,0.13,"
    "575,0.17,-487.5,-0.15,425,4.25\n"
    "1,Entrada en largo,2026-03-16 13:10,Long,6703,1,335150,425,0.13,"
    "575,0.17,-487.5,-0.15,425,4.25\n"
)


# ---------------------------------------------------------------------------
# Mapeo estrategia ↔ carpeta (puro)
# ---------------------------------------------------------------------------

def test_fmt_stop_fx_en_ticks_no_puntos():
    """P1-2: FX se expresa en ticks/$ — el yen daba '0 pts' (0.00036 en
    unidad de precio). Índices siguen en puntos. SOLO display."""
    from scripts.mr_report import fmt_stop

    yen = fmt_stop("6J", 0.00036, 4500.0)
    assert "ticks" in yen and "$4,500" in yen
    assert "0 pts" not in yen
    assert "720" in yen                       # 0.00036 / 0.0000005
    euro = fmt_stop("6E", 0.036, 4500.0)
    assert "720 ticks" in euro                # 0.036 / 0.00005
    es = fmt_stop("ES", 90.0, 4500.0)
    assert es == "90 pts = $4,500/mini"
    assert fmt_stop("ES", None, 4500.0) == "—"


def test_derive_codigo_y_clave():
    assert rr.clave_de("ES5m_ConfNormal_TC_TSR", "ES") == \
        "ES_ConfNormal_TC_TSR"
    assert rr.clave_de("6E5m_ConfStrong_NC_WeakConf", "6E") == \
        "6E_ConfStrong_NC_WeakConf"
    assert rr.clave_de("YM", "YM") == "YM_default"
    assert rr.clave_de("ES_X", "ES") == "ES_X"     # sin doble prefijo
    assert rr.clave_de("ConfSolo", "ES") == "ES_ConfSolo"


# ---------------------------------------------------------------------------
# Página
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pagina_renderiza_estudio(client: AsyncClient, dirs: Path,
                                        db: AsyncSession) -> None:
    _write_lab_manifest(dirs, {"ES5m_Test": {
        "instrument": "ES", "csv": "ListaDeOperaciones/x.csv",
        "confirmed": True}})
    _seed_motor(dirs)
    db.add(Strategy(strategy_id="ES5m_Test", name="T", asset_symbol="MES",
                    status="paper", enabled=True))
    await db.commit()

    r = await client.get("/ui/riesgo?strategy=ES5m_Test")
    assert r.status_code == 200
    html = r.text
    assert "28,175" in html                       # línea base
    assert "CON CONFIG recomendada" in html       # comparación rotulada
    assert "CRUDO" in html                        # etiqueta P1-5
    assert "PF OOS" in html                       # número de confianza
    assert "35,478" in html                       # recomendada
    assert "cancel_after" in html                 # corte / honestidad
    assert "fills reales de producción" in html
    assert "/ui/riesgo/heatmap?strategy=ES5m_Test" in html
    assert "/ui/strategies/ES5m_Test" in html     # puente a config viva
    assert "backstop_points" in html              # JSON de activación


@pytest.mark.asyncio
async def test_pagina_sin_estudio_y_sin_master(client: AsyncClient,
                                               dirs: Path) -> None:
    _write_lab_manifest(dirs, {"ES5m_Test": {
        "instrument": "ES", "csv": "ListaDeOperaciones/x.csv",
        "confirmed": True}})
    r = await client.get("/ui/riesgo?strategy=ES5m_Test")
    assert r.status_code == 200
    assert "Sin listado integrado" in r.text

    _seed_motor(dirs, con_estudio=False)
    r2 = await client.get("/ui/riesgo?strategy=ES5m_Test")
    assert "sin estudio todavía" in r2.text
    assert "Calcular" in r2.text


@pytest.mark.asyncio
async def test_script_valido_con_job_en_memoria(client: AsyncClient,
                                                dirs: Path) -> None:
    """Regresión (bug producción 2026-07-06): con un job en JOBS, el
    autoescape de Jinja convertía las comillas de `job: '<estado>'` en
    &#39; → SyntaxError → riesgoApp sin definir → TODOS los botones del
    componente muertos ("me dejó eliminar una vez pero después ya no":
    la primera carga era sin job → null → script válido; tras Calcular,
    job='done' rompía cada carga siguiente). tojson es autoescape-safe."""
    _write_lab_manifest(dirs, {"ES5m_Test": {
        "instrument": "ES", "csv": "ListaDeOperaciones/x.csv",
        "confirmed": True}})
    _seed_motor(dirs)
    rr.JOBS["ES_Test"] = {"status": "done", "tail": "", "rc": 0}
    try:
        r = await client.get("/ui/riesgo?strategy=ES5m_Test")
    finally:
        rr.JOBS.pop("ES_Test", None)
    assert r.status_code == 200
    html = r.text
    script = html.split("function riesgoApp()")[1].split("</script>")[0]
    # el veneno exacto del bug: entidades HTML dentro del JS inline
    for entidad in ("&#39;", "&#34;", "&quot;", "&amp;"):
        assert entidad not in script, entidad
    assert 'job: "done",' in script
    # y sin job: null literal (no la cadena "None")
    rr.JOBS.pop("ES_Test", None)
    r2 = await client.get("/ui/riesgo?strategy=ES5m_Test")
    script2 = r2.text.split("function riesgoApp()")[1].split("</script>")[0]
    assert "job: null," in script2
    assert "None" not in script2.split("cuenta:")[0]


# ---------------------------------------------------------------------------
# P1-1 — banner "sin recomendación validada" (nunca en blanco)
# ---------------------------------------------------------------------------

ESTUDIO_SIN_RECO = {
    "linea_base": {
        "total": {"n": 40, "net_usd": 5200.0, "pf": 1.9, "wr_pct": 70.0,
                  "max_dd_usd": 1200.0, "peor_trade_usd": -800.0},
        "in": {}, "out": {"pf": 2.1},
    },
    "recomendacion": None,
    "backstop": {"optimo": {"backstop_usd": 3000.0}},
    "corte_fills": {"cancel_after_s": 3600.0, "tope_natural_atr": 1.0,
                    "niveles": []},
    "ls": {"lectura": "sin asimetría dominante"},
    "configs": [
        {"nombre": "7+3 MES @ 0.25/0.5× + backstop", "n_piernas": 2,
         "etiquetas": ["barrido", "alta_participacion"],
         "participacion_pct": 88.0,
         "total": {"net_usd": 5900.0, "pf": 2.2, "max_dd_usd": 900.0,
                   "peor_trade_usd": -600.0},
         "gate": {"estado": "aprobada", "score": 6.5}},
        {"nombre": "señal + backstop (sin escalera)", "n_piernas": 1,
         "etiquetas": [], "participacion_pct": 100.0,
         "total": {"net_usd": 5300.0, "pf": 1.95, "max_dd_usd": 1100.0,
                   "peor_trade_usd": -700.0},
         "gate": {"estado": "aprobada", "score": 4.8}},
    ],
    "robustez": {
        "elegido": None,
        "tabla": [{"nombre": "7+3 MES @ 0.25/0.5× + backstop",
                   "veredicto": "no generaliza OOS", "flags": ["n_bajo"]}],
    },
    "meta": {"fecha": "2026-07-06"},
}


@pytest.mark.asyncio
async def test_banner_sin_recomendacion_validada(client: AsyncClient,
                                                 dirs: Path) -> None:
    """Adversarial: con ELEGIDO=ninguno (6E/6J/YM) la sección de estudio NO
    puede quedar en blanco — banner con el motivo honesto + top configs
    aprobadas marcadas 'no validadas por OOS'."""
    _write_lab_manifest(dirs, {"6E5m_Test": {
        "instrument": "6E", "csv": "ListaDeOperaciones/x.csv",
        "confirmed": True}})
    base = dirs / "MotorRiesgo" / "6E_Test"
    (base / "runs").mkdir(parents=True)
    (base / "snapshots").mkdir()
    (base / "snapshots" / "export_2026-07-04.csv").write_text(
        "x", encoding="utf-8")
    man = dict(MOTOR_MAN, activo="6E", codigo="Test")
    (base / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    (base / "runs" / "estudios_2026-07-06.json").write_text(
        json.dumps(ESTUDIO_SIN_RECO), encoding="utf-8")

    r = await client.get("/ui/riesgo?strategy=6E5m_Test")
    assert r.status_code == 200
    html = r.text
    assert "Sin recomendación validada" in html
    assert "no generaliza" in html or "nativo" in html      # motivo honesto
    assert "no validadas por OOS" in html
    assert "7+3 MES @ 0.25/0.5× + backstop" in html          # top referencia
    # y las etiquetas crudo/config presentes (P1-5)
    assert "CRUDO" in html


@pytest.mark.asyncio
async def test_tarjeta_gestion_por_lado(client: AsyncClient,
                                        dirs: Path) -> None:
    """P1b: estudio con recomendación de lado → tarjeta 'Gestión por lado'
    con efecto y caveat; sin ella (ES simétrico) → sin tarjeta."""
    _write_lab_manifest(dirs, {"YM5m_Test": {
        "instrument": "YM", "csv": "ListaDeOperaciones/x.csv",
        "confirmed": True}})
    base = dirs / "MotorRiesgo" / "YM_Test"
    (base / "runs").mkdir(parents=True)
    (base / "snapshots").mkdir()
    (base / "snapshots" / "export_2026-07-04.csv").write_text(
        "x", encoding="utf-8")
    (base / "manifest.json").write_text(
        json.dumps(dict(MOTOR_MAN, activo="YM", codigo="Test")),
        encoding="utf-8")
    est = json.loads(json.dumps(ESTUDIO_SIN_RECO))
    est["gestion_lado"] = {"recomendacion": {
        "lado_malo": "short", "lado_bueno": "long", "accion": "cortar",
        "motivo": "cortos: net -8,220 USD, PF 0.31 y concentra la "
                  "catástrofe (peor -9,175)",
        "n_lado_malo": 19, "muestra_chica": True,
        "efecto_solo_lado_bueno": {"net_usd": 30910.0, "wr_pct": 100.0,
                                   "max_dd_usd": 0.0,
                                   "peor_trade_usd": 105.0},
        "mecanismo": "solo largos — filtro de lado en la config (paso "
                     "aparte)",
        "caveat": "recomendación ESTRUCTURAL — no pasa por el walk-forward; "
                  "considera cortar y valida en demo — muestra chica "
                  "(19 trades en el lado cortos)",
    }}
    (base / "runs" / "estudios_2026-07-06.json").write_text(
        json.dumps(est), encoding="utf-8")

    r = await client.get("/ui/riesgo?strategy=YM5m_Test")
    assert r.status_code == 200
    html = r.text
    assert "Gestión por lado" in html
    assert "CORTAR" in html.upper()
    assert "30,910" in html                    # el efecto (solo largos)
    assert "muestra chica" in html             # caveat honesto
    assert "no pasa por el walk-forward" in html


@pytest.mark.asyncio
async def test_sin_gestion_lado_no_hay_tarjeta(client: AsyncClient,
                                               dirs: Path) -> None:
    _write_lab_manifest(dirs, {"ES5m_Test": {
        "instrument": "ES", "csv": "ListaDeOperaciones/x.csv",
        "confirmed": True}})
    _seed_motor(dirs)                          # ESTUDIO sin gestion_lado
    r = await client.get("/ui/riesgo?strategy=ES5m_Test")
    assert r.status_code == 200
    # texto exclusivo del CUERPO de la tarjeta (el bullet de la
    # recomendación y el comentario HTML mencionan el título legítimamente)
    assert "no pasa por el walk-forward" not in r.text
    assert "el lado malo" not in r.text.lower()


# ---------------------------------------------------------------------------
# v2 — los DOS estudios conviven y se espejean; cuenta editable; gestión de
# datos (renombrar/eliminar estrategia, eliminar/reemplazar el .csv)
# ---------------------------------------------------------------------------

def _manifest_es(dirs: Path, csv: str = "ListaDeOperaciones/x.csv") -> None:
    _write_lab_manifest(dirs, {"ES5m_Test": {
        "instrument": "ES", "csv": csv, "confirmed": True}})


@pytest.mark.asyncio
async def test_dos_estudios_conviven_y_espejean(client: AsyncClient,
                                                dirs: Path) -> None:
    """Los dos estudios en la misma ficha, con la MISMA estructura visual
    (6 KPI crudo → con config); solo cambia la tarjeta ★: 'PF fuera de
    muestra' (validado) vs 'PF (in-sample)' (sin validar — para decidir).
    El trade desastroso va en ROJO con su % de la cuenta, la duración
    ganador/perdedor está en la base y la participación es banner."""
    _manifest_es(dirs)
    est = json.loads(json.dumps(ESTUDIO))
    est["proteccion"] = PROTECCION
    est["listado_crudo"] = LISTADO_CRUDO
    _seed_motor(dirs, estudio=est)

    r = await client.get("/ui/riesgo?strategy=ES5m_Test")
    assert r.status_code == 200
    html = r.text
    # B — las dos secciones rotuladas, conviviendo
    assert "Estudio validado (fuera de muestra)" in html
    assert "Protección de cuenta (in-sample)" in html
    # espejo: misma tarjeta ★, distinto label+badge
    assert "PF fuera de muestra (OOS) ★" in html
    assert "PF (in-sample) ★" in html
    assert "sin validar — para decidir" in html
    assert "NO promesa a futuro" in html
    # A — el desastre en ROJO con % de la cuenta ($10k default: 101.6%)
    assert "101.6% de la cuenta" in html
    assert "#46" in html
    # a $10k nada llega al umbral → se muestra lo más cercano (SL 4×ATR)
    assert "SL 4×ATR" in html
    assert "supervivencia &gt; net" in html or "supervivencia > net" in html
    # B — participación como banner en AMBOS estudios
    assert html.count("Participación:</b> crudo 100%") == 2
    # C — duración media ganador/perdedor en la línea base
    assert "ganador 26.9h" in html and "perdedor 15.1h" in html
    # el suelo del SL (deja respirar) está explicado
    assert "p95 3.6" in html


@pytest.mark.asyncio
async def test_orden_tarjetas_adyacentes_heatmap_colapsado(
    client: AsyncClient, dirs: Path
) -> None:
    """Reorganización: las DOS filas de tarjetas KPI quedan ADYACENTES
    (validado y protección, una arriba de la otra — comparación de un
    vistazo); recomendaciones/JSON DEBAJO de ambas; heatmap al FINAL en un
    <details> colapsado por defecto (referencia, no la vista principal)."""
    import re

    _manifest_es(dirs)
    est = json.loads(json.dumps(ESTUDIO))
    est["proteccion"] = PROTECCION
    est["listado_crudo"] = LISTADO_CRUDO
    _seed_motor(dirs, estudio=est)

    r = await client.get("/ui/riesgo?strategy=ES5m_Test")
    assert r.status_code == 200
    html = r.text

    i_crudo = html.index("CRUDO (baseline)")
    i_kpi1 = html.index("PF fuera de muestra (OOS) ★")     # tarjetas estudio 1
    i_kpi2 = html.index("PF (in-sample) ★")                # tarjetas estudio 2
    i_reco = html.index("Recomendación (validada OOS)")    # detalle 1
    i_prot = html.index("palancas y efecto")               # detalle 2 (título
    # único de la tarjeta; el banner del bloque KPI solo la menciona)
    i_json = html.index("pipeline_config_json")            # JSON de activación
    i_heat = html.index("Candidatas del walk-forward")     # heatmap (colapsado)

    # orden: CRUDO → KPIs validado → KPIs protección → detalles → heatmap
    assert i_crudo < i_kpi1 < i_kpi2 < i_reco < i_prot < i_heat
    assert i_kpi2 < i_json                       # el JSON no separa tarjetas

    # ADYACENTES: entre las dos filas KPI no hay heatmap, ni JSON, ni
    # recomendaciones (solo la nota de honestidad y el header del bloque 2)
    entre = html[i_kpi1:i_kpi2]
    assert "Candidatas" not in entre
    assert "pipeline_config_json" not in entre
    assert "Recomendación" not in entre

    # heatmap dentro de <details> SIN atributo open (colapsado por defecto)
    m = re.search(r"<details[^>]*>", html)
    assert m is not None, "el heatmap ya no está en un <details>"
    assert "open" not in m.group(0)
    assert html.index(m.group(0)) < i_heat       # y lo envuelve
    # la imagen sigue sirviéndose desde el endpoint del motor
    assert "/ui/riesgo/heatmap?strategy=ES5m_Test" in html


@pytest.mark.asyncio
async def test_cuenta_editable_persiste_y_recomputa(client: AsyncClient,
                                                    dirs: Path) -> None:
    """La cuenta es editable, persiste y RECOMPUTA la selección: a $10k el
    −10,162 es rojo (101.6%); a $200k no hay rojos y el crudo ya protege
    (0 palancas — 'sin SL adicional')."""
    _manifest_es(dirs)
    est = json.loads(json.dumps(ESTUDIO))
    est["proteccion"] = PROTECCION
    _seed_motor(dirs, estudio=est)

    r = await client.get("/ui/riesgo?strategy=ES5m_Test")
    assert "101.6% de la cuenta" in r.text            # default $10,000

    # rango inválido → 400 y NO persiste
    r = await client.post("/ui/riesgo/cuenta", json={"cuenta_usd": 1.0})
    assert r.status_code == 400

    r = await client.post("/ui/riesgo/cuenta", json={"cuenta_usd": 200_000})
    assert r.status_code == 200
    r = await client.get("/ui/riesgo?strategy=ES5m_Test")
    html = r.text
    assert "200000" in html                           # persistió (input)
    assert "101.6% de la cuenta" not in html          # % relativo a cuenta
    assert "Sin trades rojos" in html
    assert "sin freno adicional" in html              # el crudo ya protege
    assert "5.1" in html                              # peor 10,162/200k


@pytest.mark.asyncio
async def test_config_a_aplicar_unidades_y_copy(client: AsyncClient,
                                                dirs: Path, db) -> None:
    """R-obs 3/4/5: tarjeta 'Configuración a aplicar' (bloqueos sí/no
    explícitos, JSON al lado), unidades con fuente única en Symbol Mapper
    (sin tick data → aviso 'catálogo incompleto', nunca un número
    engañoso), y la copy nueva de las palancas de protección (freno
    catastrófico, escalera REAL, TP por encima del p99)."""
    _manifest_es(dirs)
    est = json.loads(json.dumps(ESTUDIO))
    est["proteccion"] = PROTECCION
    est["meta"] = {"fecha": "2026-07-05", "usd_por_punto": 50.0,
                   "activo": "ES"}
    est["backstop"] = {"atr_mediana_pts": 14.7}
    _seed_motor(dirs, estudio=est)

    # SIN catálogo: solo $ + aviso — nunca ticks/pts inventados
    r = await client.get("/ui/riesgo?strategy=ES5m_Test")
    assert r.status_code == 200
    html = r.text
    assert "Configuración a aplicar" in html
    assert "catálogo incompleto" in html.lower()
    assert "Bloquear largos" in html and "Bloquear cortos" in html
    assert "por encima de los cierres de LuxAlgo" in html
    assert "backstop_points" in html               # el JSON sigue al lado
    # copy nueva de las palancas de protección (elegido = SL 4 + escalera)
    assert "freno catastrófico" in html
    assert "Sin stop adicional de $ fijo" in html
    assert "se toca rara vez" in html
    assert "5 micros @ 0.25×ATR" in html           # escalera REAL, no fijo
    assert "la escalera no mejoró" not in html

    # CON catálogo (Symbol Mapper, fuente única): unidad natural + $
    from app.models.symbol_map import SymbolMap
    db.add(SymbolMap(tv_symbol="MES", mapped_symbol="MESU2026",
                     exchange="CME", contract_type="future",
                     pine_script_config='"ticker": "MES"',
                     tick_size=0.25, tick_value=1.25, active=True))
    await db.commit()
    r = await client.get("/ui/riesgo?strategy=ES5m_Test")
    html = r.text
    assert "360 ticks" in html                     # backstop 90 pts / 0.25
    assert "$4,500" in html                        # y el $ del estudio
    assert "Catálogo incompleto" not in html


@pytest.mark.asyncio
async def test_renombrar_y_eliminar_estrategia(client: AsyncClient,
                                               dirs: Path) -> None:
    """D — editar (renombrar) y eliminar la estrategia del ESTUDIO: mueve la
    carpeta del motor y actualiza el manifest; eliminar borra carpeta +
    entrada + CSV subido (solo upload_*)."""
    subido = dirs / "ListaDeOperaciones" / "upload_ES5m_Test_1.csv"
    subido.write_text("x", encoding="utf-8")
    _manifest_es(dirs, csv=subido.as_posix())
    _seed_motor(dirs)

    # renombrar → carpeta movida + manifest actualizado
    r = await client.post("/ui/riesgo/estrategia/renombrar",
                          json={"strategy": "ES5m_Test",
                                "nuevo_id": "ES5m_Renombrada"})
    assert r.status_code == 200, r.text
    assert r.json()["clave"] == "ES_Renombrada"
    assert (dirs / "MotorRiesgo" / "ES_Renombrada").exists()
    assert not (dirs / "MotorRiesgo" / "ES_Test").exists()
    entries = json.loads((dirs / "REPORTES" / "lab_manifest.json")
                         .read_text(encoding="utf-8"))["entries"]
    assert "ES5m_Renombrada" in entries and "ES5m_Test" not in entries
    r = await client.get("/ui/riesgo?strategy=ES5m_Renombrada")
    assert r.status_code == 200 and "ES_Renombrada" in r.text

    # renombrar a un id ya existente → 409
    _write_lab_manifest(dirs, {**entries, "ES5m_Otra": {
        "instrument": "ES", "csv": "x.csv", "confirmed": True}})
    r = await client.post("/ui/riesgo/estrategia/renombrar",
                          json={"strategy": "ES5m_Renombrada",
                                "nuevo_id": "ES5m_Otra"})
    assert r.status_code == 409

    # eliminar → carpeta + entrada + csv subido fuera
    r = await client.delete("/ui/riesgo/estrategia",
                            params={"strategy": "ES5m_Renombrada"})
    assert r.status_code == 200
    assert not (dirs / "MotorRiesgo" / "ES_Renombrada").exists()
    assert not subido.exists()
    entries = json.loads((dirs / "REPORTES" / "lab_manifest.json")
                         .read_text(encoding="utf-8"))["entries"]
    assert "ES5m_Renombrada" not in entries


@pytest.mark.asyncio
async def test_eliminar_listado_csv_conserva_estrategia(
    client: AsyncClient, dirs: Path
) -> None:
    """D — la ficha Datos permite ELIMINAR el .csv (no solo agregar): borra
    la carpeta del motor + el CSV subido, pero la estrategia SIGUE en el
    manifest lista para reemplazar. Un export original (no upload_*) jamás
    se borra."""
    original = dirs / "ListaDeOperaciones" / "Lux_CME_MINI_ES1!_orig.csv"
    original.write_text("x", encoding="utf-8")
    _manifest_es(dirs, csv=original.as_posix())
    _seed_motor(dirs)

    r = await client.delete("/ui/riesgo/datos",
                            params={"strategy": "ES5m_Test"})
    assert r.status_code == 200
    j = r.json()
    assert j["motor_borrado"] is True
    assert j["csv_borrado"] is False              # original: NO se toca
    assert original.exists()
    assert not (dirs / "MotorRiesgo" / "ES_Test").exists()
    entries = json.loads((dirs / "REPORTES" / "lab_manifest.json")
                         .read_text(encoding="utf-8"))["entries"]
    assert "ES5m_Test" in entries                 # la estrategia sigue
    r = await client.get("/ui/riesgo?strategy=ES5m_Test")
    assert "Sin listado integrado" in r.text      # lista para reemplazar


# ---------------------------------------------------------------------------
# P1-4 — lock de integrar por clave (dos subidas simultáneas no compiten)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_integrar_serializado_por_clave(
    client: AsyncClient, dirs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversarial: dos subidas CONCURRENTES de la misma estrategia deben
    serializarse (sin lock, los dos integrar corren solapados y compiten
    por master.csv/manifest — last-writer-wins silencioso)."""
    _write_lab_manifest(dirs, {"ES5m_Test": {
        "instrument": "ES", "csv": "ListaDeOperaciones/x.csv",
        "confirmed": True}})
    estado = {"activos": 0, "max": 0}

    async def fake_motor(cmd):
        estado["activos"] += 1
        estado["max"] = max(estado["max"], estado["activos"])
        await asyncio.sleep(0.15)
        estado["activos"] -= 1
        return 0, "ok"
    monkeypatch.setattr(rr, "_run_motor", fake_motor)

    async def sube():
        return await client.post(
            "/ui/riesgo/upload", data={"strategy": "ES5m_Test"},
            files={"file": ("Lux_CME_MINI_ES1!_2026-07-04_ab.csv",
                            CSV_OK.encode(), "text/csv")})

    r1, r2 = await asyncio.gather(sube(), sube())
    assert r1.status_code == 200 and r2.status_code == 200
    assert estado["max"] == 1, "integrar NO está serializado por clave"


# ---------------------------------------------------------------------------
# Subida (existente + alta NUEVA) con el motor falso (el real, en el gated)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upload_validaciones(client: AsyncClient, dirs: Path) -> None:
    _write_lab_manifest(dirs, {"ES5m_Test": {
        "instrument": "ES", "csv": "ListaDeOperaciones/x.csv",
        "confirmed": True}})
    # id inválido
    r = await client.post("/ui/riesgo/upload", data={"strategy": "../x"},
                          files={"file": ("a_ES1!_b.csv", b"x", "text/csv")})
    assert r.status_code == 400
    # basura no parsea (estrategia del manifest)
    r = await client.post("/ui/riesgo/upload", data={"strategy": "ES5m_Test"},
                          files={"file": ("a.csv", b"no,es,luxalgo",
                                          "text/csv")})
    assert r.status_code == 400
    assert "no parsea" in r.json()["error"]
    # NUEVA sin símbolo detectable en el nombre original
    r = await client.post("/ui/riesgo/upload", data={"strategy": "NQ5m_Nueva"},
                          files={"file": ("foo.csv", CSV_OK.encode(),
                                          "text/csv")})
    assert r.status_code == 400
    assert "detectar el símbolo" in r.json()["error"]


@pytest.mark.asyncio
async def test_upload_nueva_da_de_alta_confirmada(
    client: AsyncClient, dirs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Alta de primera clase: estrategia fuera del manifest + CSV con el
    símbolo en el nombre → entra al manifest CONFIRMADA (el
    `lab_manifest --confirm` por UI) y el motor integra."""
    _write_lab_manifest(dirs, {})

    async def _fake_motor(cmd):
        return 0, "ok"
    monkeypatch.setattr(rr, "_run_motor", _fake_motor)

    r = await client.post(
        "/ui/riesgo/upload", data={"strategy": "ES5m_Nueva"},
        files={"file": ("Lux_CME_MINI_ES1!_2026-07-04_ab.csv",
                        CSV_OK.encode(), "text/csv")})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] and j["nueva"] is True
    assert j["clave"] == "ES_Nueva"
    entries = json.loads((dirs / "REPORTES" / "lab_manifest.json")
                         .read_text(encoding="utf-8"))["entries"]
    assert entries["ES5m_Nueva"]["instrument"] == "ES"
    assert entries["ES5m_Nueva"]["confirmed"] is True


@pytest.mark.asyncio
async def test_upload_error_del_motor_llega_claro(
    client: AsyncClient, dirs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Los errores del motor (doble-prefijo, cuadre al dólar) se muestran
    tal cual — y un integrar fallido NO toca el manifest."""
    _write_lab_manifest(dirs, {})

    async def _fake_motor(cmd):
        return 1, "⛔ CUADRE FALLIDO: Σ PnL parseado ≠ export"
    monkeypatch.setattr(rr, "_run_motor", _fake_motor)

    r = await client.post(
        "/ui/riesgo/upload", data={"strategy": "ES5m_Mala"},
        files={"file": ("Lux_CME_MINI_ES1!_2026-07-04_ab.csv",
                        CSV_OK.encode(), "text/csv")})
    assert r.status_code == 400
    assert "CUADRE FALLIDO" in r.json()["detalle"]
    entries = json.loads((dirs / "REPORTES" / "lab_manifest.json")
                         .read_text(encoding="utf-8"))["entries"]
    assert "ES5m_Mala" not in entries


# ---------------------------------------------------------------------------
# Calcular (job en segundo plano + polling) y artefactos
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_calcular_job_y_polling(client: AsyncClient, dirs: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    _write_lab_manifest(dirs, {"ES5m_Test": {
        "instrument": "ES", "csv": "ListaDeOperaciones/x.csv",
        "confirmed": True}})
    # sin master integrado → 409
    r = await client.post("/ui/riesgo/calcular",
                          json={"strategy": "ES5m_Test"})
    assert r.status_code == 409

    _seed_motor(dirs, con_estudio=False)
    monkeypatch.setattr(rr, "_calc_cmd",
                        lambda clave: [sys.executable, "-c", "print('ok')"])
    r = await client.post("/ui/riesgo/calcular",
                          json={"strategy": "ES5m_Test"})
    assert r.status_code == 202
    for _ in range(50):
        s = await client.get("/ui/riesgo/calcular/status?strategy=ES5m_Test")
        if s.json()["status"] != "running":
            break
        await asyncio.sleep(0.1)
    assert s.json()["status"] == "done"


@pytest.mark.asyncio
async def test_heatmap_y_reporte(client: AsyncClient, dirs: Path) -> None:
    _write_lab_manifest(dirs, {"ES5m_Test": {
        "instrument": "ES", "csv": "ListaDeOperaciones/x.csv",
        "confirmed": True}})
    _seed_motor(dirs)
    r = await client.get("/ui/riesgo/heatmap?strategy=ES5m_Test")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    r = await client.get("/ui/riesgo/reporte?strategy=ES5m_Test")
    assert r.status_code == 200 and "Riesgo" in r.text
    # llave fuera del manifest → 400 (anti-traversal incluido)
    r = await client.get("/ui/riesgo/heatmap?strategy=..%2Fx")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# ACEPTACIÓN (datos reales, gated): subir → Calcular → VER, sin terminal
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAY_DATOS, reason="datos reales ES no disponibles")
@pytest.mark.asyncio
async def test_aceptacion_es_end_to_end(client: AsyncClient,
                                        dirs: Path) -> None:
    """El criterio: desde la pestaña, sobre ES, subir el export, Calcular,
    y VER la línea base ($28,175), la comparación y el heatmap."""
    _write_lab_manifest(dirs, {})
    csv_real = Path(sorted(_ES_CSV)[-1])          # export 2026-07-04

    # 1) alta NUEVA subiendo el export real → integrar REAL (subproceso)
    r = await client.post(
        "/ui/riesgo/upload", data={"strategy": "ES5m_UiTest"},
        files={"file": (csv_real.name, csv_real.read_bytes(), "text/csv")})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["nueva"] is True and j["n_trades"] == 120

    # 2) Calcular REAL en segundo plano + polling
    r = await client.post("/ui/riesgo/calcular",
                          json={"strategy": "ES5m_UiTest"})
    assert r.status_code == 202
    for _ in range(240):
        s = await client.get(
            "/ui/riesgo/calcular/status?strategy=ES5m_UiTest")
        if s.json()["status"] != "running":
            break
        await asyncio.sleep(1.0)
    assert s.json()["status"] == "done", s.json().get("tail", "")[-500:]

    # 3) VER: línea base, comparación base→recomendada, heatmap
    r = await client.get("/ui/riesgo?strategy=ES5m_UiTest")
    assert r.status_code == 200
    html = r.text
    assert "28,175" in html                        # la línea base de ES
    assert "CON CONFIG recomendada" in html
    assert "PF OOS" in html
    hm = await client.get("/ui/riesgo/heatmap?strategy=ES5m_UiTest")
    assert hm.status_code == 200
    assert hm.headers["content-type"] == "image/png"
