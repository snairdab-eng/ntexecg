# Anexo 14 — Entrada escalonada por ATR con SL fijo (ES/MES) · v1.0

**Fecha:** 2026-06-25
**Ámbito:** Propuesta de sizing para ES/MES (entrada escalonada 1-2-3 vs entrada fija).
**Estado:** **Validado vs práctica actual (fijo-2). Candidato a implementación futura.** No implementado.

## 1. Propuesta

En vez de entrar con N microcontratos fijos desde la señal, iniciar con 1 y agregar en pullbacks dentro del envelope del SL, con stop común fijo:

- Long: C1 @ señal, C2 @ −1×ATR, C3 @ −2×ATR, stop común @ −k×ATR.
- Short: inverso.
- Objetivo: menor exposición inicial, mejor precio promedio si hay pullback, y menor pérdida máxima.

## 2. Método de validación

- 120 operaciones reales de ES (LuxAlgo, 15-mar a 25-jun 2026).
- Se usa el **MAE medido por trade** para determinar qué adds entraron (MAE ≥ 1×/2×ATR) y si tocó el stop (MAE ≥ k×ATR).
- **ATR(14) real por trade** sobre barras 5m de `ohlcv_bars` (sin proxy).
- 4 esquemas comparados con el mismo stop común: fijo-1, fijo-2 (práctica actual), fijo-3, escalonado.
- P&L en dólares MES ($5/pt). Particiones in/out-of-sample 70/30. Sin lookahead.

## 3. Distribución de fills

27 trades quedaron en 1 contrato · 24 en 2 · 69 en 3 (MAE ≥ 2×ATR). Promedio ~2.35 contratos/trade. Stops: 58 (@2.5×ATR) / 54 (@2.7×ATR).
→ Los trades van profundo en contra muy seguido: el escalonado escala de verdad.

## 4. Resultados — SL 2.5×ATR

| Esquema | net | PF | peor |
|---|---|---|---|
| fijo 1 | $409 | 1.10 | −$226 |
| fijo 2 (actual) | $817 | 1.10 | −$453 |
| fijo 3 | $1,226 | 1.10 | −$679 |
| **ESCALONADO** | **$1,503** | **1.20** | **−$407** |

escalonado vs fijo-2: TODOS +$686 · IN +$724 · OUT −$38.

## 5. Resultados — SL 2.7×ATR

| Esquema | net | PF | peor |
|---|---|---|---|
| fijo 1 | $539 | 1.13 | −$244 |
| fijo 2 (actual) | $1,077 | 1.13 | −$489 |
| fijo 3 | $1,616 | 1.13 | −$733 |
| **ESCALONADO** | **$1,893** | **1.24** | **−$462** |

escalonado vs fijo-2: TODOS +$816 · IN +$635 · **OUT +$181** (robusto dentro y fuera de muestra).

## 6. Hallazgos

1. El escalonado **domina a fijo-2** (la práctica actual) en las tres métricas — más net, mejor PF, menor peor-pérdida — en ambos niveles de SL. Es una mejora Pareto.
2. No es solo leverage: los esquemas fijos comparten PF (1.10 @2.5 / 1.13 @2.7); el escalonado sube a 1.20/1.24 porque los adds entran más baratos y el 3er contrato arriesga solo ~0.5×ATR.
3. Con **2.7×ATR** la ventaja sobre fijo-2 se vuelve robusta out-of-sample (+$181 vs −$38 con 2.5).
4. El 2.7 levanta todos los esquemas (menos stops); pero esto es 24h — la decisión RTH de ES sigue en 2.5 (Anexo 11).

## 7. Caveats

- Análisis **24h**, no RTH (que es lo que opera ES). Muestra chica (120 trades, 3 meses).
- **Sin comisiones ni slippage.** El escalonado opera ~2.35 contratos/trade (vs 2 de fijo-2) → ~17% más comisiones; reduce algo la ventaja pero no la borra.
- Todo sobre la versión **con stop** (PF ~1.1-1.2 vs 2.03 nativo). El escalonado es un overlay de sizing, no recupera el edge que el stop cuesta.

## 8. Veredicto y siguientes pasos

**Validado vs la práctica actual (fijo-2): mejora real y defendible.** Candidato a implementar.

Antes de codificar:
- Re-validar con **comisiones/slippage reales** y sobre **RTH-only**.
- Probar en las **otras estrategias** (no solo ES).

Costo de implementación (no trivial):
- Lógica de órdenes escalonadas (1 market + 2 límites en reposo) con **stop a nivel posición** que ajuste cantidad conforme entran adds, y manejo de **fills parciales** y de la salida nativa antes de completar los adds.
- Decidir vía de ejecución: **bridge de NinjaTrader** (límites + stop por posición nativos) es más natural que webhooks de TradersPost.

