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


def _manifest_es(dirs: Path, csv: str = "ListaDeOperaciones/x.csv") -> None:
    _write_lab_manifest(dirs, {"ES5m_Test": {
        "instrument": "ES", "csv": csv, "confirmed": True}})


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
# L7b — la PÁGINA v1 (render del estudio, banners, tarjetas, config-a-aplicar,
# heatmap colapsado, espejo de estudios) se RETIRÓ. Su lógica de datos la cubren
# las suites del motor (recrear bit-a-bit) y su presencia en el DETALLE la cubren
# test_estrategias_l1 (Luxy) y test_perfiles_l4. Aquí queda: el REDIRECT (P3) y
# los ENDPOINTS que sobreviven (upload/integrar/calcular/cuenta/renombrar/borrar/
# heatmap/reporte). El motor, los datos y los estudios quedan INTACTOS.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_riesgo_v1_redirect_p3(client: AsyncClient, dirs: Path) -> None:
    """/ui/riesgo redirige (no renderiza): sin `strategy` → /ui/strategies; con
    `?strategy=X` → detalle de X (sub-pestaña Luxy). Rollback = git revert."""
    _manifest_es(dirs)
    _seed_motor(dirs)
    r = await client.get("/ui/riesgo")
    assert r.status_code == 302 and r.headers["location"] == "/ui/strategies"
    r = await client.get("/ui/riesgo?strategy=ES5m_Test")
    assert r.status_code == 302
    assert r.headers["location"] == "/ui/strategies/ES5m_Test"


@pytest.mark.asyncio
async def test_cuenta_editable_persiste(client: AsyncClient,
                                        dirs: Path) -> None:
    """L7b — el ENDPOINT de cuenta sobrevive (la página v1 que la pintaba se
    retiró). Rango inválido → 400 sin persistir; válido → 200 y persiste. El
    RECÓMPUTO de la protección (`proteccion_para_cuenta`, puro) lo cubren los
    test_proteccion_* de test_robs2."""
    _manifest_es(dirs)
    est = json.loads(json.dumps(ESTUDIO))
    est["proteccion"] = PROTECCION
    _seed_motor(dirs, estudio=est)

    # rango inválido → 400 y NO persiste (sigue el default)
    r = await client.post("/ui/riesgo/cuenta", json={"cuenta_usd": 1.0})
    assert r.status_code == 400
    assert rr._leer_cuenta() == rr.CUENTA_DEFAULT

    # válido → 200 y persiste (global)
    r = await client.post("/ui/riesgo/cuenta", json={"cuenta_usd": 200_000})
    assert r.status_code == 200
    assert rr._leer_cuenta() == 200_000.0


# L7b — 'Configuración a aplicar' era render de la página v1 (retirada). El
# JSON de activación aplicable vive ahora en Luxy (`activacion_from_study`) y el
# aplicar supervisado en test_luxy_aplicar_l5.py; las unidades por Symbol Mapper
# en test_perfiles_l4.py.


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
    # L7b — el estudio movido sigue legible por el helper (la página v1 se retiró)
    assert rr._motor_manifest("ES_Renombrada") is not None

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
    # L7b — la carpeta del motor se borró (lista para reemplazar); el helper lo
    # confirma sin la página v1 (retirada).
    assert rr._motor_manifest("ES_Test") is None


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
    """El criterio, L7b: sobre ES, subir el export y Calcular (endpoints vivos),
    y VER la línea base ($28,175) en el estudio PERSISTIDO + el heatmap por su
    endpoint. (La página v1 que lo pintaba se retiró; el dato es el mismo.)"""
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

    # 3) VER (L7b — sin página v1): el estudio PERSISTIDO trae la línea base de
    # ES ($28,175); el heatmap sigue sirviéndose por su endpoint.
    est = rr._latest_estudio(rr.clave_de("ES5m_UiTest", "ES"))
    assert est is not None
    assert round(est["linea_base"]["total"]["net_usd"]) == 28175
    hm = await client.get("/ui/riesgo/heatmap?strategy=ES5m_UiTest")
    assert hm.status_code == 200
    assert hm.headers["content-type"] == "image/png"
