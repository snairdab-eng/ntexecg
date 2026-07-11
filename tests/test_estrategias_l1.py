"""LOTE L1 — alta y datos DENTRO de Estrategias.

Cubre:
- Subir la lista → integrar el master DESDE Estrategias (reusa el núcleo del
  Motor `routes_riesgo.integrar_lista`; el motor NO se muda). Real cuadrando al
  dólar en el test gated con datos de ES.
- Provisión de HOLC: activo sin HOLC → 409 holc_missing (degradado/aviso/botón);
  subir HOLC válido → queda en NINJATRADER/HOLC y el reintegro sale completo;
  HOLC inválido → rechazo sin tocar disco; anti-traversal del nombre destino.
- Detalle con sub-pestañas Config·Luxy·Lab·Perfiles + selector desplegable.
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
    # las 4 sub-pestañas + el estado Alpine
    assert "stab: 'config'" in html
    for label in ("Config", "Luxy", "Lab", "Perfiles"):
        assert f">{label}<" in html
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


@pytest.mark.skipif(not _HAY_DATOS, reason="datos reales de ES no disponibles")
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

    # la sub-pestaña renderiza el DASHBOARD (L3) + la Tabla A
    r = await client.get("/ui/strategies/ES5m_LuxReal")
    assert r.status_code == 200
    html = r.text
    assert "Tabla A — métricas" in html
    assert "espejo" in html.lower()
    # dashboard portado (dark): raíz, payload inyectado, Recalcular motor,
    # gráficas, sesiones ET y la honestidad del BE
    assert 'id="lx-root"' in html and "window.LUXY" in html
    assert "Recalcular (motor)" in html
    assert 'id="lx-chart-in"' in html and 'id="lx-chart-oos"' in html
    assert "BE: requiere recálculo del motor" in html
    assert "ET" in html                              # rango horario ET (R-T7)


@pytest.mark.skipif(not _HAY_DATOS, reason="datos reales de ES no disponibles")
@pytest.mark.asyncio
async def test_luxy_evaluar_parity_real(
    client: AsyncClient, dirs: Path, db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """RECALCULAR (/luxy/evaluar) usa el evaluador de L2 → mismos números que
    el estudio: evaluar sin overrides reproduce la fila In-sample de la Tabla A
    (BE no recomendado en ES → coinciden)."""
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
    fila_in = next(f for f in study["tabla_a"] if f["fila"] == "In-sample")
    assert study["levers_in_sample"]["breakeven"]["be_atr"] is None   # BE no reco
    assert round(ev["config"]["net"]) == round(fila_in["net_usd"]), \
        (ev["config"]["net"], fila_in["net_usd"])


# ---------------------------------------------------------------------------
# Riesgo v1 intacta (regresión)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_riesgo_v1_sigue_respondiendo(
    client: AsyncClient, dirs: Path
) -> None:
    (dirs / "REPORTES" / "lab_manifest.json").write_text(
        json.dumps({"version": 1, "entries": {}}), encoding="utf-8")
    r = await client.get("/ui/riesgo")
    assert r.status_code == 200


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
