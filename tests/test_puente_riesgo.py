"""Puente Riesgo↔Estrategias (SPEC 2026-07-06).

Candados verificados:
  P1 — visibilidad: la API expone los campos MR-5 (backstop/tp_nominal/...),
       la pestaña Estrategias avisa "SL×ATR ignorado" con backstop activo, y
       el badge de deriva distingue aplicada/difiere/sin_aplicar/sin_viva.
  P2 — aplicar SUPERVISADO: preview con diff, confirmar hace merge al
       pipeline_config_json con AuditLog, PRESERVA el mode de scale_entry
       (NX-11: aplicar no arma la ejecución) y JAMÁS toca el kill-switch.
  P3 — promoción estudio→viva: alta prellenada (id bloqueado, una sola
       tecleada de identidad) que encadena al diff de aplicar (?aplicar=1).
"""
import json
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.web.routes_lab as routes_lab
import app.web.routes_riesgo as rr
from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.audit_log import AuditLog
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from tests.test_riesgo_ui import ESTUDIO, _seed_motor, _write_lab_manifest


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_puente")
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


# La activación de la recomendación del ESTUDIO compartido (test_riesgo_ui):
# backstop 90 pts · TP L11.5/S8×ATR · escalera [0.25,0.5]×[5,5] · 2760s.
ACT = rr._activacion_json(ESTUDIO["recomendacion"])

SID = "ES5m_Test"


def _manifest_es(dirs: Path) -> None:
    _write_lab_manifest(dirs, {SID: {
        "instrument": "ES", "csv": "ListaDeOperaciones/x.csv",
        "confirmed": True}})


async def _viva(db: AsyncSession, pcfg: dict | None = None,
                with_profile: bool = True) -> StrategyProfile | None:
    db.add(Strategy(strategy_id=SID, name="T", asset_symbol="MES",
                    status="paper", enabled=True))
    prof = None
    if with_profile:
        prof = StrategyProfile(strategy_id=SID, pipeline_config_json=pcfg)
        db.add(prof)
    await db.commit()
    return prof


# ---------------------------------------------------------------------------
# P1 — deriva_estudio (puro) + _merge_activacion (puro)
# ---------------------------------------------------------------------------

def test_deriva_estados():
    assert rr.deriva_estudio({}, None) is None            # sin reco → sin badge
    d = rr.deriva_estudio({}, ACT, "2026-07-05", hay_viva=False)
    assert d["estado"] == "sin_estrategia_viva"
    assert rr.deriva_estudio({}, ACT, "2026-07-05")["estado"] == "sin_aplicar"
    # aplicada: mismos valores — el mode de scale_entry NO cuenta (NX-11)
    pcfg = rr._merge_activacion({}, ACT)
    assert pcfg["scale_entry"]["mode"] == "design_only"   # nunca arma solo
    d = rr.deriva_estudio(pcfg, ACT, "2026-07-05")
    assert d["estado"] == "aplicada" and "2026-07-05" in d["texto"]
    # difiere: un campo presente pero distinto
    otro = dict(pcfg, backstop_points=999.0)
    assert rr.deriva_estudio(otro, ACT)["estado"] == "difiere"
    # escalera vieja de pruebas (presente ≠ reco) también es deriva
    solo_se = {"scale_entry": {"levels": [4.0, 5.0], "quantities": [0, 2, 2],
                               "max_micro_contracts": 4}}
    assert rr.deriva_estudio(solo_se, ACT)["estado"] == "difiere"


