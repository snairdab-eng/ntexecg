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
    {"key": RULE_NO_STACK_GROUP, "n": 2, "implemented": True,
     "label": "No apilar el mismo grupo/clase",
     "desc": "Índices ES/NQ/RTY/YM · metales GC · FX 6E/6J · energía CL "
             "van en cardumen."},
    {"key": RULE_MAX_RISK_USD, "n": 3, "implemented": True,
     "label": "Tope de riesgo agregado ($)",
     "desc": "Suma del peor-caso de lo abierto ≤ tope configurable."},
    {"key": RULE_MAX_MICROS, "n": 4, "implemented": True,
     "label": "Tope de micros totales",
     "desc": "Suma de microcontratos abiertos ≤ N."},
    {"key": RULE_MAX_DAILY_LOSS, "n": 5, "implemented": True,
     "label": "Tope de pérdida diaria del portafolio",
     "desc": "Pérdida realizada del día > X → se para todo."},
    {"key": RULE_MAX_POSITIONS, "n": 6, "implemented": True,
     "label": "Máx. nº de posiciones simultáneas",
     "desc": "Nº de posiciones abiertas ≤ N."},
    {"key": RULE_COOLDOWN_LOSS, "n": 7, "implemented": True,
     "label": "Enfriamiento tras pérdida grande",
     "desc": "Tras pegar el backstop, pausa entradas nuevas N minutos."},
    {"key": RULE_GROUP_BIAS, "n": 8, "implemented": True,
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


# Grupos de correlación (P-B regla 2/8, confirmado por el operador 2026-07-10):
# índices ES/NQ/RTY/YM (+micros vía raíz del Symbol Mapper) · metales GC ·
# FX 6E/6J · energía CL. La raíz ya colapsa micros→padre (MES→ES).
GROUPS: dict[str, set[str]] = {
    "indices": {"ES", "NQ", "RTY", "YM"},
    "metales": {"GC"},
    "fx": {"6E", "6J"},
    "energia": {"CL"},
}
_ROOT_TO_GROUP = {root: g for g, roots in GROUPS.items() for root in roots}


def group_of(root: str | None) -> str | None:
    """Grupo de correlación de un activo raíz (None si no está catalogado)."""
    return _ROOT_TO_GROUP.get(root) if root else None


# Parámetros por regla (inertes hasta que su regla se encienda). Merge sobre
# esto lo persistido en PortfolioConfig.params_json.
DEFAULT_PARAMS: dict = {
    "rule_3_max_risk_usd": 10_000.0,        # tope del peor-caso agregado
    "rule_4_max_micros": 10,                # tope de micros totales abiertos
    "rule_5_max_daily_loss_usd": 2_000.0,   # pérdida realizada del día
    "rule_6_max_positions": 3,              # posiciones simultáneas
    "rule_7_cooldown_min": 30,              # enfriamiento tras pérdida grande
    "rule_7_loss_threshold_usd": 1_000.0,   # qué cuenta como "pérdida grande"
    "rule_8_group_bias_max": 2,             # posiciones del grupo en un lado
}


def merge_params(params_json: dict | None) -> dict:
    merged = dict(DEFAULT_PARAMS)
    if isinstance(params_json, dict):
        for k, v in params_json.items():
            if k in merged and isinstance(v, (int, float)):
                merged[k] = v
    return merged


async def load_params(db: AsyncSession) -> dict:
    from app.services.repositories import get_portfolio_config

    cfg = await get_portfolio_config(db)
    return merge_params(cfg.params_json if cfg is not None else None)


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


def _incoming_qty(config: dict, signal: NormalizedSignal) -> int:
    """Micros de la señal entrante: total del scale_entry (sin la pierna a 0) o
    la cantidad de la señal, mínimo 1."""
    qs = (config.get("scale_entry") or {}).get("quantities") or []
    total = sum(int(q) for q in qs if q and int(q) > 0)
    if total > 0:
        return total
    return max(1, int(getattr(signal, "quantity", None) or 1))


def _incoming_direction(signal: NormalizedSignal) -> str:
    return "long" if signal.action == "buy" else "short"


def _incoming_worst_case(config: dict, qty: int) -> float | None:
    """Peor-caso $ de la entrada nueva = qty micros parando en el backstop.
    REUSA `position_sizing.worst_case_loss` (helper de L4 — no se duplica la
    fórmula). None si falta backstop o el PV del catálogo (fail-closed)."""
    sl = config.get("backstop_points")
    tv = config.get("tick_value")
    ts = config.get("tick_size")
    if not sl or not tv or not ts:
        return None
    pv = float(tv) / float(ts)                 # $/punto del contrato despachado
    from app.services.position_sizing import worst_case_loss
    return worst_case_loss(float(sl), [0.0], [int(qty)], pv)


class PortfolioGuard:
    """Guardarraíl de riesgo de portafolio (L3). Solo evalúa reglas encendidas."""

    async def check_entry(
        self,
        db: AsyncSession,
        signal: NormalizedSignal,
        config: dict,
        rules: dict[str, bool] | None = None,
        params: dict | None = None,
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

        # Regla 1 — no apilar el mismo activo.
        if rules.get(RULE_NO_STACK_ASSET):
            r1 = await self._rule_no_stack_asset(db, signal, config)
            if r1["failed"]:
                return r1

        # Reglas 2–8 (P-B): codificadas; corren SOLO si están encendidas. Al
        # nacer todas OFF → este bloque no toca nada (decisión idéntica a P-A).
        pos_rules = (RULE_NO_STACK_GROUP, RULE_MAX_RISK_USD, RULE_MAX_MICROS,
                     RULE_MAX_POSITIONS, RULE_GROUP_BIAS)
        hist_rules = (RULE_MAX_DAILY_LOSS, RULE_COOLDOWN_LOSS)
        active = [k for k in pos_rules + hist_rules if rules.get(k)]
        if not active:
            return {"failed": False}
        if params is None:
            params = await load_params(db)

        # Snapshot de lo abierto (una sola lectura) para las reglas de posición.
        if any(k in active for k in pos_rules):
            try:
                snap = await self._snapshot(db, signal, config)
            except Exception as exc:
                logger.error("portfolio_snapshot_unreadable error={}", exc)
                return {"failed": True, "reason": "portfolio_state_unreadable",
                        "check": "3.5_portfolio",
                        "message": "estado de posiciones/catálogo no legible "
                                   "(fail-closed)"}
            for k in pos_rules:
                if k not in active:
                    continue
                r = self._POS_RULES[k](self, snap, signal, config, params)
                if r["failed"]:
                    return r

        if RULE_MAX_DAILY_LOSS in active:
            r = await self._rule_max_daily_loss(db, config, params)
            if r["failed"]:
                return r
        if RULE_COOLDOWN_LOSS in active:
            r = await self._rule_cooldown_after_loss(db, config, params)
            if r["failed"]:
                return r
        return {"failed": False}

    # ── snapshot de exposición (reglas 2/3/4/6/8) ──
    async def _snapshot(self, db: AsyncSession, signal: NormalizedSignal,
                        config: dict) -> dict:
        account_id = config.get("account_id", "paper_default")
        own = signal.mapped_symbol
        idx = await _build_root_index(db)
        result = await db.execute(select(PositionState).where(
            PositionState.account_id == account_id,
            PositionState.state != _FLAT))
        rows = []
        for p in result.scalars().all():
            if p.symbol == own:
                continue                       # el propio símbolo lo ve symbol_busy
            root = idx.root_for_position(p)
            if root is None:
                root = await _root_via_strategy(db, p, idx)
            rows.append({
                "symbol": p.symbol, "strategy_id": p.strategy_id,
                "root": root, "group": group_of(root),
                "qty": int(p.quantity or 0),
                "direction": (p.direction or "").lower() or None,
                "worst_case": ((p.risk_plan_json or {}) or {}).get(
                    "worst_case_usd"),
            })
        return {"incoming_root": idx.root_for_ticker(signal.ticker_received),
                "rows": rows}

    @staticmethod
    def _fail(rule: str, reason: str, message: str, **extra) -> dict:
        return {"failed": True, "reason": reason, "check": "3.5_portfolio",
                "rule": rule, "message": message, **extra}

    # ── Regla 2 — no apilar el mismo GRUPO/clase ──
    def _rule_no_stack_group(self, snap, signal, config, params) -> dict:
        g = group_of(snap["incoming_root"])
        if g is None:
            return self._fail(RULE_NO_STACK_GROUP, "portfolio_group_unknown",
                              "grupo del activo de la señal indeterminado "
                              "(fail-closed)")
        for r in snap["rows"]:
            if r["group"] is None:
                return self._fail(RULE_NO_STACK_GROUP, "portfolio_exposure_unknown",
                                  f"posición abierta de grupo indeterminado "
                                  f"({r['symbol']}) — fail-closed",
                                  unknown_symbol=r["symbol"])
            if r["group"] == g:
                return self._fail(RULE_NO_STACK_GROUP, "portfolio_group_busy",
                                  f"el grupo {g} ya tiene posición "
                                  f"({r['root']})", group=g,
                                  holder_symbol=r["symbol"])
        return {"failed": False}

    # ── Regla 3 — tope de riesgo agregado ($), peor-caso ──
    def _rule_max_risk(self, snap, signal, config, params) -> dict:
        tope = params["rule_3_max_risk_usd"]
        total = 0.0
        for r in snap["rows"]:
            wc = r["worst_case"]
            if wc is None:                     # no computable → fail-closed
                return self._fail(RULE_MAX_RISK_USD, "portfolio_risk_unknown",
                                  f"peor-caso de {r['symbol']} no disponible "
                                  f"(fail-closed)", unknown_symbol=r["symbol"])
            total += float(wc)
        inc = _incoming_worst_case(config, _incoming_qty(config, signal))
        if inc is None:
            return self._fail(RULE_MAX_RISK_USD, "portfolio_risk_unknown",
                              "peor-caso de la señal no computable (sin "
                              "backstop/PV) — fail-closed")
        if total + inc > tope:
            return self._fail(RULE_MAX_RISK_USD, "portfolio_risk_cap",
                              f"peor-caso agregado ${total + inc:,.0f} > tope "
                              f"${tope:,.0f}", worst_case_open=round(total, 2),
                              worst_case_incoming=round(inc, 2), cap=tope)
        return {"failed": False}

    # ── Regla 4 — tope de micros totales ──
    def _rule_max_micros(self, snap, signal, config, params) -> dict:
        cap = int(params["rule_4_max_micros"])
        open_micros = sum(r["qty"] for r in snap["rows"])
        inc = _incoming_qty(config, signal)
        if open_micros + inc > cap:
            return self._fail(RULE_MAX_MICROS, "portfolio_micros_cap",
                              f"{open_micros}+{inc} micros > tope {cap}",
                              open_micros=open_micros, incoming=inc, cap=cap)
        return {"failed": False}

    # ── Regla 6 — máx nº de posiciones simultáneas ──
    def _rule_max_positions(self, snap, signal, config, params) -> dict:
        cap = int(params["rule_6_max_positions"])
        n_open = len(snap["rows"])
        if n_open + 1 > cap:
            return self._fail(RULE_MAX_POSITIONS, "portfolio_positions_cap",
                              f"{n_open} posiciones abiertas + 1 > tope {cap}",
                              open_positions=n_open, cap=cap)
        return {"failed": False}

    # ── Regla 8 — sesgo direccional del grupo ──
    def _rule_group_bias(self, snap, signal, config, params) -> dict:
        cap = int(params["rule_8_group_bias_max"])
        g = group_of(snap["incoming_root"])
        if g is None:
            return self._fail(RULE_GROUP_BIAS, "portfolio_group_unknown",
                              "grupo de la señal indeterminado (fail-closed)")
        side = _incoming_direction(signal)
        same = sum(1 for r in snap["rows"]
                   if r["group"] == g and r["direction"] == side)
        if same >= cap:
            return self._fail(RULE_GROUP_BIAS, "portfolio_group_bias",
                              f"el grupo {g} ya va {same} en {side} (tope {cap})",
                              group=g, side=side, same_side=same, cap=cap)
        return {"failed": False}

    # ── Regla 5 — tope de pérdida diaria del portafolio ──
    async def _rule_max_daily_loss(self, db, config, params) -> dict:
        from datetime import datetime, timezone

        from app.models.execution_result import ExecutionResult
        cap = params["rule_5_max_daily_loss_usd"]
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                   microsecond=0)
        try:
            rows = (await db.execute(select(ExecutionResult.pnl).where(
                ExecutionResult.exit_time >= start))).scalars().all()
        except Exception as exc:
            logger.error("portfolio_daily_loss_unreadable error={}", exc)
            return self._fail(RULE_MAX_DAILY_LOSS, "portfolio_state_unreadable",
                              "pérdida diaria no legible (fail-closed)")
        realized = sum(float(p) for p in rows if p is not None)
        if realized < -abs(cap):
            return self._fail(RULE_MAX_DAILY_LOSS, "portfolio_daily_loss",
                              f"pérdida realizada del día ${realized:,.0f} "
                              f"supera el tope ${abs(cap):,.0f} — se para todo",
                              realized_usd=round(realized, 2), cap=abs(cap))
        return {"failed": False}

    # ── Regla 7 — enfriamiento tras pérdida grande ──
    async def _rule_cooldown_after_loss(self, db, config, params) -> dict:
        from datetime import datetime, timedelta, timezone

        from app.models.execution_result import ExecutionResult
        mins = params["rule_7_cooldown_min"]
        thr = abs(params["rule_7_loss_threshold_usd"])
        since = datetime.now(timezone.utc) - timedelta(minutes=mins)
        try:
            rows = (await db.execute(select(
                ExecutionResult.pnl, ExecutionResult.exit_time).where(
                ExecutionResult.exit_time >= since))).all()
        except Exception as exc:
            logger.error("portfolio_cooldown_unreadable error={}", exc)
            return self._fail(RULE_COOLDOWN_LOSS, "portfolio_state_unreadable",
                              "historial de cierres no legible (fail-closed)")
        for pnl, _t in rows:
            if pnl is not None and float(pnl) <= -thr:
                return self._fail(RULE_COOLDOWN_LOSS, "portfolio_cooldown",
                                  f"enfriamiento: pérdida ≥ ${thr:,.0f} en los "
                                  f"últimos {mins} min — pausa de entradas",
                                  cooldown_min=mins, threshold=thr)
        return {"failed": False}

    # Despacho de las reglas basadas en el snapshot (sin await — el snapshot ya
    # se leyó). Se define tras los métodos para referenciarlos.
    _POS_RULES = {
        RULE_NO_STACK_GROUP: _rule_no_stack_group,
        RULE_MAX_RISK_USD: _rule_max_risk,
        RULE_MAX_MICROS: _rule_max_micros,
        RULE_MAX_POSITIONS: _rule_max_positions,
        RULE_GROUP_BIAS: _rule_group_bias,
    }

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
