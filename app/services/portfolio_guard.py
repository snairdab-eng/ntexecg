"""PortfolioGuard — guardarraíl L3 de riesgo AGREGADO (Módulo de Portafolio, P-A).

NTEXECG controla el riesgo por trade (backstop) y por estrategia (protección).
Este módulo añade la capa que faltaba: cuánto se arriesga a la vez sumando todo
lo abierto entre TODAS las estrategias. Es un marco de reglas con interruptor
(las 8 reglas del catálogo viven en `PortfolioConfig`; al nacer solo la regla 1
está encendida). Corre como guardarraíl **L3**, junto a `symbol_busy`, y es
**FAIL-CLOSED**: si el agregado no es computable (estado de posiciones no legible
o el activo de una posición abierta no se puede determinar), BLOQUEA con motivo
visible en la decisión.

Diseño (SPEC_Luxy_Portafolio_Diseno §B, SPEC de lotes Parte B P-A):
- **Regla 1 — no apilar el mismo ACTIVO (ON):** una posición abierta por activo
  raíz (Symbol Mapper: MES/ES→ES) entre todas las estrategias, sin importar
  dirección. EXTIENDE `symbol_busy` de "por símbolo" a "por activo".
- **NO bloquea las piernas de la escalera:** el guard evalúa señales de ENTRADA
  nuevas. Las legs de una posición viajan en el despacho multi-leg de SU propia
  señal (`_dispatch_approved`) — nunca vuelven al pipeline como señales nuevas,
  así que el guard jamás las ve. La posición propia (mismo símbolo) queda a cargo
  de `symbol_busy` (con su exención de reversal); el guard SOLO mira OTROS
  símbolos del mismo activo.
- Reusa `PositionState` (lo abierto) y el Symbol Mapper (agrupar por activo). No
  inventa estado nuevo.

Reglas 2–8: definidas en el catálogo pero INERTES (se codifican en P-B).
"""
from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.normalized_signal import NormalizedSignal
from app.models.position_state import PositionState
from app.models.symbol_map import SymbolMap

# Claves de las 8 reglas (orden del catálogo B.2). Solo la 1 ON al nacer.
RULE_NO_STACK_ASSET = "rule_1_no_stack_asset"
RULE_NO_STACK_GROUP = "rule_2_no_stack_group"
RULE_MAX_RISK_USD = "rule_3_max_risk_usd"
RULE_MAX_MICROS = "rule_4_max_micros"
RULE_MAX_DAILY_LOSS = "rule_5_max_daily_loss"
RULE_MAX_POSITIONS = "rule_6_max_positions"
RULE_COOLDOWN_LOSS = "rule_7_cooldown_after_loss"
RULE_GROUP_BIAS = "rule_8_group_bias"

# Estado ON/OFF por default: SOLO la regla 1 encendida. Las demás inertes.
DEFAULT_RULES: dict[str, bool] = {
    RULE_NO_STACK_ASSET: True,
    RULE_NO_STACK_GROUP: False,
    RULE_MAX_RISK_USD: False,
    RULE_MAX_MICROS: False,
    RULE_MAX_DAILY_LOSS: False,
    RULE_MAX_POSITIONS: False,
    RULE_COOLDOWN_LOSS: False,
    RULE_GROUP_BIAS: False,
}

# Metadatos para la UI (etiqueta + descripción + estado esperado). El nº es el
# del catálogo B.2. `implemented` distingue la regla viva (1) de las inertes.
RULE_META: list[dict] = [
    {"key": RULE_NO_STACK_ASSET, "n": 1, "implemented": True,
     "label": "No apilar el mismo activo",
     "desc": "Una posición abierta por activo (MES/ES→ES) entre todas las "
             "estrategias, sin importar dirección. No bloquea las piernas de "
             "la escalera; solo entradas nuevas independientes."},
    {"key": RULE_NO_STACK_GROUP, "n": 2, "implemented": False,
     "label": "No apilar el mismo grupo/clase",
     "desc": "Índices ES/NQ/RTY/YM · metales GC · FX 6E/6J · energía CL "
             "van en cardumen."},
    {"key": RULE_MAX_RISK_USD, "n": 3, "implemented": False,
     "label": "Tope de riesgo agregado ($)",
     "desc": "Suma del peor-caso de lo abierto ≤ tope configurable."},
    {"key": RULE_MAX_MICROS, "n": 4, "implemented": False,
     "label": "Tope de micros totales",
     "desc": "Suma de microcontratos abiertos ≤ N."},
    {"key": RULE_MAX_DAILY_LOSS, "n": 5, "implemented": False,
     "label": "Tope de pérdida diaria del portafolio",
     "desc": "Pérdida realizada del día > X → se para todo."},
    {"key": RULE_MAX_POSITIONS, "n": 6, "implemented": False,
     "label": "Máx. nº de posiciones simultáneas",
     "desc": "Nº de posiciones abiertas ≤ N."},
    {"key": RULE_COOLDOWN_LOSS, "n": 7, "implemented": False,
     "label": "Enfriamiento tras pérdida grande",
     "desc": "Tras pegar el backstop, pausa entradas nuevas N minutos."},
    {"key": RULE_GROUP_BIAS, "n": 8, "implemented": False,
     "label": "Sesgo direccional del grupo",
     "desc": "Si el cardumen ya va N en una dirección, no sumar más."},
]

