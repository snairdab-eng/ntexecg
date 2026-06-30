# Reporte Estrategia 7 — NQ 5m

## Ficha técnica (EXACTA)

- **Activo / TF:** NQ (CME_MINI:NQ1!) · 5m · máximo histórico · 1 contrato · sin comisiones ni slippage.
- **Indicador:** LuxAlgo® - Backtester (S&O) [3.3.3], modo señal **Scripted**.
- **Confirmation:** Any (sin filtro de confirmación; toma todas las señales).
- **Módulos / filtros activos:** Trend Catcher · HyperWave.
- **Lógica long:** entra largo con señal Scripted Long (Confirmation Any), filtro Trend Catcher a favor y HyperWave.
- **Lógica short:** entra corto con señal Scripted Short bajo las condiciones invertidas.
- **Salida:** Scripted Exit All / reversa de señal (gestionada por el backtester LuxAlgo).

## Métricas (1 contrato grande, sin comisiones ni slippage)

| Métrica | Valor |
|---|---|
| Nº operaciones | 161 |
| Profit Factor (PF) | 1.43 |
| Win Rate | 74.5% (120/161) |
| Net Profit | $64,820.00 |
| Ganancia promedio (trade ganador) | $1,783.42 |
| Pérdida promedio (trade perdedor) | $-3,638.78 |
| PEOR operación | $-17,700.00 |
| Max Drawdown ($) | $34,980.00 |
| Max Drawdown (%) | 51.74% |
| Gross Profit / Gross Loss | $214,010.00 / $149,190.00 |

## Contrato

| Contrato | Tick | $/punto |
|---|---|---|
| NQ (grande, CME_MINI:NQ1!) | 0.25 = $5.00 | $20 |
| MNQ (Micro E-mini Nasdaq-100) | 0.25 = $0.50 | $2 |

_El P&L del CSV está en USD del contrato grande (1 contrato). MNQ = 1/10 del grande._

## ATR(14) real — 5m (desde barras HOLC locales, ET)

- ATR(14) típico (mediana) del periodo: **23.76 pts**
- ATR(14) medio del periodo: 27.74 pts
- En USD (contrato grande): mediana ≈ $475.10 | micro ≈ $47.51

## Periodo cubierto

- Desde: 2026-03-15 22:30 ET
- Hasta: 2026-06-29 12:25 ET
- Nº barras 5m en el periodo (con ATR válido): 19394

_ATR calculado con suavizado de Wilder (RMA) del True Range sobre barras HOLC 5m locales. Max DD$ = mayor caída pico-valle de la curva de P&L acumulada; Max DD% = Max DD$ / pico de equity acumulada (high-water mark) del periodo. Horas en ET (zona del chart America/New_York, idéntica a las barras HOLC)._

## Coincidencia de señales (mismo activo)

El activo **NQ** no se repite en el alcance (única estrategia: S7). No aplica análisis de coincidencia de señales entre estrategias.

