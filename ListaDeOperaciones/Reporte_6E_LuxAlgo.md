# Reporte de calibración — 6E (CME:6E1!)

**Instrumento:** Euro FX Futures (6E) · se opera en micro **M6E** ($/pt ÷10)
**Timeframe:** **5 min** · **Estrategia:** LuxAlgo® Backtester (S&O), Pine **protegido** (`PUB;bd27017692354be0877227c3b822dcdd` v38)
**Periodo backtest:** 16-mar-2026 → 26-jun-2026 · **Fuente:** CSV autorizado `..._CME_6E1!_2026-06-26_9ed3f.csv` (99 trades cerrados)
**Multiplicador detectado:** 6E $125,000/pt · M6E $12,500/pt · **ATR proxy 5m:** ~0.00030 (≈$38; ~6 ticks)

> Config leída en vivo de la pestaña 6E propia (estudios `RwUp54` + `5dwQAD`, layout `r7kGdNqY`). Métricas $ = contrato estándar; para **M6E** dividir entre 10; los % no cambian.

---

## 1. Señal / Carácter — stack de confluencia/momentum (todo alineado)
- **Carácter:** **confluencia/momentum a favor** — sin elemento contrarian ni Trend Catcher opuesto; todas las condiciones apuntan en la misma dirección.
- **Constructor de condiciones REAL (`in_7`):**
  ```
  @long()  = {bullish_confirmation+} AND {bullish_neo} AND {weak_bullish_confluence} AND fecha ≥ ~2026-03-04
  @short() = {bearish_confirmation+} AND {bearish_neo} AND {weak_bearish_confluence} AND fecha ≥ ~2026-03-04
  @exit_all() = {confirmation_exits}
  ```
- Usa **Confirmation+ (variante fuerte) + Neo + Confluence (débil)**, todo bullish para largo. `in_10 = "5dwQAD$0"` confirma OscMatrix como fuente. Salida por señal; sin SL/TP propio. Pine protegido.

### Diferencias 6E vs resto
| | señal base | elemento opuesto | régimen | TF |
|---|---|---|---|---|
| ES | Confirmation | Catcher opuesto | ranging | 5m |
| NQ | Confirmation+SmartTrail | Catcher opuesto | — | 5m |
| YM | OscMatrix | Neo opuesto | — | 15m |
| RTY | Confirmation+Neo | — | trending | 15m |
| GC | OscMatrix contrarian | — | — | 5m |
| CL | contrarian+Catcher | — | trending | 15m |
| **6E** | **Confirmation+ + Neo + Confluence** | **ninguno (todo alineado)** | — | 5m |

## 2. Key Stats (recomputadas del CSV autorizado)
| Métrica | 6E estándar | M6E (÷10) |
|---|---|---|
| Trades | 99 | 99 |
| Win% | 84.8% (84/15) | 84.8% |
| Profit Factor | 1.44 | 1.44 |
| Net PnL | $3,662 | $366 |
| Max DD | $2,994 | $299 |
| Expectancy | $37.0 | $3.7 |
| Avg winner | $143 | $14 |
| Avg loser | −$556 | −$56 |

> **Importe absoluto bajo** (6E se mueve poco; ~$38/ATR). Calmar ~1.2 — el más flojo de la cartera en 24h. La rentabilidad real aparece **al filtrar a RTH** (ver §4).

## 3. Riesgo — MAE de ganadores (proxy ATR $38, 5m)
| Estadístico | USD | ×ATR | ticks |
|---|---|---|---|
| Media | $121 | 3.22× | 19 |
| Mediana | $88 | 2.33× | 14 |
| % > 1.5×ATR | — | **58%** | |

> Ganadores aguantan mucho calor (58% >1.5×ATR). (ATR proxy de ventana baja-vol → ×ATR aprox.)

