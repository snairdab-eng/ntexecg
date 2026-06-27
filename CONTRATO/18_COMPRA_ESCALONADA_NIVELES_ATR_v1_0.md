# Anexo 18 — Mejores niveles ATR de compra escalonada por instrumento · v1.0

**Fecha:** 2026-06-27
**Ámbito:** Paso previo al sizing — hallar los múltiplos de ATR donde conviene agregar Compra 2 y
Compra 3 **antes** del SL, por instrumento, con su ventana y SL candidato. 1 microcontrato por
nivel (sin optimizar cantidades). Salida principal = nativa LuxAlgo.
**Estado:** Simulación corrida. Pendiente: 2ª simulación de cantidades por nivel.
**Script:** `scripts/sim_scaled_entry.py` (ATR de `ohlcv_bars` con `--source db`).

## Modelo (Anexo 14)
Por trade, con ATR(14) en el TF propio y MAE medido: C1 en 0×ATR; C2 llena si MAE≥A×ATR; C3 si
MAE≥B×ATR. Si MAE≥SL×ATR → posición detenida (cada contrato sale en −(SL−nivel)×ATR). Si no →
salida nativa (cada contrato gana pnl_nativo + nivel×ATR por mejor entrada). Métricas en micro $.
`avgC` = contratos promedio activados; `Δf1`/`Δf2` = vs 1 y 2 contratos fijos en 0× con el mismo
SL; `Δnat` = vs 1 contrato nativo sin SL.

---

## Mejores niveles por activo (combo 0-A-B ganador)

| Instr | Ventana · SL | Mejor 0-A-B | Net micro | PF | Peor | avgC | Δnat | Δf2 |
|---|---|---|--:|--:|--:|--:|--:|--:|
| **ES→MES** | RTH · 2.5× | **0-0.75-1.25** | $3,280 | 1.94 | −$427 | 2.53 | +1,694 | +1,494 |
| **NQ→MNQ** | 24h · 8× | **0-3-4** | $5,665 | 1.47 | −$1,023 | 2.09 | +2,799 | +5,275 |
| **YM→MYM** | 24h · 8× | **0-1.5-2** | $4,334 | 1.70 | −$754 | 2.33 | +2,065 | +1,908 |
| **RTY→M2K** | AM · 4× | **0-0.5-1.5** | $2,308 | 10.56 | −$241 | 2.18 | +985 | −259 |
| **RTY→M2K** | RTH · 6× | **0-0.5-3** | $2,122 | 2.30 | −$565 | 2.14 | +414 | −400 |
| **6E→M6E** | RTH · 2× | **0-0.5-0.75** | $483 | 6.32 | −$36 | 2.40 | +361 | +60 |
| **6J→MJY** | 24h · 8× | (no escalar) | — | — | — | — | **−126** | — |
| **GC→MGC** | RTH · 2.5× | **0-0.5-0.75** | $15,901 | 4.61 | −$654 | 2.88 | +13,366 | +6,261 |
| **GC→MGC** | 24h · 8× | **0-0.5-1** | $25,989 | 1.72 | −$2,297 | 2.72 | +12,450 | +3,782 |
| **GC→MGC** | PM · 2.5× | **0-1.0-1.5** | $4,461 | 5.19 | −$300 | 2.64 | +2,972 | +1,788 |
| **CL→MCL** | 24h · 8× | **0-0.5-2.5** | $4,506 | 1.31 | −$2,124 | 2.25 | +2,461 | +1,630 |
| **CL→MCL** | 24h · nativo | 0-0.5-2.5 | $6,327 | 1.39 | −$6,130 | 2.25 | +4,282 | +2,237 |

---

## Hallazgos

1. **La compra escalonada mejora a casi todos** vs 1 contrato nativo (Δnat positivo en 7 de 8).
   La única excepción es **6J**, donde el nativo de 1 contrato ($383) supera a cualquier escalonado
   (mejor escalonado Δnat −$126) → **6J no se escala**.
2. **El primer add óptimo es TEMPRANO (~0.5×ATR) en la mayoría** (GC, 6E, RTY, CL): el precio
   suele dar un pullback corto antes de seguir a favor. **Excepciones por profundidad del MAE:**
   - **NQ:** adds profundos **3 y 4×ATR** (NQ corre mucho en contra antes de recuperar).
   - **YM:** adds medios **1.5 y 2×ATR**.
   - **ES:** adds **0.75 y 1.25×ATR**.
3. **GC es el gran beneficiado:** RTH 2.5× con 0-0.5-0.75 da PF 4.61 (+$13.4k vs nativo); 24h 8×
   con 0-0.5-1 casi duplica el net ($25,989). El add temprano + su carácter contrarian encajan.
4. **El escalonado no siempre supera a "2 fijos en 0×":** en **RTY** Δf2 es negativo (RTY rara vez
   va profundo en contra → los adds casi no llenan → 2 contratos en la señal rinden más en $). RTY
   gana en **PF/cola** con el escalonado, no en net. Evaluar en el sizing.
5. **CL:** escalonado + 8× ($4,506, peor −$2,124) es mejor riesgo/retorno que el nativo escalonado
   ($6,327 pero peor −$6,130, 3 contratos sin stop). El stop sigue importando en CL.

## Recomendación de niveles (para la 2ª fase de sizing)

| Instr | Niveles de add sugeridos | SL |
|---|---|---|
| ES | 0 · 0.75 · 1.25 | 2.5× |
| NQ | 0 · 3 · 4 | 8× |
| YM | 0 · 1.5 · 2 | 8× |
| RTY | 0 · 0.5 · 1.5 (AM) / 0.5 · 3 (RTH) | 4× AM / 6× RTH |
| 6E | 0 · 0.5 · 0.75 | 2× |
| 6J | sin escalonado (1 contrato nativo) | 8× emergencia |
| GC | 0 · 0.5 · 0.75 (RTH/PM) / 0.5 · 1 (24h) | 2.5× RTH/PM / 8× 24h |
| CL | 0 · 0.5 · 2.5 | 8× |

## Caveats
Muestras chicas en ventanas finas: RTY AM n=11, GC PM/RTH n=11/25, 6E AM/RTH n=9/20 → dirección
robusta, magnitudes aproximadas. Backtest sin comisiones/slippage (el escalonado opera ~2.0–2.9
contratos/trade → más comisión; el sizing real debe descontarla). `avgC`<3 = los adds llenan solo
cuando el precio retrocede al nivel. **Siguiente paso: optimizar cuántos microcontratos por nivel.**
