# Reporte de calibración — YM (CBOT_MINI:YM1!)

**Instrumento:** E-mini Dow Jones (YM) · se opera en micro **MYM** ($/pt ÷10)
**Timeframe:** **15 min** (≠ ES/NQ que son 5m) · **Estrategia:** LuxAlgo® Backtester (S&O), Pine **protegido** (`PUB;bd27017692354be0877227c3b822dcdd` v38)
**Periodo backtest:** 13-ago-2025 → 19-jun-2026 (~10 meses) · **Fuente:** CSV autorizado `..._CBOT_MINI_YM1!_2026-06-26_081bb.csv` (48 trades cerrados)
**Multiplicador detectado:** YM $5/pt · MYM $0.50/pt · **ATR proxy 15m:** ~92 pts

> Config leída en vivo de la pestaña YM propia (estudios `jp7rv3` + `ZHPyGl`, layout `r7kGdNqY`). Métricas $ = contrato estándar; para **MYM** dividir entre 10; los % no cambian.

---

## 1. Señal / Carácter — ⚠️ MECANISMO DISTINTO a ES y NQ
- **Carácter:** **contrarian / mean-reversion en extremos de oscilador.**
- **Indicador que alimenta la señal:** **LuxAlgo Oscillator Matrix™** (`ZHPyGl`) — NO la Confirmation de Signals & Overlays.
- **Constructor de condiciones REAL de YM (`in_7`):**
  ```
  @long()  = {bullish_contrarian_any} AND {bearish_neo} AND {hyperwave_below_20} AND fecha ≥ ~2025-08-11
  @short() = {bearish_contrarian_any} AND {bullish_neo} AND {hyperwave_above_80} AND fecha ≥ ~2025-08-11
  @exit_all() = {contrarian_exits}
  ```
- Largo = señal **Contrarian alcista** + **Neo bajista** + **Hyperwave < 20 (sobreventa)** → compra extremos de sobreventa. Short = espejo en sobrecompra (Hyperwave > 80).
- **Sin EMAs, sin Smart Trail, sin filtro "ranging".** Salida por señal contrarian (`contrarian_exits`); sin SL/TP propio.
- `in_10 = "ZHPyGl$0"` confirma que el OscMatrix es la fuente. Pine protegido.

### Diferencias YM vs ES vs NQ
| Componente | ES | NQ | **YM** |
|---|---|---|---|
| Indicador señal | Confirmation (S&O) | Confirmation + Smart Trail | **Oscillator Matrix™** |
| Lógica | fade en rango | pullback/continuación | **contrarian en extremos (Hyperwave 20/80)** |
| Filtro régimen | ranging only | — | — |
| Timeframe | 5m | 5m | **15m** |
| Indicadores extra | 3 EMAs (visuales) | ninguno | **Oscillator Matrix** |
| Gate de fecha | ~mar-2026 | ~mar-2026 | **~ago-2025** |

## 2. Key Stats (recomputadas del CSV autorizado)
| Métrica | YM estándar | MYM (÷10) |
|---|---|---|
| Trades | 48 | 48 |
| Win% | 89.6% (43/5) | 89.6% |
| Profit Factor | 1.92 | 1.92 |
| Net PnL | $22,690 | $2,269 |
| Max DD | $9,175 | $918 |
| Expectancy | $473 (94.5 pt) | $47.3 |
| Avg winner | $1,099 | $110 |
| Avg loser | −$4,913 | −$491 |

> **Baja frecuencia** (48 trades en ~10 meses ≈ 1/semana — contrarian en 15m es selectivo). **Calmar ~2.5** (sano, similar a ES). Win rate altísimo (89.6%) con pérdidas ocasionales grandes (avgL 4.5× avgW).

## 3. Riesgo — MAE de ganadores (proxy ATR 92 pt, 15m)
| Estadístico | Puntos | ×ATR |
|---|---|---|
| Media | 247.7 | 2.69× |
| Mediana | 130.0 | 1.41× |
| % ganadores con MAE > 1.5×ATR | **44%** | |

