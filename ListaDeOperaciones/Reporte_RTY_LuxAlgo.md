# Reporte de calibración — RTY (CME_MINI:RTY1!)

**Instrumento:** E-mini Russell 2000 (RTY) · se opera en micro **M2K** ($/pt ÷10)
**Timeframe:** **15 min** · **Estrategia:** LuxAlgo® Backtester (S&O), Pine **protegido** (`PUB;bd27017692354be0877227c3b822dcdd` v38)
**Periodo backtest:** 12-ago-2025 → 23-jun-2026 (~10 meses) · **Fuente:** CSV autorizado `..._CME_MINI_RTY1!_2026-06-26_f7d11.csv` (112 trades cerrados)
**Multiplicador detectado:** RTY $50/pt · M2K $5/pt · **ATR proxy 15m:** ~8.2 pts

> Config leída en vivo de la pestaña RTY propia (estudio `NUiQKw`, layout `r7kGdNqY`). Métricas $ = contrato estándar; para **M2K** dividir entre 10; los % no cambian.

---

## 1. Señal / Carácter — estrategia de TENDENCIA
- **Carácter:** **trend-following / momentum** (¡opuesto al fade en rango de ES!).
- **Constructor de condiciones REAL (`in_7`):**
  ```
  @long()  = {bullish_confirmation} AND {bearish_neo} AND {trend_strength_trending} AND fecha ≥ ~2025-08-12
  @short() = {bearish_confirmation} AND {bullish_neo} AND {trend_strength_trending} AND fecha ≥ ~2025-08-12
  @exit_all() = {confirmation_exits}
  ```
- **Filtro de régimen:** `trend_strength_trending` → **solo opera cuando hay TENDENCIA** (ES exigía *ranging*; RTY exige *trending* — opuesto).
- Confirmation + Neo + filtro de tendencia. Sin EMAs ni indicadores extra. Salida por señal; sin SL/TP propio.
- Pine protegido.

### Diferencias RTY vs ES / NQ / YM
| Componente | ES | NQ | YM | **RTY** |
|---|---|---|---|---|
| Lógica | fade en rango | pullback | contrarian extremos | **trend/momentum** |
| Filtro régimen | ranging | — | — | **trending** |
| Señal base | Confirmation | Confirmation+SmartTrail | OscMatrix | **Confirmation+Neo** |
| Timeframe | 5m | 5m | 15m | **15m** |
| Indicadores extra | 3 EMAs | — | OscMatrix | — |

## 2. Key Stats (recomputadas del CSV autorizado)
| Métrica | RTY estándar | M2K (÷10) |
|---|---|---|
| Trades | 112 | 112 |
| Win% | 86.6% (97/14) | 86.6% |
| Profit Factor | 2.15 | 2.15 |
| Net PnL | $44,620 | $4,462 |
| Max DD | $7,465 | $746 |
| Expectancy | $398 (8.0 pt) | $39.8 |
| Avg winner | $860 | $86 |
| Avg loser | −$2,774 | −$277 |

> **El mejor perfil de la cartera: Calmar ~6.0** (Net/DD). Win rate 86.6% con 112 trades en ~10 meses.

## 3. Riesgo — MAE de ganadores (proxy ATR 8.2 pt, 15m)
| Estadístico | Puntos | ×ATR |
|---|---|---|
| Media | 15.8 | 1.93× |
| Mediana | 10.4 | 1.27× |
| % ganadores con MAE > 1.5×ATR | **46%** | |

## 4. Análisis por ventana — 🔑 RTH es EXCELENTE (opuesto a NQ/YM)
| Ventana | n | WR% | PF | exp$ | net$ | maxDD$ |
|---|---|---|---|---|---|---|
| 24h | 112 | 86.6 | 2.15 | 398 | 44,620 | 7,465 |
| **RTH 09:30–15:45** | 22 | 86.4 | **6.90** | **776** | 17,080 | **1,440** |
| **AM 09:30–12:00** | 11 | 90.9 | **24.0** | **1,202** | 13,225 | 575 |
| PM 12:00–15:45 | 12 | 75.0 | 2.13 | 273 | 3,280 | 2,195 |

> **RTY brilla en RTH** (PF 6.90, maxDD solo $1,440 → Calmar ~12) y **espectacular en la mañana** (AM PF 24, exp $1,202). Es el instrumento ideal para sesión RTH, **como ES pero más fuerte**. (Muestra RTH = 22 trades, AM = 11 → señal de dirección clara, magnitudes con cautela.)

## 5. Barrido de SL (TP 6×ATR; proxy 8.2 pt)
**RTH 09:30–15:45:**
| k×ATR | WR% | PF | exp$ | net$ | maxDD$ |
|---|---|---|---|---|---|
| **real (sin SL)** | 86.4 | **6.90** | **776** | 17,080 | 1,440 |
| 1.5 | 50.0 | 2.03 | 316 | 6,940 | 1,845 |
| 2.0 | 68.2 | 2.71 | 446 | 9,815 | 2,460 |
| 2.5 | 72.7 | 2.60 | 447 | 9,840 | 1,615 |
| 3.0 | 77.3 | 2.73 | 484 | 10,640 | 2,025 |
| **4.0** | 81.8 | **2.74** | **520** | 11,435 | 2,380 |

**24h:** real PF 2.15 (mejor); entre stops óptimo k=2.5 (PF 1.46).

- **El nativo (sin stop) domina** (RTH PF 6.90). Entre stops forzados, **k=4** es el mejor; **1.5×ATR de nuevo el peor**.
- El TP 6×ATR (49 pt) casi no dispara (2–4 hits).

## 6. Señales últimos ~10 días (hora NY)
| Entrada (NY) | Dir | Precio | PnL$ std | MAE pt | Sesión |
|---|---|---|---|---|---|
| 23-jun 05:15 | LONG | 2978 | +2,850 | 13 | off |

(1 señal en 10 días; frecuencia global ~2.6/semana.) **Alertas:** 0 a nivel cuenta; payload por gateway externo.

---

## 🎯 Recomendación RTY
| Parámetro | Valor recomendado |
|---|---|
| Ventana | **RTH 09:30–15:45 (priorizar AM 09:30–12:00)** ✅ |
| sl_atr_multiplier | **nativo / 4.0×ATR si el gateway lo fuerza** |
| TP | 6×ATR (casi no actúa) |
| ¿Señal cruda basta? | **Sí — excelente** |
| QualityScorer/HMM | **innecesario** |

**Conclusión:** RTY es el instrumento más sólido. Estrategia de **tendencia que funciona muy bien en RTH** (PF 6.90; AM PF 24). La salida nativa por señal es la mejor; si el bracket es obligatorio, **≥4×ATR**. **Como ES, opera RTH**; al contrario de NQ/YM. El 1.5×ATR vuelve a ser el peor punto.

> **Caveats:** TF 15m (ATR proxy 8.2 pt); RTH = 22 trades, AM = 11 (muestra pequeña pese al PF altísimo). Dirección muy robusta, magnitudes aproximadas.

---
*Generado en sesión de calibración NTEXECG. Config leída en vivo de la pestaña RTY; métricas del CSV autorizado `f7d11`.*
