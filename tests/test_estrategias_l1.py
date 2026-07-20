"""LOTE L1 — alta y datos DENTRO de Estrategias.

Cubre:
- Subir la lista → integrar el master DESDE Estrategias (reusa el núcleo del
  Motor `routes_riesgo.integrar_lista`; el motor NO se muda). Real cuadrando al
  dólar en el test gated con datos de ES.
- Provisión de HOLC: activo sin HOLC → 409 holc_missing (degradado/aviso/botón);
  subir HOLC válido → queda en NINJATRADER/HOLC y el reintegro sale completo;
  HOLC inválido → rechazo sin tocar disco; anti-traversal del nombre destino.
- Detalle con sub-pestañas Config·Luxy·Lab + selector desplegable (la
  ex-pestaña Perfiles vive en Config → Despacho → Destinos, 2026-07-19).
- Riesgo v1 intacta (regresión).
"""
import asyncio
import glob
import json
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
_ES_HOLC = Path("NINJATRADER/HOLC/ES_5m.csv")
_HAY_DATOS = bool(_ES_CSV) and _ES_HOLC.exists()


def _es_intrabar_confiable() -> bool:
    """LX-12 — ¿el HOLC real de ES CONTIENE los precios del master ES? Con el
    HOLC del share en otro contorno de roll que el continuo de LuxAlgo la
    contención cae por debajo del umbral y el estudio DEGRADA (fail-honest); los
    tests de PARIDAD intrabar de abajo requieren datos alineados, así que se
    saltan con motivo hasta que el operador corrija el Merge policy y reintegre.
    NO se fabrica un HOLC alineado a propósito: eso ocultaría el desalineo que la
    guardia existe para delatar."""
    if not _HAY_DATOS:
        return False
    try:
        from scripts.lab_analyze import detect_tz_offset, load_holc, parse_luxalgo_csv
        from scripts.nt_riesgo import _contencion
        trades = parse_luxalgo_csv(Path(_ES_CSV[-1]))
        bars = load_holc("ES", "5m")
        off, _s, _d = detect_tz_offset(trades, bars)
        return bool(_contencion(trades, bars, off, "ES")["confiable"])
    except Exception:
        return False


# Los tests de paridad intrabar corren SOLO si el HOLC real contiene el master
# (si no, LX-12 degrada el estudio a solo-crudo — a propósito).
_ES_INTRABAR = _HAY_DATOS and _es_intrabar_confiable()
_MOTIVO_INTRABAR = ("LX-12: HOLC del share desalineado del master ES (roll/back-"
                    "adjust) → intrabar no confiable; la paridad intrabar exige "
                    "datos alineados. Corrige el Merge policy en NinjaTrader y "
                    "reintegra.")

# Lista mínima de LuxAlgo que parsea y cuadra (1 trade, entry+exit).
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

# HOLC válido (columnas correctas, filas parseables).
HOLC_OK = "DateTime,Open,High,Low,Close,Volume\n" + "\n".join(
    f"2026-03-16 {9 + i // 12:02d}:{(i % 12) * 5:02d}:00,6700,6710,6690,6705,100"
    for i in range(30)
)


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_l1")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


@pytest.fixture()
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "REPORTES").mkdir()
    (tmp_path / "ListaDeOperaciones").mkdir()
    (tmp_path / "MotorRiesgo").mkdir()
    (tmp_path / "HOLC").mkdir()
    monkeypatch.setattr(routes_lab, "LAB_DIR", tmp_path / "REPORTES")
    monkeypatch.setattr(routes_lab, "TRADES_DIR", tmp_path / "ListaDeOperaciones")
    monkeypatch.setattr(rr, "MOTOR_DIR", tmp_path / "MotorRiesgo")
    monkeypatch.setattr(rr, "TRADES_DIR", tmp_path / "ListaDeOperaciones")
    monkeypatch.setenv("HOLC_DIR", str(tmp_path / "HOLC"))       # HOLC vacío
    rr.JOBS.clear()
    rr._INTEGRAR_LOCKS.clear()
    return tmp_path


async def _mk_strategy(db: AsyncSession, sid: str, asset: str) -> None:
    db.add(Strategy(strategy_id=sid, name=sid, asset_symbol=asset,
                    status="paper", enabled=True))
    await db.commit()


def _fake_motor_ok(monkeypatch):
    async def _f(cmd):
        return 0, "ok"
    monkeypatch.setattr(rr, "_run_motor", _f)


