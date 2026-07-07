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


# Las llaves de bracket que cada destino hereda/overridea (R-obs-2c): la
# MISMA precedencia del L5 — backstop_points (pts fijos) REEMPLAZA al SL
# ×ATR; tp_nominal del lado PREVALECE sobre el TP ×ATR único.
_BRACKET_KEYS = ("sl_atr_multiplier", "tp_atr_multiplier", "backstop_points",
                 "tp_nominal_long", "tp_nominal_short")


def recompute_bracket(entry_price: float | None, atr: float | None,
                      is_long: bool, dest: dict) -> tuple[float | None,
                                                          float | None]:
    """Bracket del DESTINO con la precedencia del L5 (R-obs-2c: ×ATR o
    puntos fijos, también por perfil). Espeja sl_tp_calculator:
      SL: backstop_points (fijo desde la señal, no necesita ATR) >
          sl_atr_multiplier × ATR.
      TP: tp_nominal_<lado> × ATR > tp_atr_multiplier × ATR >
          (sin ATR con backstop) ancho del backstop espejado > None.
    Devuelve (None, None) si el SL no es computable — el caller debe caer
    FAIL-CLOSED al bracket base del L5, nunca enviar sin stop."""
    if entry_price is None or entry_price <= 0:
        return None, None
    bk = dest.get("backstop_points")
    bk = float(bk) if isinstance(bk, (int, float)) and bk > 0 else None
    hay_atr = atr is not None and atr > 0
    if bk is not None:
        sl = entry_price - bk if is_long else entry_price + bk
    elif dest.get("sl_atr_multiplier") and hay_atr:
        k = float(dest["sl_atr_multiplier"])
        sl = entry_price - atr * k if is_long else entry_price + atr * k
    else:
        return None, None                      # SL no computable → base
    nominal = dest.get("tp_nominal_long" if is_long else "tp_nominal_short")
    tp = None
    if isinstance(nominal, (int, float)) and nominal > 0 and hay_atr:
        tp = (entry_price + atr * nominal if is_long
              else entry_price - atr * nominal)
    elif isinstance(nominal, (int, float)) and nominal > 0 and bk is not None:
        # fail-closed sin ATR: ancho del backstop espejado (como el L5)
        tp = entry_price + bk if is_long else entry_price - bk
    elif dest.get("tp_atr_multiplier") and hay_atr:
        k = float(dest["tp_atr_multiplier"])
        tp = entry_price + atr * k if is_long else entry_price - atr * k
    # Guarda P0 espejada: precios del lado correcto y > 0, o nada.
    if sl <= 0 or (sl >= entry_price if is_long else sl <= entry_price):
        return None, None
    if tp is not None and (tp <= 0 or (tp <= entry_price if is_long
                                       else tp >= entry_price)):
        tp = None
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
    # R-obs-2c — el bracket de la base viaja COMPLETO a cada destino: los
    # perfiles HEREDAN el SL/TP del perfil principal tal cual, sea ×ATR o
    # stop de puntos fijos (backstop) con TP nominal por lado.
    base_bk = config.get("backstop_points")
    base_tpl = config.get("tp_nominal_long")
    base_tps = config.get("tp_nominal_short")

    profiles = config.get("profiles") or []
    enabled = [p for p in profiles if isinstance(p, dict) and p.get("enabled")]

    base_dest = {
        "name": None,
        "webhook_url": base_webhook,
        "scale_entry": base_scale,
        "sl_atr_multiplier": base_sl,
        "tp_atr_multiplier": base_tp,
        "backstop_points": base_bk,
        "tp_nominal_long": base_tpl,
        "tp_nominal_short": base_tps,
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

        # R-obs-2c — herencia del bracket: los perfiles heredan el SL/TP de
        # la base TAL CUAL (stop fijo + TP nominal incluidos). El override
        # Avanzado ×ATR de un perfil es explícito: si el operador lo pone,
        # ese destino usa ×ATR (el stop fijo/nominal heredado se apaga para
        # que el override no quede mudo bajo la precedencia del L5).
        p_sl = p.get("sl_atr_multiplier")
        # TP explícito en el perfil (aunque sea None = "sin TP") reemplaza a
        # la base COMPLETA: también apaga el TP nominal heredado — si no, el
        # nominal ganaría por precedencia y el override quedaría mudo.
        tp_explicito = "tp_atr_multiplier" in p
        dests.append({
            "name": (p.get("name") or "perfil")[:30],
            "webhook_url": _ovr("webhook_url", base_webhook),
            "scale_entry": scale,
            "sl_atr_multiplier": _ovr("sl_atr_multiplier", base_sl),
            "tp_atr_multiplier": p["tp_atr_multiplier"] if tp_explicito else base_tp,
            "backstop_points": None if p_sl not in (None, "") else base_bk,
            "tp_nominal_long": None if tp_explicito else base_tpl,
            "tp_nominal_short": None if tp_explicito else base_tps,
            # Kill-switch por capas (NX-02): un perfil solo puede RESTRINGIR el
            # envío, nunca abrirlo por encima de la base (que ya fusiona global
            # OR/AND estrategia). dry_run hereda con OR; traderspost_enabled con
            # AND (sin especificar → hereda; especificado → solo endurece).
            "dry_run": bool(base_dry) or bool(p.get("dry_run")),
            "traderspost_enabled": bool(base_tpen) and (
                bool(p["traderspost_enabled"])
                if isinstance(p.get("traderspost_enabled"), bool) else True
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
    # R-obs-2c — el bracket heredado/overrideado viaja completo al config
    # proyectado (payload extras y gate ven la misma precedencia del L5).
    cfg["backstop_points"] = dest.get("backstop_points")
    cfg["tp_nominal_long"] = dest.get("tp_nominal_long")
    cfg["tp_nominal_short"] = dest.get("tp_nominal_short")
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