# Un símbolo "ocupa" el activo si NO está plano. Fail-closed: cualquier estado no
# FLAT (incl. transitorios, LOCKED, UNKNOWN) cuenta como exposición del activo —
# la regla es "una posición por activo", y un estado incierto no se puede
# descartar como plano.
_FLAT = "FLAT"


def merge_rules(rules_json: dict | None) -> dict[str, bool]:
    """DEFAULT_RULES con el override persistido encima (tolera reglas nuevas)."""
    merged = dict(DEFAULT_RULES)
    if isinstance(rules_json, dict):
        for k, v in rules_json.items():
            if k in merged and isinstance(v, bool):
                merged[k] = v
    return merged


async def load_rules(db: AsyncSession) -> dict[str, bool]:
    """Banderas efectivas desde la fila activa de PortfolioConfig (o defaults)."""
    from app.services.repositories import get_portfolio_config

    cfg = await get_portfolio_config(db)
    return merge_rules(cfg.rules_json if cfg is not None else None)


@dataclass
class _RootIndex:
    """Mapa activo-raíz construido de una sola pasada por symbol_maps.

    root = market_data_symbol si está puesto (micro→padre, MES→ES), si no el
    propio tv_symbol (los padres se mapean a sí mismos). Mismo criterio que
    `SymbolMapper.resolve_market_data_symbol` y la tabla del bridge.
    """
    by_mapped: dict[str, str]  # contrato ("MESU2026") → raíz ("ES")
    by_tv: dict[str, str]      # tv_symbol ("MES")    → raíz ("ES")

    def root_for_ticker(self, ticker: str | None) -> str | None:
        if not ticker:
            return None
        # tv_symbol conocido → su raíz; desconocido → él mismo (padre implícito).
        return self.by_tv.get(ticker, ticker)

    def root_for_position(self, pos: PositionState) -> str | None:
        """Raíz de una posición abierta, o None si no es computable.

        Prioriza el contrato (position.symbol). Fallback por si el contrato rodó
        (ESU2025→ESZ2025) y el símbolo viejo ya no está en el catálogo: usa el
        activo de la estrategia dueña. None si no hay forma de saberlo → el
        llamador debe fail-closed.
        """
        root = self.by_mapped.get(pos.symbol)
        if root is not None:
            return root
        if pos.strategy_id:
            # strategy_id no es tv_symbol; la raíz por estrategia necesitaría el
            # asset_symbol de la estrategia — se resuelve en el llamador si hace
            # falta. Aquí solo el catálogo de símbolos.
            return None
        return None


async def _build_root_index(db: AsyncSession) -> _RootIndex:
    result = await db.execute(select(SymbolMap).where(SymbolMap.active.is_(True)))
    by_mapped: dict[str, str] = {}
    by_tv: dict[str, str] = {}
    for sm in result.scalars().all():
        root = sm.market_data_symbol or sm.tv_symbol
        by_mapped[sm.mapped_symbol] = root
        by_tv[sm.tv_symbol] = root
    return _RootIndex(by_mapped=by_mapped, by_tv=by_tv)


async def _root_via_strategy(db: AsyncSession, pos: PositionState,
                             idx: _RootIndex) -> str | None:
    """Fallback: raíz de una posición vía el activo de su estrategia dueña."""
    if not pos.strategy_id:
        return None
    from app.services.repositories import get_strategy_by_id

    strat = await get_strategy_by_id(db, pos.strategy_id)
    if strat is None or not strat.asset_symbol:
        return None
    return idx.root_for_ticker(strat.asset_symbol)


