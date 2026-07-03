"""Weekly execution-results import: reconciliation + real metrics (Fase 8).

Takes the manually-filled weekly report (one row per closed trade) and:
  1. Stores each trade in execution_results (idempotent via row_hash).
  2. Computes pnl_calc from prices + the instrument tick value when pnl is blank.
  3. Reconciles each trade against what NTEXECG actually SENT (WebhookDelivery):
       - exact   : CSV signal_id == payload extras.signal_id
       - heuristic: same base symbol + direction, sent within a time window
  4. compute_real_metrics() aggregates REAL per-strategy performance.

NTEXECG has no live P&L; this closes the loop weekly so strategy promotion,
filter/regime calibration and reconciliation are based on real outcomes.
"""
from __future__ import annotations

import csv
import hashlib
import io
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution_result import ExecutionResult
from app.models.webhook_delivery import WebhookDelivery
from app.services.market_data_service import _CONTRACT_SUFFIX_RE
from app.services.repositories import get_active_symbol_map

_MATCH_WINDOW = timedelta(minutes=15)
_CSV_COLUMNS = (
    "signal_id", "strategy_id", "symbol", "direction", "quantity",
    "entry_time", "entry_price", "exit_time", "exit_price",
    "pnl", "exit_reason", "fees",
)


def _base_symbol(sym: str | None) -> str:
    return _CONTRACT_SUFFIX_RE.sub("", (sym or "").strip())


def _norm_dir(value: str | None) -> str | None:
    v = (value or "").strip().lower()
    if v in ("long", "buy"):
        return "long"
    if v in ("short", "sell"):
        return "short"
    return None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    s = str(value).strip().replace("T", " ")
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s[: len(fmt) + 2], fmt)
        except ValueError:
            continue
    return None


def _f(value: object) -> float | None:
    try:
        s = str(value).strip()
        return float(s) if s != "" else None
    except (ValueError, TypeError):
        return None


def _row_hash(d: dict) -> str:
    key = "|".join(str(d.get(k, "") or "") for k in (
        "signal_id", "symbol", "direction", "entry_time",
        "entry_price", "quantity", "exit_time", "exit_price",
    ))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def parse_rows(text: str) -> list[dict]:
    """Parse CSV text (with header) into a list of dict rows."""
    return list(csv.DictReader(io.StringIO(text)))


def effective_pnl(r: ExecutionResult) -> float | None:
    """Prefer the provided P&L; fall back to the computed one."""
    if r.pnl is not None:
        return float(r.pnl)
    if r.pnl_calc is not None:
        return float(r.pnl_calc)
    return None


async def _tick_info(db: AsyncSession, symbol: str, cache: dict) -> tuple[float | None, float | None]:
    base = _base_symbol(symbol)
    if base in cache:
        return cache[base]
    sm = await get_active_symbol_map(db, base)
    info = (
        (float(sm.tick_size) if sm and sm.tick_size is not None else None),
        (float(sm.tick_value) if sm and sm.tick_value is not None else None),
    )
    cache[base] = info
    return info


def _compute_pnl(direction, entry, exit_, qty, tick_size, tick_value) -> float | None:
    if None in (entry, exit_, tick_size, tick_value) or not tick_size:
        return None
    points = (exit_ - entry) if direction == "long" else (entry - exit_)
    return round((points / tick_size) * tick_value * (qty or 1), 2)