## 4. Análisis por ventana — RTH es BUENO (como ES/RTY)
| Ventana | n | WR% | PF | exp$ | net$ | maxDD$ |
|---|---|---|---|---|---|---|
| 24h | 99 | 84.8 | 1.44 | 37 | 3,662 | 2,994 |
| **RTH 09:30–15:45** | 20 | 90.0 | **1.84** | 61 | 1,219 | 1,200 |
| **AM 09:30–12:00** | 9 | 100.0 | **∞** | 161 | 1,450 | 0 |
| PM 12:00–15:45 | 11 | 81.8 | 0.84 | −21 | −231 | 1,200 |

> **6E funciona en RTH** (PF 1.84, WR 90%), con la **mañana impecable** (AM 9/9). Los grandes perdedores son overnight (ver §6) → filtrar a RTH los elimina. La tarde (PM) resta.

## 5. Barrido de SL (TP 6×ATR; proxy $38)
**RTH 09:30–15:45:**
| k×ATR | WR% | PF | exp$ | net$ | maxDD$ |
|---|---|---|---|---|---|
| real (sin SL) | 90.0 | 1.84 | 61 | 1,219 | 1,200 |
| 1.5 | 45.0 | 2.70 | 53 | 1,050 | 225 |
| 2.0 | 55.0 | 2.94 | 65 | 1,306 | 300 |
| 2.5 | 60.0 | 2.94 | 73 | 1,456 | 375 |
| 3.0 | 70.0 | 3.62 | 88 | 1,769 | 231 |
| **4.0** | 85.0 | **5.97** | **112** | 2,238 | 194 |

**24h:** real PF 1.44; entre stops óptimo k=4 (PF 1.37).

- **En RTH, un stop ANCHO (k=4) mejora mucho** (PF 5.97 vs 1.84 nativo): capa los 2 perdedores sin tocar casi ganadores. **1.5×ATR de nuevo el peor** (aunque en RTH sigue positivo por la alta WR).
- TP 6×ATR dispara bastante aquí (4–5 hits) por ATR proxy bajo.

## 6. Señales últimos ~10 días (hora NY)
| Entrada (NY) | Dir | Precio | PnL$ std | MAE | Sesión |
|---|---|---|---|---|---|
| 16-jun 06:15 | LONG | 1.16490 | **−1,875** | 314 tk | off |
| 19-jun 07:05 | LONG | 1.15045 | −938 | 165 tk | off |
| 23-jun 10:00 | SHORT | 1.14295 | +138 | 3 tk | RTH |
| 24-jun 15:05 | LONG | 1.13975 | +81 | 24 tk | RTH |
| 26-jun 03:35 | LONG | 1.14175 | +212 | 0 tk | off |

> Los dos grandes perdedores (−$1,875, −$938) son **overnight** → refuerza operar solo RTH. **Alertas:** 0 a nivel cuenta; payload por gateway externo.

---

## 🎯 Recomendación 6E
| Parámetro | Valor |
|---|---|
| Ventana | **RTH 09:30–15:45 (priorizar AM 09:30–12:00)** ✅ |
| sl_atr_multiplier | **4.0×ATR** (en RTH mejora al nativo; PF 5.97) |
| TP | 6×ATR (recalcular con ATR real) |
| ¿Señal cruda basta? | **Sí (en RTH)** |
| QualityScorer/HMM | innecesario si se opera RTH |

**Conclusión:** 6E es **instrumento de RTH** (como ES/RTY): en sesión gana (PF 1.84; AM 100%) y un **stop ancho de 4×ATR lo potencia** (PF 5.97). En 24h es flojo (Calmar ~1.2) por los perdedores nocturnos. **Importe absoluto pequeño** (M6E ~$366 net en el periodo) → revisar si compensa el esfuerzo/comisiones frente a RTY/CL. El 1.5×ATR vuelve a ser subóptimo.

> **Caveats:** ATR proxy bajo-vol (×ATR y barrido aprox., recalcular con ATR real); RTH = 20 trades, AM = 9 (muestra pequeña); rentabilidad absoluta marginal en micro.

---
*Generado en sesión de calibración NTEXECG. Config leída en vivo de la pestaña 6E; métricas del CSV autorizado `9ed3f`.*