def test_merge_activacion_nx11_y_kill_switch():
    # merge sobre pcfg vacío: escribe las llaves, mode SIEMPRE design_only
    cfg = rr._merge_activacion({}, ACT)
    assert cfg["backstop_points"] == 90.0
    assert cfg["tp_nominal_long"] == 11.5
    assert cfg["tp_nominal_short"] == 8.0
    assert cfg["entry_reserve_timeout_seconds"] == 2760
    assert cfg["scale_entry"]["levels"] == [0.25, 0.5]
    assert cfg["scale_entry"]["quantities"] == [0, 5, 5]
    assert cfg["scale_entry"]["mode"] == "design_only"
    assert cfg["scale_entry"]["stop_mode"] == "common_position_stop"
    # NX-11: una ejecución YA armada se preserva; lo ajeno no se toca
    vivo = {"guardrails": {"x": 1},
            "scale_entry": {"mode": "execute", "levels": [4.0, 5.0],
                            "quantities": [0, 2, 2], "max_micro_contracts": 4,
                            "stop_mode": "common_position_stop"}}
    cfg2 = rr._merge_activacion(vivo, ACT)
    assert cfg2["scale_entry"]["mode"] == "execute"       # preservado
    assert cfg2["scale_entry"]["levels"] == [0.25, 0.5]   # niveles nuevos
    assert cfg2["guardrails"] == {"x": 1}                 # intacto
    assert vivo["scale_entry"]["levels"] == [4.0, 5.0]    # sin mutar entrada


# ---------------------------------------------------------------------------
# P1 — API + avisos en la pestaña Estrategias
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_config_expone_campos_mr(client: AsyncClient,
                                           db: AsyncSession) -> None:
    await _viva(db, pcfg={"backstop_points": 90.0, "tp_nominal_long": 11.5,
                          "tp_nominal_short": 8.0, "short_size_factor": 0.5,
                          "entry_reserve_timeout_seconds": 2760})
    r = await client.get(f"/api/strategies/{SID}/config")
    assert r.status_code == 200
    j = r.json()
    for zona in ("override", "effective"):
        assert j[zona]["backstop_points"] == 90.0, zona
        assert j[zona]["tp_nominal_long"] == 11.5, zona
        assert j[zona]["tp_nominal_short"] == 8.0, zona
        assert j[zona]["short_size_factor"] == 0.5, zona
        assert j[zona]["entry_reserve_timeout_seconds"] == 2760, zona


@pytest.mark.asyncio
async def test_estrategias_avisa_sl_tp_ignorados(client: AsyncClient,
                                                 db: AsyncSession,
                                                 dirs: Path) -> None:
    _manifest_es(dirs)
    _seed_motor(dirs)
    await _viva(db, pcfg={"backstop_points": 90.0, "tp_nominal_long": 11.5,
                          "tp_nominal_short": 8.0})
    r = await client.get(f"/ui/strategies/{SID}")
    assert r.status_code == 200
    html = r.text
    assert "SL × ATR ignorado" in html
    assert "90.0 pts" in html or "90 pts" in html
    assert "TP × ATR ignorado" in html
    # badge de deriva (tp/backstop coinciden pero falta escalera → difiere)
    assert "difiere del estudio" in html
    assert "ver estudio →" in html


@pytest.mark.asyncio
async def test_estrategias_sin_backstop_no_avisa(client: AsyncClient,
                                                 db: AsyncSession) -> None:
    await _viva(db, pcfg=None)
    r = await client.get(f"/ui/strategies/{SID}")
    assert r.status_code == 200
    assert "SL × ATR ignorado" not in r.text
    assert "TP × ATR ignorado" not in r.text


# ---------------------------------------------------------------------------
# P1/P2 — página Riesgo: badge + botón aplicar / CTA de promoción
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_riesgo_badge_y_boton_con_viva(client: AsyncClient,
                                             db: AsyncSession,
                                             dirs: Path) -> None:
    _manifest_es(dirs)
    _seed_motor(dirs)
    await _viva(db)
    r = await client.get(f"/ui/riesgo?strategy={SID}")
    assert r.status_code == 200
    html = r.text
    assert "Aplicar a la config viva…" in html
    assert "SIN aplicar" in html                          # badge de deriva
    assert "dar de alta en Estrategias" not in html       # ya hay viva