> Como ES, ~44% de ganadores aguantan >1.5×ATR; un SL de 1.5×ATR cortaría casi la mitad.

## 4. Análisis por ventana (resultado real)
| Ventana | n | WR% | PF | exp$ | net$ | maxDD$ |
|---|---|---|---|---|---|---|
| **24h** | 48 | 89.6 | **1.92** | +473 | **+22,690** | 9,175 |
| **RTH 09:30–15:45** | 9 | 66.7 | **0.43** | −872 | **−7,850** | 10,745 |
| AM 09:30–12:00 | 7 | 57.1 | 0.41 | −1,160 | −8,120 | 10,875 |
| PM 12:00–15:45 | 2 | 100.0 | ∞ | +135 | +270 | 0 |

> **Igual que NQ: la ventana RTH DESTROZA YM** (PF 0.43). El edge vive en **24h/overnight**. Muestra RTH diminuta (9 trades) → señal de dirección, no de magnitud.

## 5. Barrido de SL (TP 6×ATR; proxy 92 pt)
**24h:**
| k×ATR | WR% | PF | exp$ | net$ | maxDD$ |
|---|---|---|---|---|---|
| **real (sin SL)** | 89.6 | **1.92** | **473** | **22,690** | 9,175 |
| 1.5 | 50.0 | 1.14 | 47 | 2,245 | 6,080 |
| 2.0 | 56.2 | 1.19 | 76 | 3,665 | 7,390 |
| 2.5 | 62.5 | 1.32 | 139 | 6,650 | 8,305 |
| 3.0 | 66.7 | 1.37 | 170 | 8,160 | 8,355 |
| **4.0** | 72.9 | **1.43** | **216** | 10,350 | 6,150 |

- **El nativo (sin stop) es lo mejor** (PF 1.92). Entre stops forzados, **k=4** es el menos malo; **1.5×ATR de nuevo es pobre**.
- El **TP 6×ATR sí dispara** algunas veces (3–6), a diferencia de NQ.
- **RTH negativo en todos los k** (mejor k=1.5: −$307) → ningún stop rescata la sesión.

## 6. Señales últimos ~10 días (hora NY)
| Entrada (NY) | Dir | Precio | PnL$ std | MAE pt | Sesión |
|---|---|---|---|---|---|
| 18-jun 07:45 | LONG | 52040 | +580 | 10 | off |
| 19-jun 07:30 | LONG | 51833 | +105 | 121 | off |

(Solo 2 señales en 10 días — baja frecuencia.) **Alertas:** 0 a nivel cuenta; payload por gateway externo.

---

## 🎯 Recomendación YM (vs ES / NQ)
| Parámetro | ES | NQ | **YM** |
|---|---|---|---|
| Ventana | RTH 09:20–15:45 | NO RTH (24h) | **NO RTH → 24h** |
| sl_atr_multiplier | 2.5×ATR | 4.0×ATR | **nativo / 4.0×ATR si forzado** |
| ¿Señal cruda basta? | Sí | No | **Sí (en 24h)** |
| QualityScorer/HMM | no | recomendado | **solo si se fuerza RTH** |

**Conclusión:** YM funciona bien **en 24h con la señal cruda** (PF 1.92, WR 89.6%) y su mejor gestión de salida es la **nativa por señal** (los stops fijos solo restan); si el gateway obliga bracket, usar **≥4×ATR**. **Como NQ, la ventana RTH es destructiva** (PF 0.43) → **no operar RTH**. El 1.5×ATR vuelve a ser el peor punto en todos los casos.

> **Caveats:** TF 15m (ATR proxy 92 pt en 15m); baja frecuencia (48 trades / 10 meses); RTH = 9 trades (muestra muy pequeña). Dirección robusta, magnitudes aproximadas.

---
*Generado en sesión de calibración NTEXECG. Config leída en vivo de la pestaña YM; métricas del CSV autorizado `081bb`.*
