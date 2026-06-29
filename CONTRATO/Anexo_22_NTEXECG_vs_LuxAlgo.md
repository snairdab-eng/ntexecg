# Anexo 22 — Comparativo NTEXECG hoy vs LuxAlgo directo (2 microcontratos)

**Fecha:** 2026-06-29 · **Ámbito:** las 8 estrategias · **Script:** `scripts/compare_ntexecg_vs_luxalgo.py` (solo lectura)

## 1. Qué se compara

- **A — NTEXECG hoy:** la estrategia tal como NTEXECG la reenvía: **SL por ATR** + **entrada escalonada**
  (con/sin fill según el MAE real) + **TP 6×ATR** + **filtros** (GC score≥55, YM régimen *ranging*) +
  **ventana** (RTH donde aplica), con el **sizing real de cada diseño** (ES 5, NQ 4, YM 4, RTY 3, 6E 3,
  6J 3, GC 3, CL 3 microcontratos repartidos en los niveles).
- **B — LuxAlgo directo:** la señal nativa, **sin SL ni filtros**, **24h**, a **2 microcontratos** fijos.

## 2. Método (y validación)

Sobre las listas de operaciones reales (que traen **P&L, MAE y MFE por trade**) + **ATR(14) real** por trade
(barras HOLC del timeframe de la señal). Por cada trade de A: se decide qué legs llenan (MAE ≥ nivel×ATR),
si tocó el **SL** (MAE ≥ k×ATR) o el **TP** (MFE ≥ 6×ATR); cada leg que llenó entró su `nivel×ATR` más barato
(mejor precio promedio). `$/punto` derivado empíricamente de cada lista. Todo en **USD de microcontrato**.

**Validación:** A reproduce el Anexo 20 (ES PF 2.39/DD −891/peor −524 ≈ 2.34/891/524; 6E 6.52/−45; RTY
11.55/−290; 6J 1.37/−87). B reproduce los nativos de los reportes (ES avg ganador ≈ $780/2 micros ≈ $784 del
reporte). **Caveats:** sin comisiones/slippage; sin secuencia intrabar (si MAE≥SL se asume stop); ATR(14)
real por trade pero el fill exacto es aproximado por MAE/MFE.

## 3. Tabla comparativa (USD de microcontrato)

> Nota de lectura: A y B tienen **tamaños distintos** (A = sizing del diseño; B = 2 micros fijos), así que
> los importes absolutos (gan/pérd promedio, neto) reflejan ese tamaño real. El **PF** y la **forma del
> riesgo** (DD, peor) son lo más comparable.

| Estrategia | Var | n | Gana­doras | Perde­doras | Gan. prom $ | Pérd. prom $ | PF | Neto $ | Drawdown $ | Peor $ |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **ES** | A · NTEXECG | 37 | 18 | 19 | 566 | −224 | **2.39** | 5,933 | **−891** | **−524** |
| ES | B · LuxAlgo 2m | 122 | 99 | 23 | 156 | −390 | 1.72 | 6,448 | −2,350 | −2,032 |
| **NQ** | A · NTEXECG | 33 | 16 | 17 | 1,131 | −523 | **2.03** | **9,206** | **−1,789** | **−842** |
| NQ | B · LuxAlgo 2m | 65 | 54 | 11 | 348 | −1,188 | 1.44 | 5,732 | −7,177 | −6,345 |
| **YM** | A · NTEXECG | 16 | 10 | 6 | 521 | −552 | 1.57 | 1,897 | −1,731 | **−883** |
| YM | B · LuxAlgo 2m | 48 | 43 | 5 | 220 | −983 | **1.92** | 4,538 | −1,835 | −1,835 |
| **GC** | A · NTEXECG | 21 | 12 | 9 | 1,149 | −312 | **4.91** | 10,974 | **−812** | **−549** |
| GC | B · LuxAlgo 2m | 107 | 65 | 42 | 854 | −677 | 1.95 | **27,078** | −7,738 | −5,878 |
| **RTY** | A · NTEXECG | 11 | 10 | 1 | 334 | −290 | **11.55** | 3,054 | **−290** | **−290** |
| RTY | B · LuxAlgo 2m | 112 | 97 | 14 | 172 | −555 | 2.15 | 8,924 | −1,493 | −1,483 |
| **6E** | A · NTEXECG | 20 | 16 | 4 | 47 | −29 | **6.52** | 633 | **−45** | **−45** |
| 6E | B · LuxAlgo 2m | 99 | 84 | 15 | 29 | −111 | 1.44 | 732 | −599 | −375 |
| **6J** | A · NTEXECG | 45 | 26 | 19 | 37 | −36 | 1.37 | 257 | −320 | **−87** |
| 6J | B · LuxAlgo 2m | 78 | 73 | 4 | 14 | −64 | **3.99** | 766 | −135 | −135 |
| **CL** | A · NTEXECG | 47 | 24 | 23 | 452 | −492 | 0.96 | −468 | −2,435 | −1,669 |
| CL | B · LuxAlgo 2m | 105 | 82 | 23 | 196 | −521 | **1.34** | **4,090** | −4,474 | −4,166 |

**Totales cartera (suma de las 8, USD micro):**

| | Neto $ | Σ Peor trade $ | Comentario |
|---|---:|---:|---|
| **A — NTEXECG** | **+31,486** | **−4,889** | menos trades, riesgo de cola controlado |
| **B — LuxAlgo 2m** | **+58,308** | **−22,249** | más neto bruto, colas ~4.5× mayores |

## 4. Conclusiones

1. **El valor principal de NTEXECG es el control de riesgo.** El SL recorta drásticamente el drawdown y la
   peor pérdida: NQ peor −$842 vs −$6,345; GC DD −$812 vs −$7,738; la suma de peores trades cae de
   −$22,249 (B) a −$4,889 (A), **~4.5× menos riesgo de cola**.
2. **PF más alto en la mayoría** (ES, NQ, GC, RTY, 6E) gracias a SL + filtros + entrada más barata.
3. **Menor neto bruto** porque A opera mucho menos (filtros + ventana + fills límite) y a veces deja ganancia
   sobre la mesa. Es una mejora **ajustada por riesgo**, no maximizadora de neto.
4. **Donde el nativo (B) gana:** **6J** (PF 3.99 vs 1.37) y **YM** (1.92 vs 1.57) — su salida nativa domina y
   el SL/escalonado les resta; y **CL**, donde A queda en **PF 0.96 (neto −$468)**: el diseño límite
   [0,0,3] a −2.5×ATR llena poco y el SL 8× no ayuda. CL es la más débil de A.
5. **NQ es el mejor caso de A:** gana en **neto Y riesgo** ($9,206 vs $5,732 con 1/4 del drawdown).

## 5. Implicación operativa

- Mantener NTEXECG como capa de **gestión de riesgo** es claramente positivo en ES, NQ, GC, RTY, 6E.
- Revisar **6J y YM**: su nativo es mejor; considerar SL solo-catástrofe (sin escalonado límite) o dejarlos
  más cerca del nativo.
- **CL**: el diseño actual no aporta; candidata a rediseñar (C1 a mercado) o mantener en shadow.
- Recordatorio: esto es backtest sin comisiones/slippage; la cuenta **demo** de esta semana dará el dato real.
