# Coincidencia de señales y correlación de P&L — 9 estrategias

Periodo: 2026-03-16 → 2026-06-29 (88 días con operaciones). Todo a 1 contrato grande, sin comisiones/slippage.

## 1. Archivos generados

| Archivo | Contenido |
|---|---|
| `senales_compartidas_ES.csv` | Cada `entry_time`+`side` presente en ≥2 estrategias de ES, con banderas S2/S5/S6/S8 y `n_estrategias`. |
| `senales_compartidas_6J.csv` | Ídem para 6J (banderas S3/S4/S9). |
| `correlacion_pnl.csv` | Matriz 9×9 de correlación de Pearson del **P&L diario realizado** (por fecha de salida; días sin trade = 0). |

Para extraer un par concreto, filtra el CSV de señales por las dos columnas en 1 (p. ej. S2=1 y S6=1).

## 2. Coincidencia de señales (misma barra 5m + mismo side)

**ES** — 120 señales compartidas de 302 distintas:

| | S2 | S5 | S6 | S8 |
|---|---|---|---|---|
| **S2** Normal·TC·TSR (123) | — | 0 | 60 | 56 |
| **S5** Strong·TSR·WeakConf (98) | 0 | — | 0 | 21 |
| **S6** Normal·TC·HyperWave (89) | 60 | 0 | — | 31 |
| **S8** Any·TC·WeakConf (136) | 56 | 0 | 31 | — |

**6J** — 46 señales compartidas de 140 distintas:

| | S3 | S4 | S9 |
|---|---|---|---|
| **S3** ContrarianUptrend·TrendTracer (68) | — | 11 | 35 |
| **S4** TSR·MoneyFlow50 (49) | 11 | — | 0 |
| **S9** MoneyFlow (69) | 35 | 0 | — |

- Ninguna señal es común a todas las estrategias de un mismo activo.
- Núcleos solapados: **S2/S6/S8** (las de Trend Catcher en ES) y **S3/S9** en 6J.
- Casi independientes por la entrada: **S5** (ES, Confirmation Strong, 79% exclusivas) y **S4** (6J, 78% exclusivas).

## 3. Correlación de P&L diario (Pearson)

```
        S1     S2     S3     S4     S5     S6     S7     S8     S9
 S1  +1.00  +0.05  +0.05  +0.40  +0.07  -0.00  +0.23  +0.20  +0.05
 S2  +0.05  +1.00  +0.09  +0.13  -0.00  +0.45  +0.29  +0.33  +0.00
 S3  +0.05  +0.09  +1.00  +0.06  +0.04  -0.05  -0.06  -0.10  +0.02
 S4  +0.40  +0.13  +0.06  +1.00  +0.03  +0.02  +0.03  +0.27  -0.04
 S5  +0.07  -0.00  +0.04  +0.03  +1.00  -0.18  +0.05  -0.04  -0.12
 S6  -0.00  +0.45  -0.05  +0.02  -0.18  +1.00  +0.15  +0.20  +0.04
 S7  +0.23  +0.29  -0.06  +0.03  +0.05  +0.15  +1.00  +0.20  +0.13
 S8  +0.20  +0.33  -0.10  +0.27  -0.04  +0.20  +0.20  +1.00  -0.10
 S9  +0.05  +0.00  +0.02  -0.04  -0.12  +0.04  +0.13  -0.10  +1.00
```

(Leyenda: S1 RTY · S2/S5/S6/S8 ES · S3/S4/S9 6J · S7 NQ.)

Pares más correlacionados: **S2–S6 +0.45**, **S1–S4 +0.40**, **S2–S8 +0.33**, S2–S7 +0.29, S4–S8 +0.27.
Más diversificadores (≤0): **S5–S6 −0.18**, **S5–S9 −0.12**, S3–S8 −0.10, S8–S9 −0.10.

## 4. Lectura para el riesgo conjunto (NTEXECG)

- **Coincidir en la entrada ≠ P&L correlacionado.** S3 y S9 comparten 35 entradas (51%) pero su P&L diario correla solo **+0.02**: como las salidas difieren (Trend Tracer vs Money Flow), el resultado se decorrela. Lo mismo S2–S6: 60 entradas comunes pero correlación +0.45 (alta pero no 1), porque HyperWave y Trend Strength cierran distinto.
- **Concentración real de riesgo:** el clúster ES con Trend Catcher (S2/S6/S8) es el más correlacionado entre sí (+0.20…+0.45). Correrlas las tres juntas apila exposición intradía en ES.
- **Mejores diversificadores:** **S5** (ES Strong) correla negativo con S6 (−0.18) y casi nulo con el resto; **S9** (6J MoneyFlow) y **S3** aportan series poco correlacionadas con ES/NQ. **S4** diversifica por señal pero es perdedora (PF 0.94) — su −0.04…+0.27 no compensa.
- **Sorpresa entre activos:** **S1 (RTY)–S4 (6J) +0.40** pese a ser activos y señales distintos: coinciden en días de P&L (probable beta de mercado/macro común). Tenerlo en cuenta al dimensionar.

_Correlación sobre P&L diario realizado (fecha de salida), índice de calendario común con 0 en días sin operación. Pearson._
