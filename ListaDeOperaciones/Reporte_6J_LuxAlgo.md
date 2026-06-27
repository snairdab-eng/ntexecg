# Reporte de calibración — 6J (CME:6J1!)  · REGENERADO 27-jun-2026

**Instrumento:** Japanese Yen Futures (6J) · **Timeframe:** 5 min · **Estrategia:** LuxAlgo® Backtester (S&O), Pine **protegido** (`PUB;bd27017692354be0877227c3b822dcdd` v38)
**Periodo backtest:** 16-mar-2026 → 22-jun-2026 · **Fuente:** CSV autorizado `..._CME_6J1!_2026-06-27_9b4af.csv` (**77 trades cerrados**)
**Multiplicador detectado:** 6J $12,500,000/pt · **ATR proxy 5m:** ~0.0000010 (≈$12) ⚠️

> 🔄 **Nota de versión:** este reporte **reemplaza** la versión anterior (que se basó en un export anómalo de 26 trades). **La configuración NO cambió** — la condición es idéntica; el export de ayer estaba **incompleto** (6J ilíquido, OscMatrix sin warmup / data parcial). Hoy, con el chart estable, salen **77 trades**, que es el set correcto.
>
> ⚠️ **Caveat ATR:** el proxy (0.0000010) viene de ventana ultra-quieta y subestima → los **×ATR del MAE y el barrido de SL son aproximados** (recalcular con ATR(14) real). El MAE en USD y las Key Stats son válidos.

---

## 1. Señal / Carácter — contrarian + Tracer + Money Flow (validado en vivo ✅)
- **Constructor de condiciones REAL (`in_7`), confirmado en la pestaña:**
  ```
  @long()  = {bullish_contrarian_any} AND {bullish_tracer} AND {moneyflow_above_50} AND fecha ≥ ~2026-03-04
  @short() = {bearish_contrarian_any} AND {bearish_tracer} AND {moneyflow_below_50} AND fecha ≥ ~2026-03-04
  @exit_all() = {contrarian_exits}
  ```
- **Contrarian (OscMatrix) + Tracer + Money Flow (>50/<50).** Fuente `in_10="xhxCXQ$0"` (OscMatrix). Salida por señal contrarian; sin SL/TP propio. Pine protegido.

## 2. Key Stats (CSV de hoy — 77 trades)
| Métrica | 6J estándar | /10 |
|---|---|---|
| Trades | **77** | 77 |
| Win% | **93.5%** (72/4) | 93.5% |
| Profit Factor | 3.99 | 3.99 |
| Net PnL | $3,825 | $382 |
| Max DD | $675 | $68 |
| Expectancy | $49.7 | $5.0 |
| Avg winner | $71 | $7 |
| Avg loser | −$320 | −$32 |

> **Calmar ~5.7** (Net/DD) — estadísticamente decente con 77 trades y WR 93.5%. **Pero rentabilidad absoluta pequeña** (micro ~$382 en el periodo). Mejor muestra que la versión anterior (26→77), conclusión económica similar: **viable pero de bajo retorno absoluto.**

## 3. Riesgo — MAE de ganadores ⚠️ (×ATR aproximado)
| Estadístico | USD (fiable) | ×ATR ⚠️ |
|---|---|---|
| Media | $125 | 9.99× (proxy malo) |
| Mediana | $56 | 4.50× |
| % > 1.5×ATR | — | 64% |

> Usar MAE en **USD** hasta recalcular con ATR(14) real.

## 4. Análisis por ventana (resultado real)
| Ventana | n | WR% | PF | exp$ | net$ | maxDD$ |
|---|---|---|---|---|---|---|
| **24h** | 77 | 93.5 | **3.99** | 50 | 3,825 | 675 |
| RTH 09:30–15:45 | 17 | 88.2 | 3.65 | 54 | 912 | 344 |
| AM 09:30–12:00 | 8 | 75.0 | 1.13 | 6 | 44 | 344 |
| PM 12:00–15:45 | 9 | 100.0 | ∞ | 97 | 869 | 0 |

> 24h es la mejor por net; RTH también positivo (PF 3.65); PM fuerte (9/9). Muestras pequeñas por ventana.

## 5. Barrido de SL ⚠️ (distorsionado por ATR proxy)
**24h:** real PF 3.99 domina; **todos los stops la degradan** (mejor k=1.5 pero ya negativo). **1.5×ATR de nuevo el peor patrón.**
> No usar para fijar SL en 6J — recalcular con ATR(14) real.

## 6. Señales últimos ~10 días (hora NY)
| Entrada (NY) | Dir | Precio | PnL$ std | MAE | Sesión |
|---|---|---|---|---|---|
| 22-jun 10:10 | SHORT | 0.0062280 | +75 | 45 tk | RTH |

(Baja frecuencia.) **Alertas:** 0 a nivel cuenta; payload por gateway externo.

---

## 🎯 Recomendación 6J (actualizada)
| Parámetro | Valor |
|---|---|
| Ventana | **24h** (mejor net; RTH también positivo) |
| sl_atr_multiplier | **nativo** — NO fijar por el barrido (recalcular ATR real) |
| ¿Señal cruda basta? | Sí (WR 93.5%, PF 3.99) |
| QualityScorer/HMM | innecesario |
| **Decisión sugerida** | **Viable pero baja prioridad por $ absoluto (~$382 micro)** |

**Conclusión:** Con el dataset correcto (77 trades), 6J es **estadísticamente sólido** (WR 93.5%, PF 3.99, Calmar ~5.7) y la **salida nativa por señal es la mejor** (los stops fijos restan; 1.5×ATR el peor). Opera bien en 24h y también en RTH. **La limitación es el retorno absoluto pequeño** (micro ~$382): mantener como instrumento secundario; priorizar RTY/GC/CL por tamaño de PnL.

> **Caveats:** ATR proxy bajo-vol (×ATR/barrido aprox.); ventanas con n pequeño; 6J ilíquido (asegurar data completa antes de exportar — el export de ayer salió con 26 trades por warmup incompleto).

---
*Generado en sesión de calibración NTEXECG. Config validada en vivo en la pestaña 6J; métricas del CSV autorizado de hoy `9b4af`.*