async def import_results(db: AsyncSession, rows: list[dict]) -> dict:
    """Import + reconcile rows. Returns a summary dict. Idempotent by row_hash."""
    parsed: list[tuple[dict, ExecutionResult]] = []
    tick_cache: dict = {}
    skipped_existing = 0
    skipped_invalid = 0

    for raw in rows:
        symbol = (raw.get("symbol") or "").strip()
        direction = _norm_dir(raw.get("direction"))
        if not symbol or direction is None:
            skipped_invalid += 1
            continue
        rh = _row_hash(raw)
        exists = await db.execute(
            select(ExecutionResult.id).where(ExecutionResult.row_hash == rh)
        )
        if exists.scalar_one_or_none() is not None:
            skipped_existing += 1
            continue

        qty = int(_f(raw.get("quantity")) or 1)
        entry_price = _f(raw.get("entry_price"))
        exit_price = _f(raw.get("exit_price"))
        ts, tv = await _tick_info(db, symbol, tick_cache)
        pnl_calc = _compute_pnl(direction, entry_price, exit_price, qty, ts, tv)

        er = ExecutionResult(
            row_hash=rh,
            signal_id=(raw.get("signal_id") or "").strip() or None,
            strategy_id=(raw.get("strategy_id") or "").strip() or None,
            symbol=symbol,
            direction=direction,
            quantity=qty,
            entry_time=_parse_dt(raw.get("entry_time")),
            entry_price=entry_price,
            exit_time=_parse_dt(raw.get("exit_time")),
            exit_price=exit_price,
            pnl=_f(raw.get("pnl")),
            pnl_calc=pnl_calc,
            exit_reason=(raw.get("exit_reason") or "").strip() or None,
            fees=_f(raw.get("fees")),
            match_method="unmatched",
        )
        parsed.append((raw, er))

    # Reconcile against SENT deliveries within the batch's time range.
    await _reconcile(db, [er for _, er in parsed])

    for _, er in parsed:
        db.add(er)
    await db.flush()

    # NX-18 Fase A — cerrar el lazo del estado estimado con los trades
    # conciliados EXACTO (nada especulativo).
    positions_reconciled = await _reconcile_positions(
        db, [er for _, er in parsed]
    )

    matched_signal = sum(1 for _, er in parsed if er.match_method == "signal_id")
    matched_heur = sum(1 for _, er in parsed if er.match_method == "heuristic")
    return {
        "imported": len(parsed),
        "skipped_existing": skipped_existing,
        "skipped_invalid": skipped_invalid,
        "matched_signal_id": matched_signal,
        "matched_heuristic": matched_heur,
        "unmatched": len(parsed) - matched_signal - matched_heur,
        "positions_reconciled": positions_reconciled,
    }


async def _reconcile(db: AsyncSession, results: list[ExecutionResult]) -> None:
    if not results:
        return
    entry_times = [r.entry_time for r in results if r.entry_time]
    lo = min(entry_times) - timedelta(days=1) if entry_times else None
    hi = max(entry_times) + timedelta(days=1) if entry_times else None

    stmt = select(WebhookDelivery).where(WebhookDelivery.status == "SENT")
    if lo and hi:
        stmt = stmt.where(WebhookDelivery.sent_at >= lo, WebhookDelivery.sent_at <= hi)
    res = await db.execute(stmt)

    # Build candidate index from each delivery's payload.
    candidates = []  # (signal_id, base_symbol, direction, sent_at, decision_id, used_flag[list])
    by_signal: dict[str, dict] = {}
    for d in res.scalars().all():
        payload = d.payload_json or {}
        extras = payload.get("extras") or {}
        action = (payload.get("action") or "").lower()
        direction = "long" if action == "buy" else "short" if action == "sell" else None
        if direction is None:
            continue  # skip exits
        cand = {
            "signal_id": extras.get("signal_id"),
            "base": _base_symbol(payload.get("ticker")),
            "direction": direction,
            "sent_at": d.sent_at,
            "decision_id": d.decision_id,
            "used": False,
        }
        candidates.append(cand)
        if cand["signal_id"]:
            by_signal[str(cand["signal_id"])] = cand

    for r in results:
        # 1) exact by signal_id
        if r.signal_id and str(r.signal_id) in by_signal:
            c = by_signal[str(r.signal_id)]
            if not c["used"]:
                c["used"] = True
                r.matched_decision_id = c["decision_id"]
                r.match_method = "signal_id"
                continue
        # 2) heuristic: base symbol + direction + nearest sent_at within window
        base = _base_symbol(r.symbol)
        best = None
        best_gap = None
        for c in candidates:
            if c["used"] or c["base"] != base or c["direction"] != r.direction:
                continue
            if r.entry_time is None or c["sent_at"] is None:
                continue
            st = c["sent_at"].replace(tzinfo=None) if c["sent_at"].tzinfo else c["sent_at"]
            gap = abs((st - r.entry_time).total_seconds())
            if gap <= _MATCH_WINDOW.total_seconds() and (best_gap is None or gap < best_gap):
                best, best_gap = c, gap
        if best is not None:
            best["used"] = True
            r.matched_decision_id = best["decision_id"]
            r.match_method = "heuristic"


