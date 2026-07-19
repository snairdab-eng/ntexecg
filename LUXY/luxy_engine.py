"""
luxy_engine.py — Motor de referencia de Luxy para integrar en NTEXECG.

Responsabilidades:
  1. build_luxy_params(...)  -> deriva TODOS los parámetros de riesgo de una lista
     de operaciones (export LuxAlgo) + OHLC. Reutiliza la lógica ya validada en
     build_dashboard_data.build() (que a su vez usa estudio_estrategia.py para
     cargar la lista, derivar el valor del punto y reconstruir el camino intrabar).
  2. scale_alloc / build_profile -> perfil principal (params calculados) + N
     perfiles secundarios que solo re-escalan el tamaño (micros) y aplican límites
     de riesgo por cuenta (propia vs fondeadora).
  3. worst_case_loss -> riesgo peor-caso por operación de un reparto de micros.
  4. traderspost_payloads -> genera el/los payload(s) de webhook para TradersPost
     por perfil y lado (largo/corto).

Todo es DENTRO DE MUESTRA con el 100% de los trades (sin OOS), en USD internamente.
Requiere: pandas, numpy, y en el mismo path estudio_estrategia.py + build_dashboard_data.py
(las piezas de cálculo ya validadas). Ver la receta .md para el detalle de cada fórmula.
"""
from __future__ import annotations
import json, datetime as dt
import build_dashboard_data as bdd   # build() = derivación completa de parámetros

MICROS_PER_MINI = 10                  # 1 mini = 10 micros -> PV_micro = PV / 10


# ----------------------------------------------------------------------------
# 1) DERIVACIÓN DE PARÁMETROS (una estrategia = una lista de operaciones)
# ----------------------------------------------------------------------------
def build_luxy_params(strategy_id: str, name: str,
                      trade_file: str, ohlc_5m: str, ohlc_1h: str) -> dict:
    """Devuelve el objeto LuxyParams para una estrategia.
    Reutiliza bdd.build(), que deriva: pv, sl_usd, tp_usd, be_usd, niveles del
    escalonado (l2/l3) y su reparto de micros por frecuencia de pullback, dirección,
    sesiones/días (diagnóstico), régimen, time-stop (veredicto), métricas crudo vs
    config y la cascada del integrador. NO se aplica time-stop ni auto-bloqueo de
    sesiones (son diagnóstico)."""
    d = bdd.build(strategy_id, name, trade_file, ohlc_5m, ohlc_1h)
    d["strategy_id"] = strategy_id
    d["computed_at"] = dt.datetime.utcnow().isoformat() + "Z"
    return d


# ----------------------------------------------------------------------------
# 2) PERFILES: principal (calculado) + secundarios (re-escala tamaño por cuenta)
# ----------------------------------------------------------------------------
def scale_alloc(alloc: list[int], size_scale: float, max_contracts: int | None = None) -> list[int]:
    """Re-escala el reparto de micros preservando proporción (mayor residuo)."""
    total = sum(alloc)
    if total == 0:
        return [0, 0, 0]
    target = max(1, round(total * size_scale))
    if max_contracts:
        target = min(target, max_contracts)
    raw = [a / total * target for a in alloc]
    out = [int(x) for x in raw]
    order = sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)
    for i in range(target - sum(out)):
        out[order[i]] += 1
    if out[0] == 0 and target > 0:                 # C1 (entrada) nunca en 0 si hay tamaño
        out[0] = 1
        out[max(range(1, len(out)), key=lambda i: out[i])] -= 1
    return out


def worst_case_loss(params: dict, alloc: list[int]) -> float:
    """Pérdida peor-caso por operación en USD: todos los niveles se llenan y para en SL.
    loss = Σ q_i · (SL_pts − L_i_pts) · PV_micro   (el add en −L_i pierde menos hasta el SL)."""
    pv = params["pv"]; pv_m = pv / MICROS_PER_MINI
    r = params["reco"]
    sl = r["sl_usd"] / pv
    lv = [0.0, r["l2_usd"] / pv, r["l3_usd"] / pv]
    return round(sum(q * (sl - L) * pv_m for q, L in zip(alloc, lv)), 2)


def build_profile(params: dict, *, name: str, account_type: str, size_scale: float,
                  max_contracts: int | None = None, max_loss_per_trade: float | None = None,
                  max_daily_loss: float | None = None, max_trades_day: int | None = None,
                  webhook_url: str = "", blocked_sessions: list[str] | None = None,
                  blocked_days: list[int] | None = None, is_main: bool = False) -> dict:
    """Construye un perfil. El principal usa el reparto calculado tal cual; los
    secundarios re-escalan el tamaño y respetan los límites de la cuenta.
    Si max_loss_per_trade se define y el peor caso lo supera, baja el tamaño hasta cumplir."""
    base_alloc = params["reco"]["alloc"]
    alloc = base_alloc[:] if is_main else scale_alloc(base_alloc, size_scale, max_contracts)
    # respeta el presupuesto de riesgo por operación de la cuenta (clave en fondeadoras)
    if max_loss_per_trade:
        while sum(alloc) > 1 and worst_case_loss(params, alloc) > max_loss_per_trade:
            alloc = scale_alloc(alloc, (sum(alloc) - 1) / sum(alloc), max_contracts)
    return {
        "name": name, "account_type": account_type, "is_main": is_main,
        "size_scale": 1.0 if is_main else size_scale,
        "alloc": alloc, "total_micros": sum(alloc),
        "worst_case_loss": worst_case_loss(params, alloc),
        "risk_caps": {"max_contracts": max_contracts, "max_loss_per_trade": max_loss_per_trade,
                      "max_daily_loss": max_daily_loss, "max_trades_day": max_trades_day},
        "webhook_url": webhook_url,
        "blocked_sessions": blocked_sessions or [], "blocked_days": blocked_days or [],
        "enabled": True,
    }


