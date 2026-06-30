"""Risk-profile (tier) resolution for multi-destination dispatch.

A strategy may define up to N risk profiles in pipeline_config_json["profiles"].
Each profile is a DELTA over the strategy's base config (the existing config):
it inherits everything and only overrides what it needs — typically the per-leg
contract quantities. Levels and SL/TP come from the research-backed base unless
explicitly overridden.

Backward compatible: no enabled profiles → a single implicit destination equal to
the base config (current behaviour, destination tag "traderspost").
"""
from __future__ import annotations


def cap_quantities(quantities: list[int], cap: int | None) -> list[int]:
    """Trim total contracts down to `cap`, removing from the FARTHEST leg first."""
    q = [int(x or 0) for x in (quantities or [])]
    if not cap or cap <= 0:
        return q

    def total(xs):
        return sum(x for x in xs if x > 0)

    guard = 0
    while total(q) > cap and guard < 10000:
        guard += 1
        for i in range(len(q) - 1, -1, -1):
            if q[i] > 0:
                q[i] -= 1
                break
    return q


def recompute_sl_tp(
    entry_price: float | None, atr: float | None, is_long: bool,
    sl_mult: float | None, tp_mult: float | None,
) -> tuple[float | None, float | None]:
    """Recompute SL/TP prices for a profile that overrides the multipliers."""
    if entry_price is None or atr is None or atr <= 0:
        return None, None
    sl = tp = None
    if sl_mult:
        sl = entry_price - atr * sl_mult if is_long else entry_price + atr * sl_mult
    if tp_mult:
        tp = entry_price + atr * tp_mult if is_long else entry_price - atr * tp_mult
    return sl, tp


def resolve_destinations(config: dict) -> list[dict]:
    """Return the list of effective dispatch destinations.

    Each destination dict: {name, webhook_url, scale_entry, sl_atr_multiplier,
    tp_atr_multiplier, dry_run, traderspost_enabled}. `name` is None for the
    implicit base destination (no profiles configured).
    """
    base_scale = config.get("scale_entry") or {}
    base_webhook = config.get("traderspost_webhook_url")
    base_sl = config.get("sl_atr_multiplier")
    base_tp = config.get("tp_atr_multiplier")
    base_dry = config.get("dry_run", True)
    base_tpen = config.get("traderspost_enabled", False)

    profiles = config.get("profiles") or []
    enabled = [p for p in profiles if isinstance(p, dict) and p.get("enabled")]

    base_dest = {
        "name": None,
        "webhook_url": base_webhook,
        "scale_entry": base_scale,
        "sl_atr_multiplier": base_sl,
        "tp_atr_multiplier": base_tp,
        "dry_run": base_dry,
        "traderspost_enabled": base_tpen,
    }

    dests: list[dict] = []
    # The base ALWAYS dispatches to its own webhook (the main account). It is
    # excluded only when it has no webhook AND at least one profile is enabled
    # (that is how you say "solo manden los perfiles"). With no profiles, the
    # base is the single destination → identical to the previous behaviour.
    if base_webhook or not enabled:
        dests.append(base_dest)

    for p in enabled:
        q = p.get("quantities")
        if q is None:
            q = base_scale.get("quantities")
        q = cap_quantities(list(q or []), p.get("max_contracts"))

        levels = p.get("levels")
        if levels is None:
            levels = base_scale.get("levels")

        scale = dict(base_scale)
        scale["quantities"] = q
        if levels is not None:
            scale["levels"] = levels
        # Inherit the base mode so escalonado stays execute/live unless base says off.

        def _ovr(key, base):
            v = p.get(key)
            return v if v not in (None, "") else base

        dests.append({
            "name": (p.get("name") or "perfil")[:30],
            "webhook_url": _ovr("webhook_url", base_webhook),
            "scale_entry": scale,
            "sl_atr_multiplier": _ovr("sl_atr_multiplier", base_sl),
            "tp_atr_multiplier": p["tp_atr_multiplier"] if "tp_atr_multiplier" in p else base_tp,
            "dry_run": p["dry_run"] if isinstance(p.get("dry_run"), bool) else base_dry,
            "traderspost_enabled": (
                p["traderspost_enabled"]
                if isinstance(p.get("traderspost_enabled"), bool) else base_tpen
            ),
        })

    # Dedupe by webhook_url (non-empty): a profile that reuses the base webhook
    # (e.g. left blank → inherited it) would double-send to the same account, so
    # keep only the first occurrence (the base wins).
    seen: set = set()
    out: list[dict] = []
    for d in dests:
        wh = d.get("webhook_url") or ""
        if wh and wh in seen:
            continue
        if wh:
            seen.add(wh)
        out.append(d)
    return out


def make_dest_config(base_config: dict, dest: dict) -> dict:
    """Project a destination back into a full config dict for PayloadBuilder/gate."""
    cfg = dict(base_config)
    cfg["scale_entry"] = dest["scale_entry"]
    cfg["sl_atr_multiplier"] = dest["sl_atr_multiplier"]
    cfg["tp_atr_multiplier"] = dest["tp_atr_multiplier"]
    cfg["traderspost_webhook_url"] = dest["webhook_url"]
    cfg["dry_run"] = dest["dry_run"]
    cfg["traderspost_enabled"] = dest["traderspost_enabled"]
    return cfg


def delivery_tag(name: str | None) -> str:
    """WebhookDelivery.destination tag — 'traderspost' or 'traderspost:<profile>'."""
    return "traderspost" if not name else f"traderspost:{name}"[:50]


def profile_from_tag(destination: str | None) -> str | None:
    """Inverse of delivery_tag — extract the profile name from a destination tag."""
    if not destination or ":" not in destination:
        return None
    return destination.split(":", 1)[1]
