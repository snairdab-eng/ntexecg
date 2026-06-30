# Reporte Estrategia 2 — ES 5m

## Ficha técnica (EXACTA)

- **Activo / TF:** ES (CME_MINI:ES1!) · 5m · máximo histórico · 1 contrato · sin comisiones ni slippage.
- **Indicador:** LuxAlgo® - Backtester (S&O) [3.3.3], modo señal **Scripted**.
- **Confirmation:** Normal.
- **Módulos / filtros activos:** Trend Catcher · Trend Strength (modo Ranging).
- **Lógica long:** entra largo con señal Scripted Long confirmada (Confirmation Normal), filtro Trend Catcher a favor y Trend Strength en régimen Ranging.
- **Lógica short:** entra corto con señal Scripted Short bajo las condiciones invertidas.
- **Salida:** Scripted Exit All / reversa de señal (gestionada por el backtester LuxAlgo).

## Métricas (1 contrato grande, sin comisiones ni slippage)

| Métrica | Valor |
|---|---|
| Nº operaciones | 123 |
| Profit Factor (PF) | 1.71 |
| Win Rate | 81.3% (100/123) |
| Net Profit | $32,537.50 |
| Ganancia promedio (trade ganador) | $785.88 |
| Pérdida promedio (trade perdedor) | $-2,002.17 |
| PEOR operación | $-10,162.50 |
| Max Drawdown ($) | $11,750.00 |
| Max Drawdown (%) | 31.26% |
| Gross Profit / Gross Loss | $78,587.50 / $46,050.00 |

## Contrato

| Contrato | Tick | $/punto |
|---|---|---|
| ES (grande, CME_MINI:ES1!) | 0.25 = $12.50 | $50 |
| MES (Micro E-mini S&P 500) | 0.25 = $1.25 | $5 |

_El P&L del CSV está en USD del contrato grande (1 contrato). MES = 1/10 del grande._

## ATR(14) real — 5m (desde barras HOLC locales, ET)

- ATR(14) típico (mediana) del periodo: **4.38 pts**
- ATR(14) medio del periodo: 5.11 pts
- En USD (contrato grande): mediana ≈ $219.20 | micro ≈ $21.92

## Periodo cubierto

- Desde: 2026-03-16 13:10 ET
- Hasta: 2026-06-29 15:29 ET
- Nº barras 5m en el periodo (con ATR válido): 19225

_ATR calculado con suavizado de Wilder (RMA) del True Range sobre barras HOLC 5m locales. Max DD$ = mayor caída pico-valle de la curva de P&L acumulada; Max DD% = Max DD$ / pico de equity acumulada (high-water mark) del periodo. Horas en ET (zona del chart America/New_York, idéntica a las barras HOLC)._

## Coincidencia de señales (mismo activo)

Criterio: una **señal coincide** si tiene el mismo `entry_time` (misma barra de 5m) y el mismo `side`. Comparación entre las estrategias del activo **ES**.

- Entradas de esta estrategia (S2): **123**
- Entradas **exclusivas** (no aparecen en ninguna otra estrategia de ES): **31** (25.2%)
- Señales **comunes a las 4 estrategias de ES**: **0**

| vs estrategia | señales coincidentes | % sobre esta estrategia |
|---|---|---|
| S5 · Strong·TrendStrengthRanging·WeakConfluence | 0 | 0.0% |
| S6 · Normal·TrendCatcher·HyperWave | 60 | 48.8% |
| S8 · Any·TrendCatcher·WeakConfluence | 56 | 45.5% |
