# Reporte de calibración — NQ (CME_MINI:NQ1!)

**Instrumento:** E-mini Nasdaq-100 (NQ) · se opera en micro **MNQ** ($/pt ÷10)
**Timeframe:** 5 min · **Estrategia:** LuxAlgo® Backtester (S&O), Pine **protegido** (`PUB;bd27017692354be0877227c3b822dcdd` v38)
**Periodo backtest:** 16-mar-2026 → 26-jun-2026 · **Fuente:** CSV autorizado `..._CME_MINI_NQ1!_2026-06-26_f2843.csv` (64 trades cerrados)
**Multiplicador detectado:** NQ $20/pt · MNQ $2/pt · **ATR proxy 5m:** ~86 pts (344 ticks)

> Config leída en vivo de la pestaña NQ propia (estudio `HWRLJj`, layout `r7kGdNqY`). Métricas $ = contrato estándar; para **MNQ** dividir entre 10; los % no cambian.

---

## 1. Señal / Carácter — ⚠️ DISTINTO a ES
- **Carácter:** **pullback / continuación de tendencia** (NO fade en rango como ES).
- **Constructor de condiciones REAL de NQ (`in_7`):**
  ```
  @long()  = {bullish_confirmation_any} AND {bullish_smart_trail} AND {bearish_catcher} AND fecha ≥ ~2026-03-04
  @short() = {bearish_confirmation_any} AND {bearish_smart_trail} AND {bullish_catcher} AND fecha ≥ ~2026-03-04
  @exit_all() = {confirmation_exits}
  ```
- Largo = **Smart Trail a favor (tendencia media alcista)** + **Trend Catcher en contra (retroceso corto)** + confirmación → compra el retroceso en tendencia.
- **Sin filtro de régimen** (`trend_strength_ranging` ausente) → opera también en tendencia.
- **Salida:** por señal contraria; sin SL/TP propio.
- **EMAs:** **ninguna** en la pestaña NQ.
- Pine protegido; alerta recomendada "Once Per Bar Close".

### Diferencias NQ vs ES
| Componente | ES | NQ |
|---|---|---|
| Confirmation | `bullish_confirmation` (Any, "1234") | `bullish_confirmation_any` (Any, "1234") |
| Filtro Smart Trail | ❌ ninguno | ✅ **requerido** |
| Trend Catcher | `bearish_catcher` | `bearish_catcher` (igual) |
| Filtro de régimen | ✅ **ranging only** | ❌ ninguno |
| EMAs en chart | 3 (visuales) | 0 |
| Salida | confirmation_exits | confirmation_exits |

## 2. Key Stats (recomputadas del CSV autorizado)
| Métrica | NQ estándar | MNQ (÷10) |
|---|---|---|
| Trades | 64 | 64 |
| Win% | 84.4% (54/10) | 84.4% |
| Profit Factor | 1.54 | 1.54 |
| Net PnL | $33,030 | $3,303 |
| Max DD | $35,885 | $3,588 |
| Expectancy | $516 (25.8 pt) | $51.6 |
| Avg winner | $1,741 | $174 |
| Avg loser | −$6,100 | −$610 |

> **Mucho más frágil que ES:** Max DD ($35.9k) ≈ Net ($33k) → Calmar ~0.9 (ES ~2.9). Menos señales (64 vs 119). Perdedores brutales (−$6,100 medio, 3× los de ES).

## 3. Riesgo — MAE de ganadores (proxy ATR 86 pt)
| Estadístico | Puntos | ×ATR |
|---|---|---|
| Media | 106.8 | 1.24× |
| Mediana | 79.9 | 0.93× |
| % ganadores con MAE > 1.5×ATR | **31%** | |

> Los ganadores de NQ aguantan **menos calor relativo** que ES (ES: 45%, media 2.7×) → un stop más holgado no corta tantos ganadores.

## 4. Análisis por ventana — 🔑 hallazgo crítico
| Ventana | n | WR% | PF | exp$ | net$ | maxDD$ |
|---|---|---|---|---|---|---|
| **24h** | 64 | 84.4 | **1.54** | +516 | **+33,030** | 35,885 |
| **RTH 09:30–15:45** | 17 | 70.6 | **0.47** | −1,288 | **−21,900** | 38,275 |
| AM 09:30–12:00 | 6 | 100.0 | ∞ | +1,837 | +11,020 | 0 |
| **PM 12:00–15:45** | 11 | 54.5 | **0.21** | −2,993 | **−32,920** | 38,275 |

