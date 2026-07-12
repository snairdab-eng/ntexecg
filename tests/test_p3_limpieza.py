"""P3 (auditoría 2026-07-06) — limpieza: Templates deprecado + sims borrados.

Candados verificados:
  - P3-1 NO destructivo: la UI de Templates se retiró (ruta 404, nav sin la
    entrada, cero refs en app/) pero el MODELO StrategyTemplate y la columna
    strategies.template_id se CONSERVAN (cero migración destructiva);
  - P3-2: los 5 sims pre-motor no existen y nada en app/scripts/tests los
    referencia (el Motor de Riesgo los supersede con OOS + corte);
  - invariantes: Lab UI y Riesgo INTACTOS (el operador canceló el retiro del
    Lab); lab_metrics/lab_analyze siguen en su sitio; ConflictLog conservado.
"""
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings

SIMS_BORRADOS = ("sim_sl_matrix", "sim_scaled_entry", "sim_sizing",
                 "sweep_matrix", "calibrate_sl_from_trades")

ARCHIVADOS = ("apply_anexo21_demo", "apply_profile_policy_v1",
              "revert_asset_profiles_v1", "diag_profiles",
              "compare_filter_decisions", "eval_quality_filters")


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_p3")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


def _fuentes(*dirs: str, exts=(".py", ".html")) -> list[Path]:
    out: list[Path] = []
    for d in dirs:
        for p in Path(d).rglob("*"):
            if (p.suffix in exts and p.is_file()
                    and "__pycache__" not in p.parts):
                out.append(p)
    return out


def test_cero_refs_a_templates_ui() -> None:
    """P3-1: los archivos de la UI no existen y NADA en app/ ni tests/ los
    referencia (nav, imports, links) — pero el modelo se conserva."""
    assert not Path("app/web/routes_strategy_templates.py").exists()
    assert not Path("app/templates/strategy_templates.html").exists()
    culpables = []
    for p in _fuentes("app", "tests"):
        if p.name == "test_p3_limpieza.py":
            continue
        texto = p.read_text(encoding="utf-8")
        if "strategy-templates" in texto or "routes_strategy_templates" in texto:
            culpables.append(str(p))
    assert culpables == [], f"refs colgando a la UI de Templates: {culpables}"
    # NO destructivo: modelo + columna + migración intactos
    assert Path("app/models/strategy_template.py").exists()
    from app.models.strategy import Strategy
    from app.models.strategy_template import StrategyTemplate  # importable

    assert StrategyTemplate.__tablename__ == "strategy_templates"
    assert hasattr(Strategy, "template_id")


def test_cero_refs_a_sims_borrados() -> None:
    """P3-2: los 5 sims pre-motor no existen y nada en el código vivo
    (app/, scripts/, tests/) los importa o invoca."""
    culpables = []
    for nombre in SIMS_BORRADOS:
        assert not Path(f"scripts/{nombre}.py").exists(), nombre
        for p in _fuentes("app", "scripts", "tests"):
            if p.name == "test_p3_limpieza.py":
                continue
            if nombre in p.read_text(encoding="utf-8"):
                culpables.append(f"{p}:{nombre}")
    assert culpables == [], f"refs colgando a sims borrados: {culpables}"


def test_one_shots_archivados_no_borrados() -> None:
    """Opcional P3: los one-shots históricos se ARCHIVARON (evidencia
    reproducible), no se borraron."""
    for nombre in ARCHIVADOS:
        assert Path(f"scripts/archivo/{nombre}.py").exists(), nombre
        assert not Path(f"scripts/{nombre}.py").exists(), nombre
    assert Path("scripts/archivo/__init__.py").exists()


@pytest.mark.asyncio
async def test_modelo_conservado_no_destructivo(db) -> None:
    """P3-1 NO destructivo: strategy_templates sigue siendo una tabla viva
    (se puede escribir/leer) aunque la UI ya no exista."""
    from sqlalchemy import select

    from app.models.strategy_template import StrategyTemplate

    db.add(StrategyTemplate(name="Persistida", strategy_type="trend_following"))
    await db.commit()
    row = await db.execute(
        select(StrategyTemplate).where(StrategyTemplate.name == "Persistida"))
    assert row.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_app_arranca_sin_templates_y_nav_intacto(
    client: AsyncClient,
) -> None:
    """La app arranca sin la ruta de Templates; el resto del nav responde y
    los invariantes (Lab UI, Riesgo) siguen vivos."""
    r = await client.get("/ui/strategy-templates")
    assert r.status_code == 404
    r = await client.post("/ui/strategy-templates/new",
                          data={"name": "X", "strategy_type": "t"})
    assert r.status_code == 404

    # L7b — /ui/riesgo ya no renderiza (redirige); el nav se verifica en otra
    # página real (Estrategias).
    r = await client.get("/ui/riesgo")
    assert r.status_code == 302
    html = (await client.get("/ui/strategies")).text
    assert "strategy-templates" not in html           # nav sin la entrada
    for entrada in (">Dashboard<", ">Estrategias<", ">Señales<",
                    ">Posiciones<", ">Settings<", ">Audit<"):
        assert entrada in html, entrada
    # L7b — Riesgo y Lab fuera del nav (Riesgo redirige; Lab vive en el detalle)
    assert ">Riesgo<" not in html and ">Lab<" not in html

    # invariantes: Lab UI conservada (bookmark/iframe L6 vivos aunque fuera del
    # nav), Estrategias (con el form de alta, sin templates_list) responde
    r = await client.get("/ui/lab")
    assert r.status_code == 200
    r = await client.get("/ui/strategies")
    assert r.status_code == 200
    r = await client.get("/ui/strategies/new")
    assert r.status_code == 200

    # el engine del Lab que Riesgo reusa sigue en su sitio
    assert Path("app/services/lab_metrics.py").exists()
    assert Path("scripts/lab_analyze.py").exists()
    assert Path("app/models/conflict_log.py").exists()
