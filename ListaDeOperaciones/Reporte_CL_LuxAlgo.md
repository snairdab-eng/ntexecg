# Reporte de calibración — CL (NYMEX:CL1!)

**Instrumento:** Crude Oil Futures (CL) · se opera en micro **MCL** ($/pt ÷10)
**Timeframe:** **15 min** · **Estrategia:** LuxAlgo® Backtester (S&O), Pine **protegido** (`PUB;bd27017692354be0877227c3b822dcdd` v38)
**Periodo backtest:** 12-ago-2025 → 10-jun-2026 · **Fuente:** CSV autorizado `..._NYMEX_CL1!_2026-06-26_37133.csv` (103 trades cerrados)
**Multiplicador detectado:** CL $1000/pt · MCL $100/pt · **ATR proxy 15m:** ~0.28 $/bbl

> Config leída en vivo de la pestaña CL propia (estudio `5SUXJP`, layout `r7kGdNqY`). Métricas $ = contrato estándar; para **MCL** dividir entre 10; los % no cambian.

---

## 1. Señal / Carácter — contrarian en régimen de tendencia
- **Carácter:** **contrarian + Trend Catcher opuesto, filtrado por TENDENCIA** (mezcla del régimen de RTY con la lógica contrarian de GC/YM).
- **Constructor de condiciones REAL (`in_7`):**
  ```
  @long()  = {bullish_contrarian} AND {bearish_catcher} AND {trend_strength_trending} AND fecha ≥ ~2025-08-08
  @short() = {bearish_contrarian} AND {bullish_catcher} AND {trend_strength_trending} AND fecha ≥ ~2025-08-08
  @exit_all() = {contrarian_exits}
  ```
- **Filtro de régimen:** `trend_strength_trending` (como RTY). Salida por señal contrarian; sin SL/TP propio. Sin indicadores extra en el chart. Pine protegido.

### Diferencias CL vs resto
| | ES | NQ | YM | RTY | GC | **CL** |
|---|---|---|---|---|---|---|
| Lógica | fade rango | pullback | contrarian extremos | tendencia | contrarian+trend | **contrarian+catcher+trend** |
| Régimen | ranging | — | — | trending | — | **trending** |
| TF | 5m | 5m | 15m | 15m | 5m | **15m** |

## 2. Key Stats (recomputadas del CSV autorizado)
| Métrica | CL estándar | MCL (÷10) |
|---|---|---|
| Trades | 103 | 103 |
| Win% | 79.6% (82/21) | 79.6% |
| Profit Factor | 2.08 | 2.08 |
| Net PnL | $41,700 | $4,170 |
| Max DD | $10,420 | $1,042 |
| Expectancy | $405 (0.40 $/bbl) | $40.5 |
| Avg winner | $981 | $98 |
| Avg loser | −$1,843 | −$184 |

> **Sólido: Calmar ~4.0.** Alta WR (79.6%) con perfil sano. 103 trades en ~10 meses.

## 3. Riesgo — MAE de ganadores (proxy ATR 0.28 $/bbl, 15m)
| Estadístico | $/bbl | ×ATR |
|---|---|---|
| Media | 0.8 | 2.77× |
| Mediana | 0.3 | 1.02× |
| % > 1.5×ATR | — | 37% |

## 4. Análisis por ventana (resultado real)
| Ventana | n | WR% | PF | exp$ | net$ | maxDD$ |
|---|---|---|---|---|---|---|
| **24h** | 103 | 79.6 | **2.08** | +405 | **+41,700** | 10,420 |
| **RTH 09:30–15:45** | 33 | 69.7 | **0.74** | −188 | **−6,190** | 13,830 |
| AM 09:30–12:00 | 14 | 78.6 | 1.09 | +62 | +870 | 9,050 |
| PM 12:00–15:45 | 19 | 63.2 | 0.50 | −372 | −7,060 | 8,990 |

> **Como NQ/YM: el edge vive en 24h/overnight; RTH es negativo** (PF 0.74), peor en la tarde (PM PF 0.50). (Curioso: comparte el filtro "trending" con RTY pero, al revés que RTY, **no** funciona en RTH.)

## 5. Barrido de SL (TP 6×ATR; proxy 0.28)
**24h:**
| k×ATR | WR% | PF | exp$ | net$ | maxDD$ |
|---|---|---|---|---|---|
| **real (sin SL)** | 79.6 | **2.08** | **405** | **41,700** | 10,420 |
| 1.5 | 50.5 | 1.21 | 43 | 4,440 | 4,910 |
| **2.0** | 59.2 | **1.34** | **77** | 7,910 | 5,190 |
| 2.5 | 61.2 | 1.16 | 43 | 4,460 | 7,360 |
| 3.0 | 63.1 | 1.13 | 40 | 4,130 | 7,510 |
| 4.0 | 66.0 | 1.11 | 39 | 3,970 | 9,040 |

- **El nativo (sin stop) domina** (PF 2.08). Entre stops forzados, el óptimo es **k=2** (PF 1.34) — más ajustado que el k=4 de NQ/YM/RTY. **1.5×ATR de nuevo pobre.**
- RTH negativo casi en todos los k (mejor k=2: +$19, PF 1.08).

## 6. Señales últimos ~10 días del backtest (hora NY)
| Entrada (NY) | Dir | Precio | PnL$ std | MAE $/bbl | Sesión |
|---|---|---|---|---|---|
| 01-jun 08:45 | LONG | 89 | +1,780 | 0.0 | off |
| 02-jun 04:45 | LONG | 91 | +720 | 0.0 | off |
| 03-jun 09:30 | LONG | 95 | +1,020 | 0.1 | RTH |
| 07-jun 18:15 | SHORT | 93 | +360 | 0.3 | off |
| 08-jun 07:15 | LONG | 92 | −240 | 0.7 | off |
| 10-jun 18:00 | SHORT | 92 | +1,600 | 0.1 | off |

(El backtest termina 10-jun-2026.) **Alertas:** 0 a nivel cuenta; payload por gateway externo.

---

## 🎯 Recomendación CL
| Parámetro | Valor |
|---|---|
| Ventana | **24h** (RTH negativo) |
| sl_atr_multiplier | **nativo / 2.0×ATR si forzado** (CL prefiere stop más ajustado que el resto) |
| TP | 6×ATR (casi no actúa) |
| ¿Señal cruda basta? | **Sí (en 24h)** |
| QualityScorer/HMM | solo si se fuerza RTH |

**Conclusión:** CL funciona bien **en 24h con señal cruda** (PF 2.08, WR 79.6%, Calmar ~4). La salida nativa por señal es la mejor; si el bracket es obligatorio, **k=2×ATR** (su óptimo, más ajustado que los demás). **Como NQ/YM, no operar RTH** (PF 0.74). El 1.5×ATR vuelve a ser subóptimo.

> **Caveats:** TF 15m (ATR proxy 0.28 $/bbl, ventana de bajo volumen → aprox.); RTH = 33 trades. Dirección robusta, magnitudes aproximadas.

---
*Generado en sesión de calibración NTEXECG. Config leída en vivo de la pestaña CL; métricas del CSV autorizado `37133`.*
