# Reporte Estrategia 8 — ES 5m

## Ficha técnica (EXACTA)

- **Activo / TF:** ES (CME_MINI:ES1!) · 5m · máximo histórico · 1 contrato · sin comisiones ni slippage.
- **Indicador:** LuxAlgo® - Backtester (S&O) [3.3.3], modo señal **Scripted**.
- **Confirmation:** Any (sin filtro de confirmación; toma todas las señales).
- **Módulos / filtros activos:** Trend Catcher · Weak Confluence.
- **Lógica long:** entra largo con señal Scripted Long (Confirmation Any), filtro Trend Catcher a favor y Weak Confluence.
- **Lógica short:** entra corto con señal Scripted Short bajo las condiciones invertidas.
- **Salida:** Scripted Exit All / reversa de señal (gestionada por el backtester LuxAlgo).

## Métricas (1 contrato grande, sin comisiones ni slippage)

| Métrica | Valor |
|---|---|
| Nº operaciones | 136 |
| Profit Factor (PF) | 1.73 |
| Win Rate | 80.1% (109/136) |
| Net Profit | $37,187.50 |
| Ganancia promedio (trade ganador) | $805.85 |
| Pérdida promedio (trade perdedor) | $-1,875.93 |
| PEOR operación | $-7,425.00 |
| Max Drawdown ($) | $9,837.50 |
| Max Drawdown (%) | 23.39% |
| Gross Profit / Gross Loss | $87,837.50 / $50,650.00 |

## Contrato

| Contrato | Tick | $/punto |
|---|---|---|
| ES (grande, CME_MINI:ES1!) | 0.25 = $12.50 | $50 |
| MES (Micro E-mini S&P 500) | 0.25 = $1.25 | $5 |

_El P&L del CSV está en USD del contrato grande (1 contrato). MES = 1/10 del grande._

## ATR(14) real — 5m (desde barras HOLC locales, ET)

- ATR(14) típico (mediana) del periodo: **4.40 pts**
- ATR(14) medio del periodo: 5.11 pts
- En USD (contrato grande): mediana ≈ $220.10 | micro ≈ $22.01

## Periodo cubierto

- Desde: 2026-03-16 02:30 ET
- Hasta: 2026-06-29 15:59 ET
- Nº barras 5m en el periodo (con ATR válido): 19353

_ATR calculado con suavizado de Wilder (RMA) del True Range sobre barras HOLC 5m locales. Max DD$ = mayor caída pico-valle de la curva de P&L acumulada; Max DD% = Max DD$ / pico de equity acumulada (high-water mark) del periodo. Horas en ET (zona del chart America/New_York, idéntica a las barras HOLC)._

## Coincidencia de señales (mismo activo)

Criterio: una **señal coincide** si tiene el mismo `entry_time` (misma barra de 5m) y el mismo `side`. Comparación entre las estrategias del activo **ES**.

- Entradas de esta estrategia (S8): **136**
- Entradas **exclusivas** (no aparecen en ninguna otra estrategia de ES): **52** (38.2%)
- Señales **comunes a las 4 estrategias de ES**: **0**

| vs estrategia | señales coincidentes | % sobre esta estrategia |
|---|---|---|
| S2 · Normal·TrendCatcher·TrendStrengthRanging | 56 | 41.2% |
| S5 · Strong·TrendStrengthRanging·WeakConfluence | 21 | 15.4% |
| S6 · Normal·TrendCatcher·HyperWave | 31 | 22.8% |
