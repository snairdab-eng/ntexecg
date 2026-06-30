# Reporte Estrategia 1 — RTY 5m

## Ficha técnica (EXACTA)

- **Activo / TF:** RTY (CME_MINI:RTY1!) · 5m · máximo histórico · 1 contrato · sin comisiones ni slippage.
- **Indicador:** LuxAlgo® - Backtester (S&O) [3.3.3], modo señal **Scripted**.
- **Confirmation:** Strong.
- **Módulos / filtros activos:** Smart Trail · Trend Catcher.
- **Lógica long:** entra largo con señal Scripted Long confirmada (Confirmation Strong) y filtros Smart Trail / Trend Catcher a favor.
- **Lógica short:** entra corto con señal Scripted Short bajo las mismas condiciones invertidas.
- **Salida:** Scripted Exit All / reversa de señal (salida gestionada por el propio backtester LuxAlgo).

## Métricas (1 contrato grande, sin comisiones ni slippage)

| Métrica | Valor |
|---|---|
| Nº operaciones | 65 |
| Profit Factor (PF) | 1.97 |
| Win Rate | 83.1% (54/65) |
| Net Profit | $14,125.00 |
| Ganancia promedio (trade ganador) | $532.50 |
| Pérdida promedio (trade perdedor) | $-1,330.00 |
| PEOR operación | $-5,600.00 |
| Max Drawdown ($) | $7,970.00 |
| Max Drawdown (%) | 36.07% |
| Gross Profit / Gross Loss | $28,755.00 / $14,630.00 |

## Contrato

| | $/punto |
|---|---|
| RTY (grande) | $50 |
| M2K (Micro E-mini Russell 2000) (micro) | $5 |

## ATR(14) real — 5m (desde barras HOLC locales, ET)

- ATR(14) típico (mediana) del periodo: **2.47 pts**
- ATR(14) medio del periodo: 2.95 pts
- En USD (contrato grande): mediana ≈ $123.71 | micro ≈ $12.37

## Periodo cubierto

- Desde: 2026-03-17 14:30 ET
- Hasta: 2026-06-29 15:05 ET
- Nº barras 5m en el periodo (con ATR válido): 18926

_ATR calculado con suavizado de Wilder (RMA) del True Range sobre barras HOLC 5m locales. Max DD$ = mayor caída pico-valle de la curva de P&L acumulada; Max DD% = Max DD$ / pico de equity acumulada (high-water mark) del periodo. Horas en ET (zona del chart America/New_York, idéntica a las barras HOLC)._

## Coincidencia de señales (mismo activo)

El activo **RTY** no se repite en el alcance (única estrategia: S1). No aplica análisis de coincidencia de señales entre estrategias.