> **La ventana RTH de ES DESTROZA NQ** (PF 0.47, −$21.9k). Todo el daño está en la **TARDE (12:00–15:45): PF 0.21**. La mañana (6/6) es impecable pero muestra diminuta. El grueso del +$33k viene de **overnight/Globex**.

## 5. Barrido de SL (TP 6×ATR; proxy 86 pt)
**24h:**
| k×ATR | WR% | PF | exp$ | net$ | maxDD$ |
|---|---|---|---|---|---|
| real | 84.4 | 1.54 | 516 | 33,030 | 35,885 |
| 1.5 | 57.8 | 0.88 | −127 | −8,155 | 43,010 |
| 2.0 | 64.1 | 0.91 | −104 | −6,665 | 43,415 |
| 2.5 | 71.9 | 1.13 | 132 | 8,425 | 34,625 |
| 3.0 | 75.0 | 1.23 | 231 | 14,795 | 32,905 |
| **4.0** | 82.8 | **2.01** | **727** | **46,500** | **20,945** |

- **TP 6×ATR (516 pt) NUNCA dispara** (0 hits).
- En NQ-24h **un stop ANCHO mejora** (k=4 → PF 2.01, net +$46.5k, DD a la mitad). **1.5×ATR es lo peor.** Opuesto a ES.
- **RTH negativo en todos los k** → ningún stop rescata la tarde.

## 6. Señales últimos ~10 días (hora NY)
| Entrada (NY) | Dir | Precio | PnL$ std | MAE pt | Sesión |
|---|---|---|---|---|---|
| 17-jun 12:55 | SHORT | 30399.75 | −2,110 | 120 | RTH |
| 17-jun 14:45 | LONG | 30505.25 | −3,235 | 582 | RTH |
| 18-jun 00:20 | SHORT | 30343.50 | −7,885 | 440 | off |
| 22-jun 06:05 | LONG | 30737.75 | +865 | 35 | off |
| 23-jun 13:55 | SHORT | 29756.75 | +3,035 | 26 | RTH |
| 23-jun 18:25 | SHORT | 29711.00 | +685 | 181 | off |
| 26-jun 02:10 | SHORT | 29293.50 | −2,750 | 269 | off |
| 26-jun 05:50 | LONG | 29431.00 | +5,315 | 250 | off |

**Alertas:** 0 a nivel cuenta; payload gestionado por gateway externo.

---

## 🎯 Recomendación NQ vs ES
| Parámetro | ES (cerrado) | **NQ (recomendado)** |
|---|---|---|
| Ventana | RTH 09:20–15:45 ✅ | **NO RTH** — 24h, o solo mañana 09:30–12:00 |
| sl_atr_multiplier | 2.5×ATR | **4.0×ATR** (mín.) |
| TP | 6×ATR | 6×ATR (irrelevante, 0 hits) |
| ¿Señal cruda basta? | **Sí** | **NO** |
| QualityScorer / HMM | innecesario | **Evaluar — recomendado** |

**Conclusión:** la plantilla de ES **no transfiere a NQ**. Caminos, en orden:
1. **NQ 24h con SL 4×ATR** (no RTH): único setup con PnL/DD decente (PF 2.01, Calmar ~2.2).
2. **Activar QualityScorer/HMM** para filtrar los trades de tarde que matan el RTH (NQ sí los necesita; ES no).
3. **Morning-only 09:30–12:00** prometedor (6/6) pero muestra insuficiente — validar con más histórico.

> **NO** replicar ciegamente "RTH + SL 2.5×ATR" de ES en NQ → perdería dinero (PF 0.47).
>
> **Caveats:** ATR proxy fijo 86 pt (aprox.); RTH = 17 trades; k de NQ dependen de ~10 perdedores. Dirección robusta, magnitudes aproximadas.

---
*Generado en sesión de calibración NTEXECG. Config leída en vivo de la pestaña NQ; métricas del CSV autorizado `f2843`.*