class PortfolioGuard:
    """Guardarraíl de riesgo de portafolio (L3). Solo evalúa reglas encendidas."""

    async def check_entry(
        self,
        db: AsyncSession,
        signal: NormalizedSignal,
        config: dict,
        rules: dict[str, bool] | None = None,
    ) -> dict:
        """Evalúa una señal de ENTRADA contra las reglas encendidas.

        Devuelve {"failed": bool, ...}. `failed=True` → BLOCK; el dict lleva
        `reason` (código estable) y `message` (motivo humano visible). Cuando
        ninguna regla encendida aplica, devuelve {"failed": False} sin tocar
        nada — con la regla APAGADA la decisión es idéntica al comportamiento
        anterior (ni siquiera escanea posiciones).

        FAIL-CLOSED: cualquier fallo al leer el estado o al determinar el activo
        de una posición abierta → BLOCK (no se puede computar el agregado).
        """
        if rules is None:
            rules = await load_rules(db)

        # Regla 1 — no apilar el mismo activo. Única regla viva en P-A.
        if rules.get(RULE_NO_STACK_ASSET):
            r1 = await self._rule_no_stack_asset(db, signal, config)
            if r1["failed"]:
                return r1

        # Reglas 2–8: inertes (P-B). No corren aunque estén encendidas.
        return {"failed": False}

    async def _rule_no_stack_asset(
        self, db: AsyncSession, signal: NormalizedSignal, config: dict
    ) -> dict:
        account_id = config.get("account_id", "paper_default")
        own_symbol = signal.mapped_symbol

        # FAIL-CLOSED en toda lectura: si no podemos leer el catálogo de
        # símbolos o el estado de posiciones, no hay agregado computable.
        try:
            idx = await _build_root_index(db)
        except Exception as exc:  # pragma: no cover - defensivo
            logger.error("portfolio_root_index_failed error={}", exc)
            return {"failed": True, "reason": "portfolio_state_unreadable",
                    "check": "3.5_portfolio", "rule": RULE_NO_STACK_ASSET,
                    "message": "no se pudo leer el catálogo de activos "
                               "(fail-closed)"}

        incoming_root = idx.root_for_ticker(signal.ticker_received)
        if incoming_root is None:
            # Sin raíz para la propia señal no se puede evaluar el agregado.
            return {"failed": True, "reason": "portfolio_state_unreadable",
                    "check": "3.5_portfolio", "rule": RULE_NO_STACK_ASSET,
                    "message": "activo de la señal indeterminado (fail-closed)"}

        try:
            result = await db.execute(
                select(PositionState).where(
                    PositionState.account_id == account_id,
                    PositionState.state != _FLAT,
                )
            )
            open_positions = list(result.scalars().all())
        except Exception as exc:
            logger.error("portfolio_positions_unreadable account={} error={}",
                         account_id, exc)
            return {"failed": True, "reason": "portfolio_state_unreadable",
                    "check": "3.5_portfolio", "rule": RULE_NO_STACK_ASSET,
                    "message": "estado de posiciones no legible (fail-closed)"}

        for pos in open_positions:
            # La posición propia (mismo símbolo) NO es asunto de esta regla —
            # de ella se encarga `symbol_busy` (con su exención de reversal).
            if pos.symbol == own_symbol:
                continue

            pos_root = idx.root_for_position(pos)
            if pos_root is None:
                pos_root = await _root_via_strategy(db, pos, idx)
            if pos_root is None:
                # Hay una posición abierta cuyo activo no podemos determinar:
                # no se puede descartar que sea el mismo activo → fail-closed.
                logger.warning(
                    "portfolio_exposure_unknown symbol={} strategy={}",
                    pos.symbol, pos.strategy_id)
                return {"failed": True, "reason": "portfolio_exposure_unknown",
                        "check": "3.5_portfolio", "rule": RULE_NO_STACK_ASSET,
                        "message": f"posición abierta de activo indeterminado "
                                   f"({pos.symbol}) — fail-closed",
                        "unknown_symbol": pos.symbol}

            if pos_root == incoming_root:
                return {"failed": True, "reason": "portfolio_asset_busy",
                        "check": "3.5_portfolio", "rule": RULE_NO_STACK_ASSET,
                        "message": f"{incoming_root} ya tiene posición",
                        "asset": incoming_root,
                        "holder_symbol": pos.symbol,
                        "holder_strategy": pos.strategy_id,
                        "holder_state": pos.state}

        return {"failed": False}


async def compute_exposure(db: AsyncSession, account_id: str = "paper_default") -> dict:
    """Vista de exposición en vivo para la config "Portafolio" (read-only).

    Agrupa las posiciones NO planas por activo raíz. Devuelve por activo la
    lista de posiciones (símbolo, estrategia, estado, dirección, qty) y el total
    de micros (Σ qty), más el total global de micros y el nº de activos ocupados.

    Peor-caso agregado en $ (regla 3): DIFERIDO — usará el helper compartido de
    L4 (`worst_case_loss`) cuando exista; aquí no se inventa.
    """
    idx = await _build_root_index(db)
    result = await db.execute(
        select(PositionState).where(
            PositionState.account_id == account_id,
            PositionState.state != _FLAT,
        )
    )
    positions = list(result.scalars().all())

    by_asset: dict[str, dict] = {}
    unknown: list[dict] = []
    total_micros = 0
    for pos in positions:
        root = idx.root_for_position(pos)
        if root is None:
            root = await _root_via_strategy(db, pos, idx)
        qty = int(pos.quantity or 0)
        total_micros += qty
        row = {
            "symbol": pos.symbol,
            "strategy_id": pos.strategy_id,
            "state": pos.state,
            "direction": pos.direction,
            "quantity": qty,
        }
        if root is None:
            unknown.append(row)
            continue
        entry = by_asset.setdefault(root, {"asset": root, "positions": [],
                                           "micros": 0})
        entry["positions"].append(row)
        entry["micros"] += qty

    assets = sorted(by_asset.values(), key=lambda e: e["asset"])
    return {
        "account_id": account_id,
        "assets": assets,
        "unknown": unknown,          # posiciones cuyo activo no se pudo resolver
        "total_micros": total_micros,
        "occupied_assets": len(assets),
    }