@pytest.mark.asyncio
async def test_riesgo_cta_promocion_sin_viva(client: AsyncClient,
                                             dirs: Path) -> None:
    _manifest_es(dirs)
    _seed_motor(dirs)
    r = await client.get(f"/ui/riesgo?strategy={SID}")
    assert r.status_code == 200
    html = r.text
    assert "dar de alta en Estrategias" in html
    assert f"from_estudio={SID}" in html
    assert "Aplicar a la config viva…" not in html
    assert "sin estrategia viva" in html                  # badge gris


# ---------------------------------------------------------------------------
# P2 — preview (diff) y aplicar (merge + audit + kill-switch intacto)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_preview_400s(client: AsyncClient, db: AsyncSession,
                            dirs: Path) -> None:
    _write_lab_manifest(dirs, {})
    r = await client.get(f"/ui/riesgo/aplicar/preview?strategy={SID}")
    assert r.status_code == 400 and "manifest" in r.json()["error"]
    # en el manifest pero sin recomendación validada
    _manifest_es(dirs)
    _seed_motor(dirs, estudio={**ESTUDIO, "recomendacion": None})
    r = await client.get(f"/ui/riesgo/aplicar/preview?strategy={SID}")
    assert r.status_code == 400 and "recomendación" in r.json()["error"]
    # con recomendación pero sin estrategia viva
    (dirs / "MotorRiesgo" / "ES_Test" / "runs"
     / "estudios_2026-07-05.json").write_text(
        json.dumps(ESTUDIO), encoding="utf-8")
    r = await client.get(f"/ui/riesgo/aplicar/preview?strategy={SID}")
    assert r.status_code == 400 and "viva" in r.json()["error"]


@pytest.mark.asyncio
async def test_preview_diff_completo(client: AsyncClient, db: AsyncSession,
                                     dirs: Path) -> None:
    _manifest_es(dirs)
    _seed_motor(dirs)
    await _viva(db, pcfg={"scale_entry": {"mode": "execute",
                                          "levels": [0.75, 1.25],
                                          "quantities": [0, 1, 4],
                                          "max_micro_contracts": 5}})
    r = await client.get(f"/ui/riesgo/aplicar/preview?strategy={SID}")
    assert r.status_code == 200
    j = r.json()
    campos = {f["campo"]: f for f in j["filas"]}
    assert any("backstop" in c for c in campos)
    bk = next(f for c, f in campos.items() if "backstop" in c)
    assert bk["cambia"] is True and "90" in bk["recomendado"]
    esc = next(f for c, f in campos.items() if "Escalera" in c)
    assert esc["cambia"] is True and "NX-11" in esc["nota"]
    ca = next(f for c, f in campos.items() if "cancel_after" in c)
    assert "TradersPost" in ca["nota"]                     # el aviso manual
    assert j["deriva"]["estado"] == "difiere"
    assert any("TradersPost" in a for a in j["avisos"])
    assert any("dry_run" in a for a in j["avisos"])


@pytest.mark.asyncio
async def test_aplicar_merge_audit_y_kill_switch(client: AsyncClient,
                                                 db: AsyncSession,
                                                 dirs: Path) -> None:
    _manifest_es(dirs)
    _seed_motor(dirs)
    prof = await _viva(db, pcfg={
        "guardrails": {"g": 1},
        "scale_entry": {"mode": "execute", "levels": [0.75, 1.25],
                        "quantities": [0, 1, 4], "max_micro_contracts": 5,
                        "stop_mode": "common_position_stop"}})
    dry_antes = prof.dry_run
    tp_antes = prof.traderspost_enabled
    r = await client.post("/ui/riesgo/aplicar", json={"strategy": SID})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True
    assert j["deriva"]["estado"] == "aplicada"
    assert "TradersPost" in j["recordatorio"]
    await db.refresh(prof)
    cfg = prof.pipeline_config_json
    assert cfg["backstop_points"] == 90.0
    assert cfg["tp_nominal_long"] == 11.5
    assert cfg["tp_nominal_short"] == 8.0
    assert cfg["entry_reserve_timeout_seconds"] == 2760
    assert cfg["scale_entry"]["levels"] == [0.25, 0.5]
    assert cfg["scale_entry"]["quantities"] == [0, 5, 5]
    assert cfg["scale_entry"]["mode"] == "execute"        # NX-11 preservado
    assert cfg["guardrails"] == {"g": 1}                  # lo ajeno intacto
    # kill-switch INTACTO
    assert prof.dry_run == dry_antes
    assert prof.traderspost_enabled == tp_antes
    s = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == SID))).scalar_one()
    assert s.status == "paper"
    # AuditLog con el diff
    row = (await db.execute(select(AuditLog).where(
        AuditLog.action == "APPLY_RIESGO_RECO"))).scalars().first()
    assert row is not None and row.object_id == SID
    # segunda pasada: idempotente y deriva sigue aplicada
    r2 = await client.post("/ui/riesgo/aplicar", json={"strategy": SID})
    assert r2.status_code == 200
    assert r2.json()["deriva"]["estado"] == "aplicada"


