"""MR-5c — escalera participativa (CONFIG, no código) + asimetría de lado.

Veredicto config-vs-código: build_scaled YA expresa la escalera del
recomendacion.json — la convención existente `quantities[0]` = pierna a
mercado (0 = sin ella) y `quantities[i>0]` ↔ `levels[i-1]` (límite a
señal ∓ level×ATR) cubre la balanceada completa:
  scale_entry: {"mode": "execute",
                "quantities": [0,1,1,1,1,1,1,1,1,1,1],
                "levels": [0.5,1,2,3,3.5,4.5,5,5.5,6,6.5],
                "max_micro_contracts": 10}
Los tests de escalera CONFIRMAN eso (sin código nuevo) + el fail-closed de
la Directiva 4 (sin ATR → entrada única; el stop sigue).

Lo NUEVO (opt-in): `short_size_factor` (0<f≤1) — motor de largos → cortos
con tamaño reducido, no eliminados. Entrada única: quantity·f (mínimo 1).
Escalonado: reparto por mayor resto conservando el total objetivo
(round(total·f)); a empate ganan las piernas SOMERAS (participación
primero). Ausente = simétrico (retrocompat). Solo entradas cortas; las
salidas jamás se reducen (cerrar completo).
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.normalized_signal import NormalizedSignal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services.config_resolver import ConfigResolver
from app.services.filter_pipeline import FilterPipeline
from app.services.payload_builder import PayloadBuilder


def _make_signal(action: str = "buy", sentiment: str = "long",
                 price: float | None = 5500.0, quantity: int = 1,
                 role: str = "entry_long") -> NormalizedSignal:
    return NormalizedSignal(
        raw_signal_id=uuid.uuid4(),
        strategy_id="test_strat",
        ticker_received="MES",
        mapped_symbol="MESU2025",
        action=action,
        sentiment=sentiment,
        price=price,
        quantity=quantity,
        signal_ts=datetime.now(timezone.utc),
        dedupe_key=uuid.uuid4().hex,
        signal_role=role,
    )


def _result(sl=5410.0, tp=None, atr=8.0):
    return SimpleNamespace(sl_price=sl, tp_price=tp, atr_value=atr,
                           score=None, quality=None, filters_active=False,
                           market_data_provider="Mock")


BALANCEADA = {
    "mode": "execute",
    "quantities": [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    "levels": [0.5, 1.0, 2.0, 3.0, 3.5, 4.5, 5.0, 5.5, 6.0, 6.5],
    "max_micro_contracts": 10,
}


# ---------------------------------------------------------------------------
# Escalera desde config (confirmación — la convención existente basta)
# ---------------------------------------------------------------------------

def test_escalera_balanceada_todas_limite_ancladas_a_senal():
    """quantities[0]=0 → SIN pierna a mercado; 10 límites a señal − d×ATR
    (long), todas con el MISMO stop — la escalera del recomendacion.json
    es config pura."""
    b = PayloadBuilder()
    legs = b.build_scaled(_make_signal("buy", "long"), None,
                          {"scale_entry": dict(BALANCEADA)}, _result())
    assert len(legs) == 10
    for leg, d in zip(legs, BALANCEADA["levels"]):
        assert leg["orderType"] == "limit"
        assert leg["limitPrice"] == pytest.approx(5500.0 - d * 8.0)
        assert leg["quantity"] == 1
        assert leg["stopLoss"] == {"type": "stop", "stopPrice": 5410.0}
    assert sum(x["quantity"] for x in legs) == 10


def test_escalera_short_signo_invertido():
    b = PayloadBuilder()
    legs = b.build_scaled(
        _make_signal("sell", "short", role="entry_short"), None,
        {"scale_entry": dict(BALANCEADA)}, _result(sl=5590.0))
    assert legs[0]["limitPrice"] == pytest.approx(5500.0 + 0.5 * 8.0)
    assert legs[-1]["limitPrice"] == pytest.approx(5500.0 + 6.5 * 8.0)


def test_escalera_sin_atr_cae_a_entrada_unica_con_stop():
    """Fail-closed Directiva 4: sin ATR no hay piernas → entrada única, y
    el stop (backstop fijo, que no necesita ATR) viaja igual."""
    b = PayloadBuilder()
    legs = b.build_scaled(_make_signal("buy", "long"), None,
                          {"scale_entry": dict(BALANCEADA)},
                          _result(sl=5410.0, atr=None))
    assert len(legs) == 1
    assert "orderType" not in legs[0]                    # mercado
    assert legs[0]["stopLoss"]["stopPrice"] == 5410.0


# ---------------------------------------------------------------------------
# Asimetría de lado (NUEVO, opt-in): short_size_factor
# ---------------------------------------------------------------------------

def test_factor_entrada_unica_corto_reducido():
    b = PayloadBuilder()
    p = b.build(_make_signal("sell", "short", quantity=2,
                             role="entry_short"), None,
                {"short_size_factor": 0.5}, _result(sl=5590.0))
    assert p["quantity"] == 1


def test_factor_no_reduce_por_debajo_de_1():
    """Reducidos, NO eliminados (referencia: 'tamaño reducido, no
    eliminarlos')."""
    b = PayloadBuilder()
    p = b.build(_make_signal("sell", "short", quantity=1,
                             role="entry_short"), None,
                {"short_size_factor": 0.3}, _result(sl=5590.0))
    assert p["quantity"] == 1


def test_factor_no_toca_largos_ni_salidas():
    b = PayloadBuilder()
    largo = b.build(_make_signal("buy", "long", quantity=2), None,
                    {"short_size_factor": 0.5}, _result())
    assert largo["quantity"] == 2
    salida = b.build(_make_signal("exit", "flat", quantity=2,
                                  role="exit_short"), None,
                     {"short_size_factor": 0.5}, _result(sl=None))
    # P0-EXIT-PARCIAL: cerrar COMPLETO = SIN quantity (con quantity TradersPost
    # cierra PARCIAL — este assert pineaba el bug del incidente 2026-07-20).
    assert "quantity" not in salida
    assert salida["extras"]["omitted_quantity"] == 2     # traza forense


def test_factor_escalonado_mayor_resto():
    """[3,7] con f=0.5 → total objetivo 5, mayor resto → [2,3]."""
    b = PayloadBuilder()
    se = {"mode": "execute", "quantities": [3, 7], "levels": [7.0],
          "max_micro_contracts": 10}
    legs = b.build_scaled(
        _make_signal("sell", "short", role="entry_short"), None,
        {"scale_entry": se, "short_size_factor": 0.5}, _result(sl=5590.0))
    assert [x["quantity"] for x in legs] == [2, 3]


def test_factor_escalonado_balanceada_someras_primero():
    """Balanceada (10×1) con f=0.5 → quedan las 5 piernas SOMERAS
    (participación primero — prioridad declarada del estudio)."""
    b = PayloadBuilder()
    legs = b.build_scaled(
        _make_signal("sell", "short", role="entry_short"), None,
        {"scale_entry": dict(BALANCEADA), "short_size_factor": 0.5},
        _result(sl=5590.0))
    assert sum(x["quantity"] for x in legs) == 5
    assert [x["extras"]["level_atr"] for x in legs] == [0.5, 1.0, 2.0,
                                                        3.0, 3.5]


def test_factor_escalonado_no_toca_largos():
    b = PayloadBuilder()
    legs = b.build_scaled(
        _make_signal("buy", "long"), None,
        {"scale_entry": dict(BALANCEADA), "short_size_factor": 0.5},
        _result())
    assert sum(x["quantity"] for x in legs) == 10


def test_sin_factor_simetrico_retrocompat():
    b = PayloadBuilder()
    p = b.build(_make_signal("sell", "short", quantity=2,
                             role="entry_short"), None, {},
                _result(sl=5590.0))
    assert p["quantity"] == 2


def test_factor_invalido_ignorado():
    b = PayloadBuilder()
    for malo in (0, 1.5, -0.5, True, "0.5"):
        p = b.build(_make_signal("sell", "short", quantity=2,
                                 role="entry_short"), None,
                    {"short_size_factor": malo}, _result(sl=5590.0))
        assert p["quantity"] == 2, f"factor={malo!r}"


# ---------------------------------------------------------------------------
# Resolver + e2e con los TRES mecanismos juntos
# ---------------------------------------------------------------------------

async def _seed(db: AsyncSession, pipeline_json: dict | None) -> None:
    db.add(Strategy(strategy_id="mr5c", name="M", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id="mr5c", mode="paper",
                           pipeline_config_json=pipeline_json))
    await db.commit()


@pytest.mark.asyncio
async def test_resolver_short_size_factor(db: AsyncSession):
    await _seed(db, {"short_size_factor": 0.5})
    cfg = await ConfigResolver().resolve(db, "mr5c", "MES")
    assert cfg["short_size_factor"] == 0.5


@pytest.mark.asyncio
async def test_resolver_factor_ausente_o_invalido(db: AsyncSession):
    await _seed(db, {"short_size_factor": 1.5})
    cfg = await ConfigResolver().resolve(db, "mr5c", "MES")
    assert cfg.get("short_size_factor") is None          # inválido no entra


@pytest.mark.asyncio
async def test_e2e_los_tres_mecanismos_juntos(
    db: AsyncSession, market_data_service
) -> None:
    """Backstop + TP nominal + escalera + asimetría en un corto: SL fijo
    señal+90, TP nominal señal−8×ATR, 5 piernas someras límite — el JSON
    de activación de ES completo, de la señal al payload."""
    pipeline = FilterPipeline(market_data_service)
    config = {
        "mode": "normal", "score_minimum": 70,
        "backstop_points": 90,
        "tp_nominal_long": 11.5, "tp_nominal_short": 8.0,
        "short_size_factor": 0.5,
        "scale_entry": dict(BALANCEADA),
    }
    signal = _make_signal("sell", "short", 5500.0, quantity=1,
                          role="entry_short")
    with patch(
        "app.services.session_validator.SessionValidator.is_within_session_config",
        return_value=True,
    ):
        result = await pipeline.evaluate(db, signal, _make_strategy_live(),
                                         config)
    assert result.outcome == "APPROVE"
    assert result.sl_price == 5500.0 + 90                # backstop fijo
    assert result.tp_price == 5500.0 - 8.0 * 8.0         # TP nominal corto

    legs = PayloadBuilder().build_scaled(signal, None, config, result)
    assert sum(x["quantity"] for x in legs) == 5         # corto reducido
    for leg in legs:
        assert leg["stopLoss"]["stopPrice"] == 5590.0
        assert leg["takeProfit"]["limitPrice"] == 5436.0
        assert leg["orderType"] == "limit"
        assert leg["limitPrice"] > 5500.0                # pullback corto


def _make_strategy_live() -> Strategy:
    return Strategy(strategy_id="test_strat", name="Test", asset_symbol="MES",
                    status="live", enabled=True)
