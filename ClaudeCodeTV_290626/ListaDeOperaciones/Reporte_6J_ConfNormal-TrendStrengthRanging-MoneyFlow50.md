# Reporte Estrategia 4 — 6J 5m

## Ficha técnica (EXACTA)

- **Activo / TF:** 6J (CME:6J1!) · 5m · máximo histórico · 1 contrato · sin comisiones ni slippage.
- **Indicador:** LuxAlgo® - Backtester (S&O) [3.3.3], modo señal **Scripted**.
- **Confirmation:** Normal.
- **Módulos / filtros activos:** Trend Strength (modo Ranging) · Money Flow (filtro Below/Above 50).
- **Lógica long:** entra largo con señal Scripted Long confirmada (Confirmation Normal), con Trend Strength en régimen Ranging y Money Flow por encima de 50.
- **Lógica short:** entra corto con señal Scripted Short, Trend Strength Ranging y Money Flow por debajo de 50.
- **Salida:** Scripted Exit All / reversa de señal (gestionada por el backtester LuxAlgo).

## Métricas (1 contrato grande, sin comisiones ni slippage)

| Métrica | Valor |
|---|---|
| Nº operaciones | 49 |
| Profit Factor (PF) | 0.94 |
| Win Rate | 79.6% (39/49) |
| Net Profit | $-212.50 |
| Ganancia promedio (trade ganador) | $81.25 |
| Pérdida promedio (trade perdedor) | $-338.12 |
| PEOR operación | $-2,668.75 |
| Max Drawdown ($) | $2,668.75 |
| Max Drawdown (%) | 108.65% |
| Gross Profit / Gross Loss | $3,168.75 / $3,381.25 |

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
- En USD (contrato grande): mediana ≈ $18.92 | micro ≈ $1.89

## Periodo cubierto

- Desde: 2026-03-16 12:55 ET
- Hasta: 2026-06-29 15:24 ET
- Nº barras 5m en el periodo (con ATR válido): 19238

_ATR calculado con suavizado de Wilder (RMA) del True Range sobre barras HOLC 5m locales. Max DD$ = mayor caída pico-valle de la curva de P&L acumulada; Max DD% = Max DD$ / pico de equity acumulada (high-water mark) del periodo. Horas en ET (zona del chart America/New_York, idéntica a las barras HOLC)._

## Coincidencia de señales (mismo activo)

Criterio: una **señal coincide** si tiene el mismo `entry_time` (misma barra de 5m) y el mismo `side`. Comparación entre las estrategias del activo **6J**.

- Entradas de esta estrategia (S4): **49**
- Entradas **exclusivas** (no aparecen en ninguna otra estrategia de 6J): **38** (77.6%)
- Señales **comunes a las 3 estrategias de 6J**: **0**

| vs estrategia | señales coincidentes | % sobre esta estrategia |
|---|---|---|
| S3 · Normal·ContrarianUptrend·TrendTracer | 11 | 22.4% |
| S9 · Normal·MoneyFlow | 0 | 0.0% |
