"""LOTE P-B — reglas 2–8 del Portafolio, codificadas e INERTES.

Por regla: APAGADA → decisión byte-a-byte idéntica (el escenario que la violaría
pasa intacto: {"failed": False}); ENCENDIDA → bloquea SU escenario con motivo
visible; fail-closed cuando el estado no es computable con la regla encendida.
Exits/legs siguen exentos (el guard solo ve entradas nuevas — semántica de P-A).
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution_result import ExecutionResult
from app.models.normalized_signal import NormalizedSignal
from app.models.position_state import PositionState
from app.models.symbol_map import SymbolMap
from app.services import portfolio_guard as pg
from app.services.portfolio_guard import PortfolioGuard

UTC = timezone.utc
G = PortfolioGuard()


def _map(tv, mapped, data=None):
    return SymbolMap(tv_symbol=tv, mapped_symbol=mapped, market_data_symbol=data,
                     exchange="CME", contract_type="futures_micro",
                     pine_script_config=f'"ticker": "{tv}"', active=True)


async def _seed_maps(db):
    db.add(_map("ES", "ESU2026")); db.add(_map("MES", "MESU2026", data="ES"))
    db.add(_map("NQ", "NQU2026")); db.add(_map("GC", "GCU2026"))
    await db.flush()


def _sig(ticker="ES", mapped="ESU2026", action="buy"):
    return NormalizedSignal(
        raw_signal_id=uuid.uuid4(), strategy_id="B", ticker_received=ticker,
        mapped_symbol=mapped, action=action, sentiment="long",
        price=5000.0, signal_ts=datetime.now(UTC), signal_role="entry_long",
        dedupe_key=uuid.uuid4().hex)


def _pos(symbol, state="LONG", qty=1, direction="long", worst=None,
         strategy_id="A"):
    return PositionState(
        strategy_id=strategy_id, account_id="paper_default", symbol=symbol,
        state=state, state_source="estimated", direction=direction,
        quantity=qty, risk_plan_json=({"worst_case_usd": worst}
                                      if worst is not None else None))


async def _check(db, rules, params=None, ticker="ES", mapped="ESU2026",
                 action="buy", config=None):
    return await G.check_entry(db, _sig(ticker, mapped, action),
                               config or {}, rules=rules, params=params)


# ---------------------------------------------------------------------------
# Regla 2 — no apilar el mismo grupo/clase
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_r2_off_identico(db: AsyncSession):
    await _seed_maps(db)
    db.add(_pos("NQU2026")); await db.flush()             # índice abierto
    r = await _check(db, {pg.RULE_NO_STACK_GROUP: False})  # ES entra
    assert r == {"failed": False}                         # byte-a-byte


@pytest.mark.asyncio
async def test_r2_on_bloquea_grupo(db: AsyncSession):
    await _seed_maps(db)
    db.add(_pos("NQU2026")); await db.flush()
    r = await _check(db, {pg.RULE_NO_STACK_GROUP: True})   # ES (índice) sobre NQ
    assert r["failed"] and r["reason"] == "portfolio_group_busy"
    assert r["group"] == "indices"


@pytest.mark.asyncio
async def test_r2_on_otro_grupo_pasa(db: AsyncSession):
    await _seed_maps(db)
    db.add(_pos("GCU2026")); await db.flush()             # metal abierto
    r = await _check(db, {pg.RULE_NO_STACK_GROUP: True})   # ES (índice) → distinto
    assert r == {"failed": False}


@pytest.mark.asyncio
async def test_r2_failclosed_grupo_desconocido(db: AsyncSession):
    await _seed_maps(db)
    db.add(_pos("ZZZ9", strategy_id="ghost")); await db.flush()  # sin mapa
    r = await _check(db, {pg.RULE_NO_STACK_GROUP: True})
    assert r["failed"] and r["reason"] == "portfolio_exposure_unknown"


# ---------------------------------------------------------------------------
# Regla 3 — tope de riesgo agregado ($) — reusa worst_case_loss (L4)
# ---------------------------------------------------------------------------

_CFG_PV = {"backstop_points": 80.0, "tick_value": 1.25, "tick_size": 0.25}  # pv=5


@pytest.mark.asyncio
async def test_r3_off_identico(db: AsyncSession):
    await _seed_maps(db)
    db.add(_pos("NQU2026", worst=9000.0)); await db.flush()
    r = await _check(db, {pg.RULE_MAX_RISK_USD: False},
                     params={"rule_3_max_risk_usd": 1000.0}, config=_CFG_PV)
    assert r == {"failed": False}


@pytest.mark.asyncio
async def test_r3_on_bloquea_tope(db: AsyncSession):
    await _seed_maps(db)
    db.add(_pos("NQU2026", worst=9000.0)); await db.flush()
    # incoming worst-case = 2*80*5 = 800 → 9000+800 > 5000
    r = await _check(db, {pg.RULE_MAX_RISK_USD: True},
                     params={"rule_3_max_risk_usd": 5000.0},
                     config={**_CFG_PV, "scale_entry": {"quantities": [2]}})
    assert r["failed"] and r["reason"] == "portfolio_risk_cap"


@pytest.mark.asyncio
async def test_r3_on_bajo_tope_pasa(db: AsyncSession):
    await _seed_maps(db)
    db.add(_pos("NQU2026", worst=1000.0)); await db.flush()
    r = await _check(db, {pg.RULE_MAX_RISK_USD: True},
                     params={"rule_3_max_risk_usd": 5000.0},
                     config={**_CFG_PV, "scale_entry": {"quantities": [2]}})
    assert r == {"failed": False}                          # 1000+800 ≤ 5000


@pytest.mark.asyncio
async def test_r3_failclosed_sin_worstcase(db: AsyncSession):
    await _seed_maps(db)
    db.add(_pos("NQU2026")); await db.flush()             # sin worst_case_usd
    r = await _check(db, {pg.RULE_MAX_RISK_USD: True},
                     params={"rule_3_max_risk_usd": 5000.0}, config=_CFG_PV)
    assert r["failed"] and r["reason"] == "portfolio_risk_unknown"


# ---------------------------------------------------------------------------
# Regla 4 — tope de micros totales
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_r4_off_identico(db: AsyncSession):
    await _seed_maps(db)
    db.add(_pos("NQU2026", qty=8)); await db.flush()
    r = await _check(db, {pg.RULE_MAX_MICROS: False},
                     params={"rule_4_max_micros": 10},
                     config={"scale_entry": {"quantities": [5]}})
    assert r == {"failed": False}


@pytest.mark.asyncio
async def test_r4_on_bloquea(db: AsyncSession):
    await _seed_maps(db)
    db.add(_pos("NQU2026", qty=8)); await db.flush()      # 8 abiertos + 5 > 10
    r = await _check(db, {pg.RULE_MAX_MICROS: True},
                     params={"rule_4_max_micros": 10},
                     config={"scale_entry": {"quantities": [5]}})
    assert r["failed"] and r["reason"] == "portfolio_micros_cap"


@pytest.mark.asyncio
async def test_r4_on_cabe_pasa(db: AsyncSession):
    await _seed_maps(db)
    db.add(_pos("NQU2026", qty=3)); await db.flush()      # 3 + 2 ≤ 10
    r = await _check(db, {pg.RULE_MAX_MICROS: True},
                     params={"rule_4_max_micros": 10},
                     config={"scale_entry": {"quantities": [2]}})
    assert r == {"failed": False}


@pytest.mark.asyncio
async def test_r4_failclosed_estado_ilegible(db: AsyncSession,
                                             monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(db, "execute", _boom)
    r = await _check(db, {pg.RULE_MAX_MICROS: True},
                     params={"rule_4_max_micros": 10})
    assert r["failed"] and r["reason"] == "portfolio_state_unreadable"


# ---------------------------------------------------------------------------
# Regla 5 — tope de pérdida diaria (ExecutionResult)
# ---------------------------------------------------------------------------

def _exec(pnl, mins_ago=5):
    return ExecutionResult(
        row_hash=uuid.uuid4().hex, symbol="ESU2026", direction="long",
        quantity=1, exit_time=datetime.now(UTC) - timedelta(minutes=mins_ago),
        pnl=pnl, match_method="signal_id")


@pytest.mark.asyncio
async def test_r5_off_identico(db: AsyncSession):
    db.add(_exec(-3000.0)); await db.flush()
    r = await _check(db, {pg.RULE_MAX_DAILY_LOSS: False},
                     params={"rule_5_max_daily_loss_usd": 2000.0})
    assert r == {"failed": False}


@pytest.mark.asyncio
async def test_r5_on_bloquea(db: AsyncSession):
    db.add(_exec(-1500.0)); db.add(_exec(-1000.0)); await db.flush()  # -2500
    r = await _check(db, {pg.RULE_MAX_DAILY_LOSS: True},
                     params={"rule_5_max_daily_loss_usd": 2000.0})
    assert r["failed"] and r["reason"] == "portfolio_daily_loss"


@pytest.mark.asyncio
async def test_r5_on_dentro_pasa(db: AsyncSession):
    db.add(_exec(-500.0)); db.add(_exec(300.0)); await db.flush()   # -200
    r = await _check(db, {pg.RULE_MAX_DAILY_LOSS: True},
                     params={"rule_5_max_daily_loss_usd": 2000.0})
    assert r == {"failed": False}


# ---------------------------------------------------------------------------
# Regla 6 — máx posiciones simultáneas
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_r6_off_identico(db: AsyncSession):
    await _seed_maps(db)
    for s in ("NQU2026", "GCU2026", "ESU2026"):
        db.add(_pos(s))
    await db.flush()
    r = await _check(db, {pg.RULE_MAX_POSITIONS: False},
                     params={"rule_6_max_positions": 3}, ticker="6E",
                     mapped="6EU2026")
    assert r == {"failed": False}


@pytest.mark.asyncio
async def test_r6_on_bloquea(db: AsyncSession):
    await _seed_maps(db)
    for s in ("NQU2026", "GCU2026", "ESU2026"):            # 3 abiertas
        db.add(_pos(s))
    await db.flush()
    r = await _check(db, {pg.RULE_MAX_POSITIONS: True},
                     params={"rule_6_max_positions": 3}, ticker="6E",
                     mapped="6EU2026")                     # la 4ª
    assert r["failed"] and r["reason"] == "portfolio_positions_cap"


@pytest.mark.asyncio
async def test_r6_on_cabe_pasa(db: AsyncSession):
    await _seed_maps(db)
    db.add(_pos("NQU2026")); await db.flush()
    r = await _check(db, {pg.RULE_MAX_POSITIONS: True},
                     params={"rule_6_max_positions": 3}, ticker="6E",
                     mapped="6EU2026")
    assert r == {"failed": False}


# ---------------------------------------------------------------------------
# Regla 7 — enfriamiento tras pérdida grande
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_r7_off_identico(db: AsyncSession):
    db.add(_exec(-1500.0, mins_ago=5)); await db.flush()
    r = await _check(db, {pg.RULE_COOLDOWN_LOSS: False},
                     params={"rule_7_cooldown_min": 30,
                             "rule_7_loss_threshold_usd": 1000.0})
    assert r == {"failed": False}


@pytest.mark.asyncio
async def test_r7_on_bloquea(db: AsyncSession):
    db.add(_exec(-1500.0, mins_ago=5)); await db.flush()  # pérdida grande reciente
    r = await _check(db, {pg.RULE_COOLDOWN_LOSS: True},
                     params={"rule_7_cooldown_min": 30,
                             "rule_7_loss_threshold_usd": 1000.0})
    assert r["failed"] and r["reason"] == "portfolio_cooldown"


@pytest.mark.asyncio
async def test_r7_on_fuera_ventana_pasa(db: AsyncSession):
    db.add(_exec(-1500.0, mins_ago=90)); await db.flush()  # fuera de los 30 min
    r = await _check(db, {pg.RULE_COOLDOWN_LOSS: True},
                     params={"rule_7_cooldown_min": 30,
                             "rule_7_loss_threshold_usd": 1000.0})
    assert r == {"failed": False}


# ---------------------------------------------------------------------------
# Regla 8 — sesgo direccional del grupo
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_r8_off_identico(db: AsyncSession):
    await _seed_maps(db)
    db.add(_pos("NQU2026", direction="long"))
    db.add(_pos("YMU2026", direction="long")); await db.flush()
    r = await _check(db, {pg.RULE_GROUP_BIAS: False},
                     params={"rule_8_group_bias_max": 2})
    assert r == {"failed": False}


@pytest.mark.asyncio
async def test_r8_on_bloquea_sesgo(db: AsyncSession):
    await _seed_maps(db)
    db.add(_map("YM", "YMU2026"))
    db.add(_pos("NQU2026", direction="long"))
    db.add(_pos("YMU2026", direction="long")); await db.flush()   # 2 índices long
    r = await _check(db, {pg.RULE_GROUP_BIAS: True},
                     params={"rule_8_group_bias_max": 2})          # ES long = 3º
    assert r["failed"] and r["reason"] == "portfolio_group_bias"
    assert r["side"] == "long" and r["group"] == "indices"


@pytest.mark.asyncio
async def test_r8_on_lado_contrario_pasa(db: AsyncSession):
    await _seed_maps(db)
    db.add(_map("YM", "YMU2026"))
    db.add(_pos("NQU2026", direction="short"))
    db.add(_pos("YMU2026", direction="short")); await db.flush()  # 2 shorts
    r = await _check(db, {pg.RULE_GROUP_BIAS: True},
                     params={"rule_8_group_bias_max": 2})          # ES LONG → otro lado
    assert r == {"failed": False}