---

# REFINAMIENTO v1.1 — Salida nativa + stop de emergencia (supera el veredicto §8)

## 9. Hallazgo clave

El problema no era el multiplicador del SL, sino **usarlo como salida**. Con la
**salida nativa de LuxAlgo como primaria** y el SL solo como **backstop ancho de
emergencia**, la estrategia conserva casi todo su edge.

### 9.1 Costo del SL en el PF (RTH, 1 contrato)

| SL | net | PF | WR | peor |
|---|---|---|---|---|
| **NATIVO (sin SL)** | $1,678 | **2.03** | 84.1% | −$1,016 |
| 1.5×ATR | −$85 | 0.95 | 36.4% | −$136 |
| 2.0×ATR | $427 | 1.25 | 47.7% | −$181 |
| 2.5×ATR (anterior) | $728 | 1.42 | 59.1% | −$226 |
| 2.7×ATR | $780 | 1.46 | 59.1% | −$244 |
| 3.0×ATR | $592 | 1.32 | 59.1% | −$272 |
| 4.0×ATR | $965 | 1.51 | 68.2% | −$362 |

Usar el stop como salida **parte el PF a la mitad** (2.03 → ~1.4). Cuanto más ancho
el stop, más se conserva el PF y mayor la cola.

### 9.2 Distribución de MAE de ganadores (justifica los niveles de add)

GANADORES (RTH): p50=1.7 · p75=3.4 · media=2.9 (×ATR). Los adds en **1.5× y 3.0×ATR**
caen justo en el p50/p75 — los pullbacks más concurrentes.

## 10. Barrido de 9 combinaciones (salida nativa + emergencia 4.0×ATR, RTH, MES $5/pt)

| Esquema | net | PF | peor | maxC | avgC | Δ vs fijo-2 actual |
|---|---|---|---|---|---|---|
| fijo-2 ACTUAL @2.5 | $1,455 | 1.42 | −$453 | 2 | 2.00 | 0 |
| fijo-2 @4.0 | $1,930 | 1.51 | −$724 | 2 | 2.00 | +$474 |
| fijo-3 @4.0 | $2,895 | 1.51 | −$1,086 | 3 | 3.00 | +$1,439 |
| **E3 1-2-0** | $3,065 | **1.72** | −$815 | 3 | 2.27 | +$1,610 |
| E5 1-2-1 | $3,438 | 1.73 | −$905 | 4 | 2.66 | +$1,982 |
| E7 1-2-2 | $3,810 | 1.73 | −$996 | 5 | 3.05 | +$2,355 |
| E9 2-2-2 | $4,775 | 1.67 | −$1,358 | 6 | 4.05 | +$3,320 |

(Todos los @4.0 stopean 13/44 vs 18/44 del 2.5 actual.)

## 11. Protección de cola (peor trade con vs sin stop de emergencia)

| Combo | peor @4.0×ATR | peor SIN SL | salva |
|---|---|---|---|
| E3 1-2-0 | −$815 | −$2,777 | $1,962 |
| E5 1-2-1 | −$905 | −$3,522 | $2,617 |
| E7 1-2-2 | −$996 | −$4,267 | $3,271 |
| E9 2-2-2 | −$1,358 | −$5,283 | $3,925 |

El stop de emergencia casi no toca el trade típico, pero **capa la cola**: sin él, un
solo mal trade a 3–5 contratos (tipo #108) cuesta −$2,777 a −$5,283.

## 12. CONFIGURACIÓN FINAL RECOMENDADA

- **Salida principal:** LuxAlgo nativa.
- **Stop operativo normal:** NO.
- **Stop de emergencia:** SÍ, **4.0×ATR**.
- **Escenario base:** **E3 (1-2-0)** — 1 @ señal + 2 @ −1.5×ATR. PF 1.72, net $3,065
  (+111% vs fijo-2 actual), peor −$815, el más eficiente ($1,350/contrato) y de menor cola.
- **Escenario agresivo:** **E5 (1-2-1)** — + 1 @ −3.0×ATR. PF 1.73, net $3,438 (+136%), peor −$905.

### Implementación en dos fases
1. **Fase 1 (config, sin código):** ensanchar el SL de ES a 4.0×ATR + confiar en la
   salida nativa. Solo con esto, fijo-2 pasa de PF 1.42 a 1.51 (+$474).
2. **Fase 2 (código):** lógica de órdenes escalonadas (1 market + adds límite) con stop
   a nivel posición y manejo de fills parciales.

### Caveats
44 trades RTH **in-sample**, **sin comisiones/slippage**, sin out-of-sample. El peor
caso por trade crece (−$453 actual → −$815 E3). Validar **OOS + comisiones** antes de
operar en serio.