_RECONCILABLE_STATES = ("PENDING_LONG", "PENDING_SHORT", "LONG", "SHORT",
                        "EXITING")


async def _reconcile_positions(db: AsyncSession, results: list) -> int:
    """NX-18 Fase A — pone FLAT el estado estimado SOLO con certeza total:

      1. el trade concilió EXACTO por signal_id (match_method == "signal_id"),
      2. el trade está CERRADO (exit_time presente), y
      3. la posición fue abierta por ESA señal (entry_signal_id == signal_id).

    Match heurístico, trades abiertos, o posiciones reabiertas por otra señal
    NO se tocan — nada especulativo. Audit RECONCILE por posición cerrada.
    """
    from app.models.position_state import PositionState
    from app.services.audit_service import AuditService

    n = 0
    for r in results:
        if r.match_method != "signal_id" or r.exit_time is None or not r.signal_id:
            continue
        try:
            sig_uuid = uuid.UUID(str(r.signal_id))
        except (ValueError, AttributeError):
            continue
        pos = (await db.execute(
            select(PositionState).where(
                PositionState.entry_signal_id == sig_uuid)
        )).scalar_one_or_none()
        if pos is None or pos.state not in _RECONCILABLE_STATES:
            continue

        old_state = pos.state
        pos.state = "FLAT"
        pos.quantity = 0
        pos.direction = None
        pos.entry_price = None
        pos.entry_signal_id = None
        plan = dict(pos.risk_plan_json or {})
        plan["reconciled"] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "signal_id": str(sig_uuid),
            "exit_time": str(r.exit_time),
            "source": "results_import",
        }
        pos.risk_plan_json = plan
        await db.flush()
        await AuditService().log(
            db, actor="results_import", action="RECONCILE",
            object_type="PositionState",
            object_id=f"{pos.account_id}:{pos.symbol}",
            old_value={"state": old_state},
            new_value={"state": "FLAT", "signal_id": str(sig_uuid),
                       "exit_time": str(r.exit_time)},
        )
        n += 1
    return n


async def compute_real_metrics(db: AsyncSession, strategy_id: str | None = None) -> dict:
    """Aggregate REAL per-strategy metrics from execution_results."""
    stmt = select(ExecutionResult)
    if strategy_id:
        stmt = stmt.where(ExecutionResult.strategy_id == strategy_id)
    rows = list((await db.execute(stmt)).scalars().all())

    by_strat: dict[str, list[ExecutionResult]] = {}
    for r in rows:
        by_strat.setdefault(r.strategy_id or "(sin estrategia)", []).append(r)

    out: dict[str, dict] = {}
    for sid, trades in by_strat.items():
        pnls = [effective_pnl(t) for t in trades]
        pnls = [p for p in pnls if p is not None]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        n = len(pnls)
        matched = sum(1 for t in trades if t.match_method != "unmatched")

        # Max drawdown over the cumulative P&L curve (ordered by exit_time).
        ordered = sorted(
            [t for t in trades if effective_pnl(t) is not None],
            key=lambda t: (t.exit_time or t.entry_time or datetime.min),
        )
        peak = cum = max_dd = 0.0
        for t in ordered:
            cum += effective_pnl(t)
            peak = max(peak, cum)
            max_dd = min(max_dd, cum - peak)

        out[sid] = {
            "trades": len(trades),
            "with_pnl": n,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / n * 100, 1) if n else None,
            "total_pnl": round(sum(pnls), 2) if n else None,
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
            "expectancy": round(sum(pnls) / n, 2) if n else None,
            "max_drawdown": round(max_dd, 2),
            "reconciled": matched,
            "reconciled_pct": round(matched / len(trades) * 100, 1) if trades else None,
        }
    return out
