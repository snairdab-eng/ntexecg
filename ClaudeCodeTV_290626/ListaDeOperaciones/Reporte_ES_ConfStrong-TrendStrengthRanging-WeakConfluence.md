# Reporte Estrategia 5 — ES 5m

## Ficha técnica (EXACTA)

- **Activo / TF:** ES (CME_MINI:ES1!) · 5m · máximo histórico · 1 contrato · sin comisiones ni slippage.
- **Indicador:** LuxAlgo® - Backtester (S&O) [3.3.3], modo señal **Scripted**.
- **Confirmation:** Strong.
- **Módulos / filtros activos:** Trend Strength (modo Ranging) · Weak Confluence.
- **Lógica long:** entra largo con señal Scripted Long confirmada (Confirmation Strong), Trend Strength en régimen Ranging y filtro Weak Confluence.
- **Lógica short:** entra corto con señal Scripted Short bajo las condiciones invertidas.
- **Salida:** Scripted Exit All / reversa de señal (gestionada por el backtester LuxAlgo).

## Métricas (1 contrato grande, sin comisiones ni slippage)

| Métrica | Valor |
|---|---|
| Nº operaciones | 98 |
| Profit Factor (PF) | 1.44 |
| Win Rate | 71.4% (70/98) |
| Net Profit | $19,250.00 |
| Ganancia promedio (trade ganador) | $900.89 |
| Pérdida promedio (trade perdedor) | $-1,564.73 |
| PEOR operación | $-6,112.50 |
| Max Drawdown ($) | $12,250.00 |
| Max Drawdown (%) | 45.86% |
| Gross Profit / Gross Loss | $63,062.50 / $43,812.50 |

## Contrato

| Contrato | Tick | $/punto |
|---|---|---|
| ES (grande, CME_MINI:ES1!) | 0.25 = $12.50 | $50 |
| MES (Micro E-mini S&P 500) | 0.25 = $1.25 | $5 |

_El P&L del CSV está en USD del contrato grande (1 contrato). MES = 1/10 del grande._

## ATR(14) real — 5m (desde barras HOLC locales, ET)

- ATR(14) típico (mediana) del periodo: **4.41 pts**
- ATR(14) medio del periodo: 5.12 pts
- En USD (contrato grande): mediana ≈ $220.35 | micro ≈ $22.03

## Periodo cubierto

- Desde: 2026-03-16 04:10 ET
- Hasta: 2026-06-29 14:00 ET
- Nº barras 5m en el periodo (con ATR válido): 19333

_ATR calculado con suavizado de Wilder (RMA) del True Range sobre barras HOLC 5m locales. Max DD$ = mayor caída pico-valle de la curva de P&L acumulada; Max DD% = Max DD$ / pico de equity acumulada (high-water mark) del periodo. Horas en ET (zona del chart America/New_York, idéntica a las barras HOLC)._

## Coincidencia de señales (mismo activo)

Criterio: una **señal coincide** si tiene el mismo `entry_time` (misma barra de 5m) y el mismo `side`. Comparación entre las estrategias del activo **ES**.

- Entradas de esta estrategia (S5): **98**
- Entradas **exclusivas** (no aparecen en ninguna otra estrategia de ES): **77** (78.6%)
- Señales **comunes a las 4 estrategias de ES**: **0**

| vs estrategia | señales coincidentes | % sobre esta estrategia |
|---|---|---|
| S2 · Normal·TrendCatcher·TrendStrengthRanging | 0 | 0.0% |
| S6 · Normal·TrendCatcher·HyperWave | 0 | 0.0% |
| S8 · Any·TrendCatcher·WeakConfluence | 21 | 21.4% |
