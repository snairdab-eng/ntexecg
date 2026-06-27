# Anexo 11 — Calibración del Stop Loss por ATR (estrategia ES) · v1.0

**Fecha:** 2026-06-25
**Ámbito:** Estrategia `ES5m` (LuxAlgo "Confirmation Normal / Trend Catcher / Trend Strength Ranging"), instrumento MES.
**Estado:** Aplicado en producción (`StrategyProfile.sl_atr_multiplier` 1.5 → 2.5).

## 1. Motivo

NTEXECG impone un Stop Loss obligatorio de `sl_atr_multiplier × ATR` a toda entrada
(regla de contrato: el SL nunca es nulo). El valor por defecto era **1.5×ATR**. Surgió
la duda de si ese stop es compatible con el comportamiento real de la estrategia de ES,
y si la ventana RTH (09:20–15:45 NY) captura su edge.

## 2. Fuente de datos

- Backtester interno de LuxAlgo "Backtester (S&O) [3.3.3]" sobre `CME_MINI:ES1!` (E-mini;
  mismo subyacente y mismo ATR en puntos que MES, sólo cambia el $/punto ×10).
- Exportación CSV de la **Lista de operaciones**: 119 trades cerrados, 15-mar a 25-jun 2026,
  con **MAE (Desviación adversa) y MFE (Desviación favorable) medidos por trade**.
- Análisis reproducible vía script sobre el CSV (no estimación).

## 3. Metodología

- **Filtro de sesión:** trades cuya hora de entrada (NY) cae en 09:20–15:45.
- **Barrido de SL:** para k ∈ {1.5, 2.0, 2.5, 3.0, 4.0}, regla conservadora: si el MAE del
  trade supera k×ATR, el trade se detiene en −k×ATR; si no, conserva su resultado real
  (ganancias capadas a +6×ATR si el MFE lo permite). TP fijo = 6×ATR.
- **ATR proxy = 7.0 pts** (valor actual; los múltiplos ×ATR son aproximados, ver caveats).
- Supuestos conservadores: el stop se toca antes que el TP si ambos niveles se alcanzan;
  fills exactos sin slippage.

## 4. Resultados

### 4.1 Backtest 24h vs solo-sesión (RTH)

| Métrica | TOTAL 24h | RTH (09:20–15:45) |
|---|---|---|
| Nº trades | 119 | 43 (36%) |
| Win rate | 82.4% | 86.0% |
| Profit Factor | 1.81 | 2.03 |
| Expectancy | $289 (5.8 pt) | $391 (7.8 pt) |
| Avg winner | $784 | $893 |
| Avg loser | −$2,018 | −$2,706 |
| Net PnL | $34,450 | $16,800 |
| Max DD (equity de cierre) | $11,750 | $10,162 |

### 4.2 Barrido de SL en RTH (TP = 6×ATR; ATR proxy 7 pt)

| k×ATR | SL $ | WR % | PF | exp $ | net $ | maxDD $ | W cortados | L capados |
|---|---|---|---|---|---|---|---|---|
| nativo (sin SL) | — | 86.0 | 2.03 | 391 | 16,800 | 10,162 | — | — |
| 1.5 | 525 | 37.2 | 1.08 | 27 | 1,175 | 3,150 | 21 | 6 |
| 2.0 | 700 | 48.8 | 1.31 | 111 | 4,775 | 3,312 | 16 | 6 |
| **2.5** | **875** | **55.8** | **1.31** | **119** | **5,138** | **3,138** | **13** | **6** |
| 3.0 | 1,050 | 58.1 | 1.15 | 68 | 2,925 | 4,700 | 12 | 6 |
| 4.0 | 1,400 | 62.8 | 1.07 | 36 | 1,562 | 6,475 | 10 | 5 |

## 5. Hallazgos

1. **La ventana RTH mejora la calidad por trade** (PF 1.81 → 2.03, expectancy +35%). Se confirma operar sólo RTH.
2. **1.5×ATR es el peor punto medido** (RTH: expectancy $27, PF 1.08). Corta 21 de ~37 ganadores. Inviable.
3. **El stop fijo óptimo es ~2.5×ATR** (2.0 casi idéntico): mayor expectancy ($119) y PF (1.31) entre stops fijos, y el menor drawdown ($3,138, un tercio del nativo).
4. **Ningún stop fijo le gana al exit nativo por señal** (PF 2.03). El SL obligatorio, aun en su óptimo, **cuesta ~35% del edge** (PF 1.31 vs 2.03). Causa estructural: las excursiones adversas de ganadores y perdedores se solapan; cualquier stop que tape los losers de −40 pts también corta ganadores.
5. **El TP de 6×ATR es casi irrelevante** (sólo 2–4 trades lo alcanzan). Se mantiene únicamente porque TradersPost exige bracket (TP+SL).

## 6. Decisión

- `ES5m.sl_atr_multiplier`: **1.5 → 2.5** (aplicado 2026-06-25).
- Ventana operativa: **se mantiene RTH** 09:20–15:45 NY (validada por datos).
- `tp_atr_multiplier`: **se mantiene en 6.0** (respaldo del bracket).

El SL obligatorio se asume como **elección de control de riesgo**: a cambio de ~35% del edge,
acota la pérdida por trade y reduce el drawdown a un tercio ($3,138 vs $10,162). Trade defendible
en fase paper / arranque, donde proteger capital pesa más que exprimir el máximo edge.

## 7. Implicaciones de diseño (pendientes)

- **Calibración por estrategia:** este resultado es sólo de ES. MYM/MNQ/M6E tienen perfiles de MAE distintos y requieren el mismo análisis antes de afinar su SL.
- **Modo futuro a evaluar:** "stop catastrófico ancho + salida por señal nativa" para estrategias cuyo edge muere con stop fijo, sin violar la red de seguridad de riesgo.

## 8. Caveats

- ATR proxy fijo = 7 pt; con ATR(14) real por barra los umbrales se desplazan. El **ranking** (1.5 pésimo · ~2.5 óptimo · nativo mejor) es robusto; los valores absolutos son aproximados.
- Muestra pequeña en RTH (43 trades, ~6 losers) → PF algo ruidoso.
- Backtest ≠ futuro. Perfil frágil: win rate alto con pocas pérdidas grandes; DD histórico elevado.
- Sin slippage modelado.
