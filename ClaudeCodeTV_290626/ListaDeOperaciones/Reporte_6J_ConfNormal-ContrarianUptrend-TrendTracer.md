# Reporte Estrategia 3 — 6J 5m

## Ficha técnica (EXACTA)

- **Activo / TF:** 6J (CME:6J1!) · 5m · máximo histórico · 1 contrato · sin comisiones ni slippage.
- **Indicador:** LuxAlgo® - Backtester (S&O) [3.3.3], modo señal **Scripted**.
- **Confirmation:** Normal.
- **Módulos / filtros activos:** Contrarian Uptrend · Trend Tracer.
- **Lógica long:** entra largo con señal Scripted Long confirmada (Confirmation Normal), módulo Contrarian Uptrend y filtro Trend Tracer a favor.
- **Lógica short:** entra corto con señal Scripted Short bajo las condiciones invertidas.
- **Salida:** Scripted Exit All / reversa de señal (gestionada por el backtester LuxAlgo).

## Métricas (1 contrato grande, sin comisiones ni slippage)

| Métrica | Valor |
|---|---|
| Nº operaciones | 68 |
| Profit Factor (PF) | 4.69 |
| Win Rate | 86.8% (59/68) |
| Net Profit | $2,906.25 |
| Ganancia promedio (trade ganador) | $62.61 |
| Pérdida promedio (trade perdedor) | $-87.50 |
| PEOR operación | $-262.50 |
| Max Drawdown ($) | $262.50 |
| Max Drawdown (%) | 9.03% |
| Gross Profit / Gross Loss | $3,693.75 / $787.50 |

## Contrato

El 6J cotiza en USD por JPY (precios ~0.0063). Multiplicador = 12.500.000 JPY por contrato grande.

| Contrato | Tick | Valor por 0.000001 (1 pip al 6º decimal) | Valor por 1.00 de precio |
|---|---|---|---|
| 6J (grande, CME:6J1!) | 0.0000005 = $6.25 | $12.50 | $12,500,000 |
| M6J (Micro JPY/USD) | 0.000001 = $1.25 | $1.25 | $1,250,000 |

_Nota: el P&L del CSV está en USD reales del contrato grande (1 contrato). El micro M6J = 1/10 del grande._

## ATR(14) real — 5m (desde barras HOLC locales, ET)

- ATR(14) típico (mediana) del periodo: **0.0000015 pts**
- ATR(14) medio del periodo: 0.0000018 pts
- En USD (contrato grande): mediana ≈ $19.01 | micro ≈ $1.90

## Periodo cubierto

- Desde: 2026-03-16 03:30 ET
- Hasta: 2026-06-26 10:45 ET
- Nº barras 5m en el periodo (con ATR válido): 19351

_ATR calculado con suavizado de Wilder (RMA) del True Range sobre barras HOLC 5m locales. Max DD$ = mayor caída pico-valle de la curva de P&L acumulada; Max DD% = Max DD$ / pico de equity acumulada (high-water mark) del periodo. Horas en ET (zona del chart America/New_York, idéntica a las barras HOLC)._

## Coincidencia de señales (mismo activo)

Criterio: una **señal coincide** si tiene el mismo `entry_time` (misma barra de 5m) y el mismo `side`. Comparación entre las estrategias del activo **6J**.

- Entradas de esta estrategia (S3): **68**
- Entradas **exclusivas** (no aparecen en ninguna otra estrategia de 6J): **22** (32.4%)
- Señales **comunes a las 3 estrategias de 6J**: **0**

| vs estrategia | señales coincidentes | % sobre esta estrategia |
|---|---|---|
| S4 · Normal·TrendStrengthRanging·MoneyFlow50 | 11 | 16.2% |
| S9 · Normal·MoneyFlow | 35 | 51.5% |