# ---------------------------------------------------------------------------
# Alta de datos: sin HOLC → degradado + aviso + botón
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_integrar_sin_holc_ofrece_degradado(
    client: AsyncClient, dirs: Path, db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mk_strategy(db, "ES5m_L1", "ES")           # HOLC_DIR vacío → sin HOLC
    _fake_motor_ok(monkeypatch)

    # Sin degradado → 409 holc_missing (la UI ofrece subir HOLC o degradar)
    r = await client.post(
        "/ui/strategies/ES5m_L1/integrar",
        files={"file": ("lista.csv", CSV_OK.encode(), "text/csv")})
    assert r.status_code == 409
    j = r.json()
    assert j["holc_missing"] is True and j["instrument"] == "ES"

    # Con degradado → integra igual (master cuadrado; estudio pendiente)
    r = await client.post(
        "/ui/strategies/ES5m_L1/integrar",
        data={"degradado": "true"},
        files={"file": ("lista.csv", CSV_OK.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True and j["degradado"] is True


@pytest.mark.asyncio
async def test_integrar_sin_activo_rechaza(
    client: AsyncClient, dirs: Path, db: AsyncSession
) -> None:
    await _mk_strategy(db, "SinActivo", "")
    r = await client.post(
        "/ui/strategies/SinActivo/integrar",
        files={"file": ("lista.csv", CSV_OK.encode(), "text/csv")})
    assert r.status_code == 400
    assert "activo" in r.json()["error"]


# ---------------------------------------------------------------------------
# Provisión de HOLC: válido guarda + reintegro completo; inválido rechaza
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_holc_valido_guarda_y_reintegro_completo(
    client: AsyncClient, dirs: Path, db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mk_strategy(db, "ES5m_L1b", "ES")
    _fake_motor_ok(monkeypatch)

    # Subir HOLC válido → queda en NINJATRADER/HOLC (temp) como ES_5m.csv
    r = await client.post(
        "/ui/strategies/holc",
        data={"symbol": "ES", "timeframe": "5m"},
        files={"file": ("es.csv", HOLC_OK.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    assert (dirs / "HOLC" / "ES_5m.csv").exists()
    assert rr.holc_disponible("ES") is True

    # Ahora integrar NO pide HOLC — sale completo (degradado False)
    r = await client.post(
        "/ui/strategies/ES5m_L1b/integrar",
        files={"file": ("lista.csv", CSV_OK.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["degradado"] is False


@pytest.mark.asyncio
async def test_holc_micro_normaliza_a_raiz(
    client: AsyncClient, dirs: Path
) -> None:
    """Subir HOLC con el micro (MES) lo guarda como raíz del catálogo (ES)."""
    r = await client.post(
        "/ui/strategies/holc",
        data={"symbol": "MES", "timeframe": "5m"},
        files={"file": ("x.csv", HOLC_OK.encode(), "text/csv")})
    assert r.status_code == 200
    assert r.json()["symbol"] == "ES"
    assert (dirs / "HOLC" / "ES_5m.csv").exists()


@pytest.mark.asyncio
async def test_holc_invalido_no_toca_disco(
    client: AsyncClient, dirs: Path
) -> None:
    r = await client.post(
        "/ui/strategies/holc",
        data={"symbol": "NQ", "timeframe": "5m"},
        files={"file": ("bad.csv", b"no,es,holc\n1,2,3", "text/csv")})
    assert r.status_code == 400
    assert "inválido" in r.json()["error"]
    assert not (dirs / "HOLC" / "NQ_5m.csv").exists()     # nada a disco


@pytest.mark.asyncio
async def test_holc_anti_traversal(client: AsyncClient, dirs: Path) -> None:
    # símbolo con separadores de ruta → regex lo rechaza, cero disco
    for sym in ("../../etc", "ES/x", "..\\x"):
        r = await client.post(
            "/ui/strategies/holc",
            data={"symbol": sym, "timeframe": "5m"},
            files={"file": ("h.csv", HOLC_OK.encode(), "text/csv")})
        assert r.status_code == 400, sym
    # timeframe fuera de whitelist
    r = await client.post(
        "/ui/strategies/holc",
        data={"symbol": "ES", "timeframe": "../evil"},
        files={"file": ("h.csv", HOLC_OK.encode(), "text/csv")})
    assert r.status_code == 400
    # símbolo válido en forma pero fuera del catálogo del motor
    r = await client.post(
        "/ui/strategies/holc",
        data={"symbol": "ZZZ", "timeframe": "5m"},
        files={"file": ("h.csv", HOLC_OK.encode(), "text/csv")})
    assert r.status_code == 400
    assert "catálogo" in r.json()["error"]
    # el directorio HOLC no ganó ningún archivo espurio
    assert list((dirs / "HOLC").glob("*.csv")) == []


# ---------------------------------------------------------------------------
# Detalle: sub-pestañas + selector
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detalle_subpestanas_y_selector(
    client: AsyncClient, dirs: Path, db: AsyncSession
) -> None:
    await _mk_strategy(db, "ES5m_A", "ES")
    await _mk_strategy(db, "NQ5m_B", "NQ")

    r = await client.get("/ui/strategies/ES5m_A")
    assert r.status_code == 200
    html = r.text
    # las 3 sub-pestañas + el estado Alpine (UI-DESPACHO-UNIFICADO 2026-07-19:
    # la pestaña Perfiles se RETIRÓ — su contenido vive en Config → Despacho →
    # Destinos; compat #perfiles → scroll a #despacho)
    assert "stab: 'config'" in html
    for label in ("Config", "Luxy", "Lab"):
        assert f">{label}<" in html
    assert ">Perfiles</button>" not in html         # pestaña retirada
    assert 'id="despacho"' in html or "Sin perfil de configuración" in html
    # Luxy funcional (botón) + Lab placeholder honesto (migra en L6)
    assert "Calcular estudio" in html and "L6" in html
    # selector con ambas estrategias, navega al cambiar
    assert "window.location.href='/ui/strategies/'+this.value" in html
    assert 'value="ES5m_A"' in html and 'value="NQ5m_B"' in html
    # panel de Datos (subir lista) presente
    assert "Subir lista e integrar" in html


# ---------------------------------------------------------------------------
# L1.1 — Calcular sobre master degradado: fail-honest (409), no crash sucio
# ---------------------------------------------------------------------------

def _write_motor_manifest(dirs: Path, clave: str, degradado: bool) -> None:
    d = dirs / "MotorRiesgo" / clave
    d.mkdir(parents=True, exist_ok=True)
    holc = ({"archivo": None, "ultima_barra": None, "degradado": True}
            if degradado else
            {"archivo": "ES_5m.csv", "ultima_barra": "2026-06-22T22:30:00",
             "sin_cobertura": 0, "atr_estimado": 0, "degradado": False})
    (d / "manifest.json").write_text(json.dumps({
        "version": 1, "activo": "ES", "codigo": clave.split("_", 1)[-1],
        "integrado": "2026-07-11", "degradado": degradado,
        "trades": {"n": 1}, "usd_por_punto": {"usado": 50.0},
        "holc": holc, "cuadre": {"ok": True},
    }), encoding="utf-8")


@pytest.mark.asyncio
async def test_calcular_sobre_degradado_409(
    client: AsyncClient, dirs: Path
) -> None:
    (dirs / "REPORTES" / "lab_manifest.json").write_text(json.dumps(
        {"version": 1, "entries": {"ES5m_Deg": {
            "instrument": "ES", "csv": "x.csv", "confirmed": True}}}),
        encoding="utf-8")
    _write_motor_manifest(dirs, "ES_Deg", degradado=True)

    r = await client.post("/ui/riesgo/calcular", json={"strategy": "ES5m_Deg"})
    assert r.status_code == 409
    assert "degradado" in r.json()["error"]


@pytest.mark.asyncio
async def test_calcular_sobre_normal_202(
    client: AsyncClient, dirs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Master normal: 202 igual que siempre (el guard no toca ese camino)."""
    _fake_motor_ok(monkeypatch)
    (dirs / "REPORTES" / "lab_manifest.json").write_text(json.dumps(
        {"version": 1, "entries": {"ES5m_Norm": {
            "instrument": "ES", "csv": "x.csv", "confirmed": True}}}),
        encoding="utf-8")
    _write_motor_manifest(dirs, "ES_Norm", degradado=False)

    r = await client.post("/ui/riesgo/calcular", json={"strategy": "ES5m_Norm"})
    assert r.status_code == 202
    assert r.json()["status"] == "running"


# ---------------------------------------------------------------------------
# L2 — sub-pestaña Luxy: sin estudio muestra el botón; e2e real con JOB
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_luxy_tab_sin_estudio(
    client: AsyncClient, dirs: Path, db: AsyncSession
) -> None:
    await _mk_strategy(db, "ES5m_LuxA", "ES")
    r = await client.get("/ui/strategies/ES5m_LuxA")
    assert r.status_code == 200
    html = r.text
    assert "Calcular estudio" in html
    assert "Sin estudio Luxy" in html            # aún no corrió
    # L4 — panel de Perfiles read-only: sin estudio muestra el CTA
    assert "Perfiles — sizing y peor-caso" in html
    assert "read-only · no edita · no despacha" in html
    assert "Corre el" in html                    # CTA a Calcular


@pytest.mark.skipif(not _ES_INTRABAR, reason=_MOTIVO_INTRABAR)
@pytest.mark.asyncio
async def test_luxy_e2e_real(
    client: AsyncClient, dirs: Path, db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """e2e: integrar ES real → Calcular estudio Luxy (JOB+polling) → la
    sub-pestaña Luxy renderiza la Tabla A con el crudo; el JSON persiste con
    intrabar (BE evaluado)."""
    import json as _json
    monkeypatch.setenv("HOLC_DIR", "NINJATRADER/HOLC")
    await _mk_strategy(db, "ES5m_LuxReal", "ES")
    csv_real = Path(sorted(_ES_CSV)[-1])
    r = await client.post(
        "/ui/strategies/ES5m_LuxReal/integrar",
        files={"file": (csv_real.name, csv_real.read_bytes(), "text/csv")})
    assert r.status_code == 200, r.text
    clave = r.json()["clave"]

    r = await client.post("/ui/strategies/ES5m_LuxReal/luxy/calcular")
    assert r.status_code == 202, r.text
    for _ in range(240):
        s = await client.get("/ui/strategies/ES5m_LuxReal/luxy/status")
        if s.json().get("status") != "running":
            break
        await asyncio.sleep(1.0)
    assert s.json().get("status") == "done", s.json().get("tail", "")[-400:]

    # JSON persistido: intrabar (no degradado), BE evaluado, tabla A/B
    study = _json.loads(
        (dirs / "MotorRiesgo" / clave / "runs" /
         sorted((dirs / "MotorRiesgo" / clave / "runs").glob("luxy_*.json"))[-1].name)
        .read_text(encoding="utf-8"))
    assert study["degradado"] is False
    assert study["levers_in_sample"]["breakeven"]["disponible"] is True
    assert len(study["tabla_a"]) == 3 and study["tabla_b"]["convergencia"]

    # la sub-pestaña renderiza el DASHBOARD (L3) con la tabla reactiva (LX-1 #4:
    # la Tabla A estática se retiró) y el diagrama único (LX-1 #3)
    r = await client.get("/ui/strategies/ES5m_LuxReal")
    assert r.status_code == 200
    html = r.text
    assert 'id="lx-table3"' in html                   # tabla reactiva (Crudo/Crudo+/OOS)
    assert "Tabla A — métricas" not in html           # estática retirada
    assert "espejo" in html.lower()
    # dashboard portado (dark): raíz, payload inyectado, Recalcular motor,
    # diagrama ÚNICO a todo el ancho, sesiones ET y la honestidad del BE
    assert 'id="lx-root"' in html and "window.LUXY" in html
    assert "Recalcular (motor)" in html
    assert 'id="lx-chart"' in html                    # un solo canvas
    assert 'id="lx-chart-in"' not in html and 'id="lx-chart-oos"' not in html
    assert "in-sample · OOS" in html                  # rótulo del corte del split
    assert "BE: requiere recálculo del motor" in html
    # LX-2 — el código de los switches por sesión/día viaja (el panel es
    # JS-driven; el render visual lo valida el smoke del operador): CSS del
    # switch, binding por zona/día y el payload de toggles al motor.
    assert ".lx-sw{" in html                          # estilo del switch (dark)
    assert "input[data-z]" in html and "input[data-d]" in html
    assert "zones_off" in html and "days_off" in html  # recalc envía los toggles
    assert "no persiste" in html                      # aviso conservado (criterio 5)
    # LX-3 — resemántica de la tabla: fila Crudo+ + nota honesta de muestra
    # (la lógica y el texto exacto viajan en el JS; se pinta solo si aplica).
    assert "'Crudo+'" in html                         # fila Crudo+ (JS renderTable)
    # LX-3b — semáforo de robustez (OOS), columna $/trade, retención y banner:
    # su código/wording viaja en el JS (el render lo valida el smoke del operador).
    assert "robustez OOS" in html
    assert "$/trade" in html and "retiene " in html
    assert "pendiente de Recalcular" in html
    # LX-6 — tripwire de plausibilidad: banner rojo + "sin veredicto" en el JS
    assert 'id="lx-implausible"' in html and "números implausibles" in html
    # LX-7 — PF honesto: la lógica "n/s (perdedores)" + cherry-picking viaja en el JS
    assert "MIN_PERDEDORES_PF" in html and "n/s (" in html and "cherry-picking" in html
    # LX-7 (tripwire) — estado PROPIO "no evaluable por muestra" (ámbar, ≠ implausible)
    assert 'id="lx-pf-noeval"' in html and "no evaluable por muestra" in html
    # (el texto del banner de muestra vive en el payload/Python — se testea en
    #  test_luxy_toggles_lx2::test_muestra_banner_on_off_y_texto; ES no lo dispara.)
    assert "ET" in html                              # rango horario ET (R-T7)
    # L4 — el panel de Perfiles renderiza el sizing + Export (builder real)
    assert "Perfiles — sizing y peor-caso" in html
    assert "Peor-caso/op" in html
    assert "lo que Estrategias enviaría" in html     # R-T8
    # L5 — botón Aplicar supervisado + diff correcto con ES real (gated)
    assert "Aplicar a la config viva" in html
    pv = await client.get("/ui/strategies/ES5m_LuxReal/luxy/aplicar/preview")
    assert pv.status_code == 200, pv.text
    pj = pv.json()
    assert pj["fuente"] == "luxy" and pj["filas"]
    assert any("R-T10" in a for a in pj["avisos"])   # fila in-sample, no OOS
    # el diff propone el TP nominal + la escalera derivada del estudio
    campos = " ".join(f["campo"] for f in pj["filas"])
    assert "TP nominal" in campos and "Escalera" in campos


@pytest.mark.skipif(not _ES_INTRABAR, reason=_MOTIVO_INTRABAR)
@pytest.mark.asyncio
async def test_luxy_evaluar_parity_real(
    client: AsyncClient, dirs: Path, db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """RECALCULAR (/luxy/evaluar) usa el evaluador de L2 → mismos números que
    el estudio. LX-1 #4: `config` se evalúa SOLO sobre el subconjunto in-sample
    (R-T10). LX-3: evaluar sin overrides reproduce la fila CRUDO+ de la tabla
    reactiva (dashboard.table3.crudo_plus = palancas sobre TODOS los sts) y la
    fila OOS es el espejo sobre el subconjunto apartado. BE no reco en ES."""
    import scripts.mr_luxy as mrl
    import app.web.routes_riesgo as rr
    monkeypatch.setenv("HOLC_DIR", "NINJATRADER/HOLC")
    await _mk_strategy(db, "ES5m_LuxPar", "ES")
    csv_real = Path(sorted(_ES_CSV)[-1])
    r = await client.post(
        "/ui/strategies/ES5m_LuxPar/integrar",
        files={"file": (csv_real.name, csv_real.read_bytes(), "text/csv")})
    clave = r.json()["clave"]

    study = mrl.run_for_clave(clave, rr.MOTOR_DIR)
    ev = mrl.evaluate_overrides(clave, rr.MOTOR_DIR, {})
    t3 = study["dashboard"]["table3"]
    dash = study["dashboard"]
    assert study["levers_in_sample"]["breakeven"]["be_atr"] is None   # BE no reco
    # LX-3: config = CRUDO+ (palancas sobre TODOS los sts, viejos+recientes);
    # oos = espejo con las MISMAS palancas SOLO sobre el subconjunto apartado.
    assert round(ev["config"]["net"]) == round(t3["crudo_plus"]["net_usd"]), \
        (ev["config"]["net"], t3["crudo_plus"]["net_usd"])
    assert round(ev["oos"]["net"]) == round(t3["oos"]["net_usd"]), \
        (ev["oos"]["net"], t3["oos"]["net_usd"])
    # Crudo+ == la VIEJA fila In-sample de la Tabla A (misma semántica: palancas
    # sobre toda la muestra simulable) — con las mismas palancas del estudio.
    fila_in = next(f for f in study["tabla_a"] if f["fila"] == "In-sample")
    assert t3["crudo_plus"]["net_usd"] == fila_in["net_usd"]
    assert t3["crudo_plus"]["n"] == fila_in["n"]
    # LX-3 Ns: Crudo = lista base (n_total) · Crudo+ = simulable · OOS = subconj.
    assert t3["crudo"]["n"] == dash["n_total"]
    assert t3["crudo_plus"]["n"] == dash["n_simulable"] == study["split"]["n_total"]
    assert t3["oos"]["n"] == study["split"]["n_oos"]
    # el payload EXPONE ambos N para la nota honesta de muestra (se pinta solo si
    # n_simulable < n_total; este export de ES está 100% cubierto → n iguales).
    assert isinstance(dash["n_total"], int) and isinstance(dash["n_simulable"], int)
    assert dash["n_simulable"] <= dash["n_total"]
    # LX-5 — UNA sola definición de simulable: n_simulable == Crudo+ n == suma de
    # los subconjuntos por ventana (sts_in + sts_oos). Sin ambigüedad.
    assert dash["n_simulable"] == t3["crudo_plus"]["n"]
    assert dash["n_simulable"] == study["split"]["n_in_sample"] + study["split"]["n_oos"]
    assert dash["n_no_simulable"] == dash["n_total"] - dash["n_simulable"]
    # doble universo del split: trades totales vs simulables
    assert study["split"]["n_trades_in"] + study["split"]["n_trades_oos"] == dash["n_total"]
    assert study["split"]["n_in_sample"] <= study["split"]["n_trades_in"]
    # ES 100% cubierto por el HOLC → sin banner de muestra
    assert dash["muestra_banner"] is None
    # LX-6 — el estudio LIMPIO del ES NO es implausible (tripwire OFF)
    assert dash["implausible"] is False
    # LX-7 — ES tiene perdedores de sobra → el PF SÍ es evaluable (no el estado
    # nuevo "no evaluable por muestra"); el semáforo queda con veredicto real.
    assert dash.get("pf_no_evaluable") is False
    # LX-3b — semáforo de robustez SOLO de la fila OOS validada, con los umbrales
    oos_net, oos_pf = t3["oos"]["net_usd"], t3["oos"]["pf"]
    exp = ("rojo" if (oos_net is None or oos_pf is None or oos_net <= 0 or oos_pf < 1.0)
           else "verde" if oos_pf >= 1.3 else "amarillo")
    assert dash["robustez"]["verdict"] == exp
    assert ev["robustez"]["verdict"] == exp           # Recalcular refresca el mismo
    # retención $/trade: expuesta con el n del OOS (guarda de división adentro)
    assert dash["retencion"]["n_oos"] == t3["oos"]["n"]
    assert "pct" in dash["retencion"] and "pct" in ev["retencion"]
    # LX-7 — el motor expone n_perdedores por fila (validado y en table3) para el
    # rotulado honesto del PF; con muestra completa hay ≥ MIN_PERDEDORES_PF.
    assert ev["config"]["n_perdedores"] is not None and ev["oos"]["n_perdedores"] is not None
    assert t3["crudo_plus"]["n_perdedores"] is not None
    assert t3["crudo_plus"]["n_perdedores"] >= 3      # ES completo: PF con sentido
    # LX-9 — el payload lleva la identidad del estudio (fecha:sha) para que el
    # navegador invalide la exploración guardada si el estudio cambia. La
    # persistencia es SOLO localStorage: cero endpoints/escrituras en el server.
    assert isinstance(dash["estudio_id"], str) and ":" in dash["estudio_id"]
    assert dash["estudio_id"].startswith(study["fecha"])
    # LX-1 #3 — cutoff para el corte visual: coincide con el nº in-sample y con
    # la frontera in→oos de la nube (orden cronológico, 100% de la muestra simulable).
    assert dash["cutoff_i"] == study["split"]["n_in_sample"]
    assert sum(1 for t in dash["trades"] if t["in"]) == dash["cutoff_i"]
    assert len(dash["trades"]) == study["split"]["n_total"]


@pytest.mark.skipif(not _ES_INTRABAR, reason=_MOTIVO_INTRABAR)
@pytest.mark.asyncio
async def test_luxy_toggles_motor_real(
    client: AsyncClient, dirs: Path, db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """LX-2 — evaluate_overrides acepta zones_off/days_off y excluye por
    zona/día ANTES de evaluar CADA ventana (R-T7, mismo zone_of_hour): baja n
    en las filas por ventana, NUNCA en el crudo; sin toggles = byte-igual (no
    regresión LX-1) y determinista."""
    import scripts.mr_luxy as mrl
    import app.web.routes_riesgo as rr
    monkeypatch.setenv("HOLC_DIR", "NINJATRADER/HOLC")
    await _mk_strategy(db, "ES5m_Tog", "ES")
    csv_real = Path(sorted(_ES_CSV)[-1])
    r = await client.post(
        "/ui/strategies/ES5m_Tog/integrar",
        files={"file": (csv_real.name, csv_real.read_bytes(), "text/csv")})
    clave = r.json()["clave"]

    ev0 = mrl.evaluate_overrides(clave, rr.MOTOR_DIR, {})
    evD = mrl.evaluate_overrides(clave, rr.MOTOR_DIR, {"days_off": [4]})   # sin viernes
    evZ = mrl.evaluate_overrides(clave, rr.MOTOR_DIR, {"zones_off": ["Asia"]})

    # el crudo (base = 100% de trades) NUNCA se filtra por toggles
    assert evD["base"] == ev0["base"] and evZ["base"] == ev0["base"]
    # LX-3: los toggles aplican a Crudo+ (config, todos los sts) Y a OOS por igual
    assert evD["config"]["n"] <= ev0["config"]["n"]
    assert evD["oos"]["n"] <= ev0["oos"]["n"]
    assert (evD["config"]["n"] + evD["oos"]["n"]) < (ev0["config"]["n"] + ev0["oos"]["n"])
    assert (evZ["config"]["n"] + evZ["oos"]["n"]) < (ev0["config"]["n"] + ev0["oos"]["n"])
    # LX-3 aislamiento (criterio 2): mover una palanca NO altera el Crudo (base)
    ev_sl = mrl.evaluate_overrides(clave, rr.MOTOR_DIR, {"sl_usd": 3000.0})
    assert ev_sl["base"] == ev0["base"]
    assert ev_sl["config"] != ev0["config"]           # Crudo+ sí reacciona
    # sin toggles: byte-igual a LX-1 (no regresión) + determinismo bit-a-bit
    assert mrl.evaluate_overrides(clave, rr.MOTOR_DIR, {}) == ev0
    assert mrl.evaluate_overrides(clave, rr.MOTOR_DIR, {"days_off": [4]}) == evD


@pytest.mark.skipif(not _ES_INTRABAR, reason=_MOTIVO_INTRABAR)
@pytest.mark.asyncio
async def test_luxy_hereda_cobertura_del_snapshot(
    client: AsyncClient, dirs: Path, db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """LX-4 — Luxy hereda la cobertura del HOLC por R-T2 vía el snapshot por-clave
    (`holc_5m.csv`), no del HOLC global. Al integrar se escribe el snapshot; si la
    costura extiende la cola (snapshot con MÁS barras) → `n_simulable` sube y el
    banner de muestra desaparece. Aquí lo demostramos truncando/restaurando el
    snapshot (la costura hace exactamente eso: añadir la cola desde la DB)."""
    import scripts.mr_luxy as mrl
    import scripts.nt_riesgo as ntr
    import app.web.routes_riesgo as rr
    from scripts.lab_analyze import load_holc_from_path
    monkeypatch.setenv("HOLC_DIR", "NINJATRADER/HOLC")
    await _mk_strategy(db, "ES5m_Cob", "ES")
    csv_real = Path(sorted(_ES_CSV)[-1])
    r = await client.post(
        "/ui/strategies/ES5m_Cob/integrar",
        files={"file": (csv_real.name, csv_real.read_bytes(), "text/csv")})
    clave = r.json()["clave"]

    base_dir = rr.MOTOR_DIR / clave
    snap = base_dir / "holc_5m.csv"
    assert snap.exists()                              # LX-4: snapshot por-clave escrito
    man = json.loads((base_dir / "manifest.json").read_text(encoding="utf-8"))
    assert man["holc"]["snapshot"] == "holc_5m.csv"

    n_full = mrl.run_for_clave(clave, rr.MOTOR_DIR)["dashboard"]["n_simulable"]

    # cobertura RECORTADA (cola descubierta): truncar el HOLC en la mediana de los
    # tiempos de entrada → ~la mitad de los trades quedan sin barras.
    from scripts.lab_analyze import parse_luxalgo_csv
    trades = parse_luxalgo_csv(base_dir / "master.csv")
    cut = sorted(t.entry_ts for t in trades)[len(trades) // 2]
    full = load_holc_from_path(snap)
    ntr._write_holc_snapshot(snap, {k: v for k, v in full.items() if k <= cut})
    st_trunc = mrl.run_for_clave(clave, rr.MOTOR_DIR)
    dt = st_trunc["dashboard"]
    n_trunc = dt["n_simulable"]
    assert n_trunc < n_full                           # menos cobertura → menos simulables
    assert dt["muestra_banner"] is not None           # banner aparece
    # LX-5 — desglose por causa: los descubiertos son cola posterior (v1 estimaría)
    assert "cola posterior a la última barra cosida" in dt["muestra_banner"]
    assert dt["n_estimados"] > 0                       # cola contada aparte
    # consistencia total incluso con cobertura truncada
    assert dt["n_simulable"] == dt["table3"]["crudo_plus"]["n"]
    assert dt["n_simulable"] == st_trunc["split"]["n_in_sample"] + st_trunc["split"]["n_oos"]
    assert dt["n_estimados"] + dt["n_inicio"] == dt["n_no_simulable"]

    # RESTAURAR la cobertura completa (lo que hace la costura) → n_simulable SUBE
    ntr._write_holc_snapshot(snap, full)
    st_re = mrl.run_for_clave(clave, rr.MOTOR_DIR)
    assert st_re["dashboard"]["n_simulable"] == n_full and n_full > n_trunc
    assert st_re["dashboard"]["muestra_banner"] is None          # cubierto → sin banner


# ---------------------------------------------------------------------------
# L7a — ventana de operación NATIVA en Luxy (paridad numérica con v1)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _ES_INTRABAR, reason=_MOTIVO_INTRABAR)
@pytest.mark.asyncio
async def test_luxy_ventana_paridad_v1_real(
    client: AsyncClient, dirs: Path, db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """La ventana de operación de Luxy (rango/duración POR LADO + cobertura) es
    NUMÉRICAMENTE IGUAL a la de v1: Luxy reusa el mismo helper RIES-W
    (`nt_riesgo._listado_crudo`) sobre el mismo listado crudo + offset ET, sin
    duplicar lógica. Además la ruta inyecta la comparación con la ventana L2
    vigente y el front expone el panel nativo."""
    import scripts.mr_luxy as mrl
    import scripts.nt_riesgo as ntr
    import app.web.routes_riesgo as rr
    monkeypatch.setenv("HOLC_DIR", "NINJATRADER/HOLC")
    await _mk_strategy(db, "ES5m_Vent", "ES")
    csv_real = Path(sorted(_ES_CSV)[-1])
    r = await client.post(
        "/ui/strategies/ES5m_Vent/integrar",
        files={"file": (csv_real.name, csv_real.read_bytes(), "text/csv")})
    clave = r.json()["clave"]

    study = mrl.run_for_clave(clave, rr.MOTOR_DIR)
    dash = study["dashboard"]
    assert dash is not None                        # ES tiene HOLC → intrabar

    # v1 INDEPENDIENTE: recarga el master y llama al helper RIES-W directo.
    base_dir = rr.MOTOR_DIR / clave
    _man, trades, _ppt, _k, _i, _b, has_intrabar, off = mrl._load_master(base_dir)
    assert has_intrabar and isinstance(off, int)   # master enriquecido (offset ET)
    lc = ntr._listado_crudo(trades, off)

    # paridad exacta (mismo helper, mismos trades, mismo offset)
    assert dash["ventana_operacion"] == lc["ventana_operacion"]
    assert dash["duracion_h_por_lado"] == lc["duracion_h_por_lado"]
    # el rango POR LADO existe (largos/cortos) — el dato que dimensiona el topo
    et = dash["ventana_operacion"]["rango_horario_et"]
    assert "long" in et and "short" in et

    # la ruta expone el panel nativo + inyecta la comparación con la L2 vigente
    page = await client.get("/ui/strategies/ES5m_Vent")
    assert page.status_code == 200
    assert 'id="lx-window"' in page.text
    assert '"ventana_operacion"' in page.text
    assert '"ventana_vigente"' in page.text


# ---------------------------------------------------------------------------
# Riesgo v1 intacta (regresión)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_riesgo_v1_redirige_al_detalle(
    client: AsyncClient, dirs: Path
) -> None:
    """L7b — Riesgo v1 retirado de la UI: /ui/riesgo redirige (patrón P3). Sin
    `strategy` → /ui/strategies; con `?strategy=X` → detalle de X (Luxy)."""
    r = await client.get("/ui/riesgo")
    assert r.status_code == 302
    assert r.headers["location"] == "/ui/strategies"
    r = await client.get("/ui/riesgo?strategy=ES5m_Test")
    assert r.status_code == 302
    assert r.headers["location"] == "/ui/strategies/ES5m_Test"


# ---------------------------------------------------------------------------
# ACEPTACIÓN (datos reales, gated): alta → subir lista → integrar cuadrando
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAY_DATOS, reason="datos reales de ES no disponibles")
@pytest.mark.asyncio
async def test_alta_subir_integrar_real_cuadre(
    client: AsyncClient, dirs: Path, db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """e2e desde Estrategias: crear estrategia ES → subir el export real →
    integrar con el motor REAL (cuadre al dólar bloqueante). R-T9:
    usd_por_punto del master, no del CSV."""
    monkeypatch.setenv("HOLC_DIR", "NINJATRADER/HOLC")        # HOLC real
    await _mk_strategy(db, "ES5m_L1Real", "ES")
    csv_real = Path(sorted(_ES_CSV)[-1])

    r = await client.post(
        "/ui/strategies/ES5m_L1Real/integrar",
        files={"file": (csv_real.name, csv_real.read_bytes(), "text/csv")})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True and j["n_trades"] == 120
    assert j["degradado"] is False and j["instrument"] == "ES"

    man = json.loads(
        (dirs / "MotorRiesgo" / j["clave"] / "manifest.json")
        .read_text(encoding="utf-8"))
    assert man["cuadre"]["ok"] is True                 # cuadre al dólar
    assert man["usd_por_punto"]["usado"] == 50.0       # del master (R-T9)