@pytest.mark.asyncio
async def test_aplicar_crea_profile_si_no_existe(client: AsyncClient,
                                                 db: AsyncSession,
                                                 dirs: Path) -> None:
    _manifest_es(dirs)
    _seed_motor(dirs)
    await _viva(db, with_profile=False)
    r = await client.post("/ui/riesgo/aplicar", json={"strategy": SID})
    assert r.status_code == 200, r.text
    prof = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == SID))).scalar_one()
    cfg = prof.pipeline_config_json
    assert cfg["backstop_points"] == 90.0
    # sin ejecución previa que preservar → la escalera queda en design_only
    assert cfg["scale_entry"]["mode"] == "design_only"


# ---------------------------------------------------------------------------
# P3 — promoción estudio→viva
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alta_prellenada_desde_estudio(client: AsyncClient,
                                             dirs: Path) -> None:
    _manifest_es(dirs)
    r = await client.get(f"/ui/strategies/new?from_estudio={SID}")
    assert r.status_code == 200
    html = r.text
    assert "Promoción desde el Motor de Riesgo" in html
    assert f'value="{SID}"' in html and "readonly" in html
    assert f'name="from_estudio" value="{SID}"' in html
    assert "Sugerido: <b>MES</b>" in html                 # micro del ES
    assert 'value="5m" selected' in html                  # tf del id


@pytest.mark.asyncio
async def test_alta_sin_from_estudio_no_cambia(client: AsyncClient) -> None:
    r = await client.get("/ui/strategies/new")
    assert r.status_code == 200
    assert "Promoción desde el Motor de Riesgo" not in r.text
    assert "readonly" not in r.text


@pytest.mark.asyncio
async def test_promocion_encadena_a_aplicar(client: AsyncClient,
                                            db: AsyncSession,
                                            dirs: Path) -> None:
    _manifest_es(dirs)
    _seed_motor(dirs)
    r = await client.post("/ui/strategies/new", data={
        "strategy_id": SID, "name": "Promovida", "asset_symbol": "MES",
        "timeframe": "5m", "from_estudio": SID})
    assert r.status_code == 303
    loc = r.headers["location"]
    assert f"/ui/riesgo?strategy={SID}" in loc and "aplicar=1" in loc
    assert "token" in loc                                  # flash con el token
    # nace DESARMADA
    s = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == SID))).scalar_one()
    assert s.status in ("candidate", "paper")
    # y la página de riesgo con ese flash renderiza el mensaje (token a la
    # vista UNA vez) + el diff listo (botón presente porque ya hay viva)
    r2 = await client.get(loc.replace(" ", "%20"))
    assert r2.status_code == 200
    assert "token webhook" in r2.text
    assert "Aplicar a la config viva…" in r2.text


@pytest.mark.asyncio
async def test_alta_normal_redirige_a_detalle(client: AsyncClient,
                                              db: AsyncSession) -> None:
    r = await client.post("/ui/strategies/new", data={
        "strategy_id": "NQ5m_Nueva", "name": "Normal",
        "asset_symbol": "MNQ", "timeframe": "5m"})
    assert r.status_code == 303
    assert "/ui/strategies/NQ5m_Nueva" in r.headers["location"]