# ----------------------------------------------------------------------------
# 3) DESPACHO A TRADERSPOST (payload por perfil y lado)
# ----------------------------------------------------------------------------
def traderspost_payloads(params: dict, profile: dict, *, side: str, ticker: str,
                         signal_price: float | None = None, scale_in: bool = False,
                         use_risk_sizing: bool = False) -> list[dict]:
    """Genera los payloads de webhook de TradersPost para una señal.
    side: 'long' | 'short'.  ticker: símbolo TradersPost (p.ej. 'MES' o 'MESZ2025').
    scale_in=False (Opción A): una sola entrada al tamaño total con bracket SL/TP.
    scale_in=True  (Opción B): C1 market + C2/C3 como órdenes 'add' limit a los offsets.
    use_risk_sizing=True: en vez de fixed_quantity usa quantityType risk_dollar_amount
        con el presupuesto de la cuenta (TradersPost dimensiona el tamaño por riesgo)."""
    pv = params["pv"]; r = params["reco"]
    action = "buy" if side == "long" else "sell"
    sentiment = "bullish" if side == "long" else "bearish"
    sgn = 1 if side == "long" else -1
    tp_pts = round(r["tp_usd"] / pv, 4)          # offset de precio (puntos) para futuros
    sl_pts = round(r["sl_usd"] / pv, 4)
    c2_pts = round(r["l2_usd"] / pv, 4)
    c3_pts = round(r["l3_usd"] / pv, 4)
    alloc = profile["alloc"]
    now = dt.datetime.utcnow().isoformat() + "Z"
    extras = {"strategy": params["strategy_id"], "profile": profile["name"], "luxy": True}

    def sizing(qty_micros):
        if use_risk_sizing and profile["risk_caps"].get("max_loss_per_trade"):
            # TradersPost calcula el tamaño por riesgo (requiere stopLoss)
            return {"quantity": profile["risk_caps"]["max_loss_per_trade"],
                    "quantityType": "risk_dollar_amount"}
        return {"quantity": qty_micros, "quantityType": "fixed_quantity"}

    entry = {"ticker": ticker, "action": action, "sentiment": sentiment,
             "orderType": "market", **sizing(sum(alloc) if not scale_in else alloc[0]),
             "takeProfit": {"amount": tp_pts},
             "stopLoss": {"type": "stop", "amount": sl_pts},
             "time": now, "extras": extras}
    if signal_price is not None:
        entry["signalPrice"] = signal_price
    payloads = [entry]

    if scale_in and (alloc[1] or alloc[2]) and signal_price is not None:
        for lvl_pts, q in ((c2_pts, alloc[1]), (c3_pts, alloc[2])):
            if q <= 0:
                continue
            payloads.append({
                "ticker": ticker, "action": "add", "sentiment": sentiment,
                "orderType": "limit",
                "limitPrice": round(signal_price - sgn * lvl_pts, 4),  # add en contra de la posición
                "quantity": q, "quantityType": "fixed_quantity",
                "time": now, "extras": {**extras, "leg": f"add@-{lvl_pts}"},
            })
    return payloads


def exit_payload(params: dict, profile: dict, ticker: str) -> dict:
    """Salida real: la señal de LuxAlgo (Scripted Exit All) cierra toda la posición."""
    return {"ticker": ticker, "action": "exit", "sentiment": "flat",
            "time": dt.datetime.utcnow().isoformat() + "Z",
            "extras": {"strategy": params["strategy_id"], "profile": profile["name"], "luxy": True}}


# ----------------------------------------------------------------------------
# Ejemplo de uso (referencia)
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    p = build_luxy_params(
        "ES5m_ConfNormal_TC_TSR", "MicroES5m - Confirmation Normal - TC - TSR",
        "trades/1783518563576_ES5m_ConfNormal_TC_TSR_070726.csv",
        "ohlc/ES_5m.csv", "ohlc/ES_1h.csv")

    profiles = [
        build_profile(p, name="Principal (cuenta propia)", account_type="propia",
                      size_scale=1.0, is_main=True, webhook_url="https://webhooks.traderspost.io/.../own"),
        build_profile(p, name="Fondeadora A (50k)", account_type="fondeadora",
                      size_scale=0.6, max_contracts=6, max_loss_per_trade=500,
                      max_daily_loss=1000, webhook_url="https://webhooks.traderspost.io/.../fa"),
        build_profile(p, name="Fondeadora B (25k)", account_type="fondeadora",
                      size_scale=0.4, max_contracts=4, max_loss_per_trade=300,
                      max_daily_loss=600, webhook_url="https://webhooks.traderspost.io/.../fb"),
        build_profile(p, name="Fondeadora C (agresiva)", account_type="fondeadora",
                      size_scale=0.3, max_contracts=3, max_loss_per_trade=250, max_daily_loss=500),
        build_profile(p, name="Shadow / test", account_type="paper", size_scale=1.0),
    ]

    print("Estrategia:", p["strategy_id"], "| PV", p["pv"], "| reparto principal",
          p["reco"]["alloc"], "| SL", p["reco"]["sl_usd"], "TP", p["reco"]["tp_usd"])
    for pr in profiles:
        print(f"  {pr['name']:28s} micros {pr['alloc']} (Σ{pr['total_micros']})"
              f"  peor-caso ${pr['worst_case_loss']:.0f}")
    print("\nPayload TradersPost (perfil principal, largo, escalonado real):")
    print(json.dumps(traderspost_payloads(p, profiles[0], side="long", ticker="MES",
                                          signal_price=5000.0, scale_in=True), indent=2, ensure_ascii=False))
