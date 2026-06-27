# Reporte de calibración — GC (COMEX:GC1!)

**Instrumento:** Gold Futures (GC) · se opera en micro **MGC** ($/pt ÷10)
**Timeframe:** **5 min** · **Estrategia:** LuxAlgo® Backtester (S&O), Pine **protegido** (`PUB;bd27017692354be0877227c3b822dcdd` v38)
**Periodo backtest:** 16-mar-2026 → 25-jun-2026 · **Fuente:** CSV autorizado `..._COMEX_GC1!_2026-06-26_ae6f0.csv` (106 trades cerrados)
**Multiplicador detectado:** GC $100/pt · MGC $10/pt · **ATR proxy 5m:** ~4.0 pts ⚠️ *(ver caveat)*

> Config leída en vivo de la pestaña GC propia (estudios `Xmdmx3` + `oiTkL1`, layout `r7kGdNqY`). Métricas $ = contrato estándar; para **MGC** dividir entre 10; los % no cambian.

> ⚠️ **CAVEAT ATR (importante en GC):** el ATR proxy de 4.0 pts se midió en una ventana nocturna de bajo volumen y **subestima** la volatilidad activa del oro. Esto **infla los múltiplos ×ATR del MAE y sesga el barrido de SL** (umbrales k×ATR demasiado pequeños → más trades "detenidos", TP 6×ATR demasiado fácil). **El MAE en PUNTOS es fiable; los ×ATR y el barrido de SL de GC deben recalcularse con ATR(14) real antes de fijar el SL.**

---

## 1. Señal / Carácter — contrarian con filtro de tendencia
- **Carácter:** **reversión/contrarian (OscMatrix) filtrado por tendencia (Smart Trail) + Confluence.** Perfil de **tendencia/swing** (WR baja, ganadores grandes).
- **Constructor de condiciones REAL (`in_7`):**
  ```
  @long()  = {bearish_contrarian} AND {bullish_smart_trail} AND {weak_bullish_confluence} AND fecha ≥ ~2026-03-03
  @short() = {bullish_contrarian} AND {bearish_smart_trail} AND {weak_bearish_confluence} AND fecha ≥ ~2026-03-03
  @exit_all() = {contrarian_exits}
  ```
- Largo = contrarian bajista (agotamiento de venta) + Smart Trail alcista + Confluence débil alcista. `in_10 = "oiTkL1$0"` confirma OscMatrix como fuente. Salida por señal contrarian; sin SL/TP propio. Pine protegido.

### Diferencias GC vs resto
| | ES | NQ | YM | RTY | **GC** |
|---|---|---|---|---|---|
| Lógica | fade rango | pullback | contrarian extremos | tendencia | **contrarian+SmartTrail+confluence** |
| Indicador | Confirmation | Conf+SmartTrail | OscMatrix | Conf+Neo | **OscMatrix** |
| TF | 5m | 5m | 15m | 15m | **5m** |
| Win rate | 82% | 84% | 90% | 87% | **60%** |
| Perfil | alta WR | alta WR | alta WR | alta WR | **baja WR / skew +** |

## 2. Key Stats (recomputadas del CSV autorizado)
| Métrica | GC estándar | MGC (÷10) |
|---|---|---|
| Trades | 106 | 106 |
| Win% | 60.4% (64/42) | 60.4% |
| Profit Factor | 1.91 | 1.91 |
| Net PnL | $128,970 | $12,897 |
| Max DD | $38,690 | $3,869 |
| Expectancy | $1,217 (12.2 pt) | $121.7 |
| Avg winner | $4,237 | $424 |
| Avg loser | −$3,386 | −$339 |

> **Perfil único:** WR baja (60%) pero **avg winner > avg loser** (skew positivo, deja correr ganadores). Net enorme ($129k std) por el gran movimiento del oro y $100/pt. **Alto riesgo de cola:** MaxDD $38.7k; un solo trade reciente perdió **−$15,470**. Calmar ~3.3.

## 3. Riesgo — MAE de ganadores
| Estadístico | Puntos (fiable) | ×ATR(4) ⚠️ infla |
|---|---|---|
| Media | 18.3 | 4.57× |
| Mediana | 8.8 | 2.19× |
| % > 1.5×ATR | — | 73% |

> Los ×ATR están inflados por el ATR proxy bajo. **Usar el MAE en puntos (media 18.3, mediana 8.8) hasta recalcular con ATR real.**

## 4. Análisis por ventana (resultado real)
| Ventana | n | WR% | PF | exp$ | net$ | maxDD$ |
|---|---|---|---|---|---|---|
| **24h** | 106 | 60.4 | **1.91** | 1,217 | **128,970** | 38,690 |
| RTH 09:30–15:45 | 25 | 60.0 | 1.57 | 1,014 | 25,350 | 23,910 |
| AM 09:30–12:00 | 14 | 50.0 | 1.27 | 747 | 10,460 | 21,570 |
| **PM 12:00–15:45** | 11 | 72.7 | **3.29** | 1,354 | 14,890 | 3,150 |

> A diferencia de NQ/YM, **GC en RTH es positivo** (PF 1.57), y la **tarde (PM) es la mejor franja** (PF 3.29, maxDD bajo). 24h sigue ganando por net absoluto.

## 5. Barrido de SL ⚠️ (sesgado por ATR proxy — recalcular con ATR real)
**24h** (ATR=4, TP6x=24pt):
| k×ATR | WR% | PF | exp$ | net$ | maxDD$ |
|---|---|---|---|---|---|
| **real (sin SL)** | 60.4 | **1.91** | **1,217** | **128,970** | 38,690 |
| 1.5 | 16.0 | 0.69 | −156 | −16,560 | 19,500 |
| 2.0 | 27.4 | 0.97 | −18 | −1,910 | 12,810 |
| 2.5 | 34.0 | 1.08 | 51 | 5,360 | 13,410 |
| **3.0** | 38.7 | **1.12** | **82** | 8,710 | 16,730 |
| 4.0 | 43.4 | 1.04 | 34 | 3,620 | 17,130 |

- **El nativo (sin stop) domina por enorme margen** (PF 1.91 vs ≤1.12). Entre stops, **k=3** el menos malo; **1.5×ATR catastrófico** (−$16.5k).
- El TP 6×ATR dispara mucho aquí **por el ATR subestimado** (24pt demasiado fácil para el oro) → no fiable. **Recalcular con ATR real.**

## 6. Señales últimos ~10 días (hora NY) — incluye la cola
| Entrada (NY) | Dir | Precio | PnL$ std | MAE pt | Sesión |
|---|---|---|---|---|---|
| 16-jun 03:05 | LONG | 4348 | +1,130 | 3 | off |
| 17-jun 04:45 | SHORT | 4339 | +4,760 | 64 | off |
| 18-jun 10:05 | SHORT | 4266 | +9,320 | 9 | RTH |
| 21-jun 19:40 | SHORT | 4161 | −5,770 | 77 | off |
| 22-jun 19:55 | SHORT | 4205 | +6,030 | 11 | off |
| **23-jun 09:45** | **LONG** | **4145** | **−15,470** | **169** | RTH |
| 25-jun 04:05 | LONG | 4007 | +2,580 | 18 | off |

(17 señales en 10 días — frecuencia alta. Tabla abreviada.) **Alertas:** 0 a nivel cuenta; payload por gateway externo.

---

## 🎯 Recomendación GC (preliminar — pendiente recálculo de ATR)
| Parámetro | Valor |
|---|---|
| Ventana | **24h** (mejor net) o **RTH con foco PM 12:00–15:45** (PF 3.29) |
| sl_atr_multiplier | **nativo / ≥3×ATR si forzado — CONFIRMAR con ATR(14) real** |
| TP | 6×ATR (recalcular; el proxy lo distorsiona) |
| ¿Señal cruda basta? | Sí en net, pero **alto riesgo de cola** |
| QualityScorer/HMM | **Evaluar** — filtraría trades tipo −$15k que disparan el DD |

**Conclusión:** GC es un **trend/swing de baja WR y ganancias grandes** con net espectacular ($129k std / $12.9k MGC) pero **drawdown y cola elevados**. La salida nativa por señal es muy superior; **1.5×ATR es inviable**. **Antes de fijar el SL, recalcular el barrido con ATR(14) real** (el proxy nocturno lo sesga). Por el riesgo de cola, **GC es buen candidato para QualityScorer/HMM**.

> **Caveats:** ATR proxy 4 pts subestimado → ×ATR y barrido SL aproximados (recalcular). Win rate baja amplifica el peso de pocos trades grandes.

---
*Generado en sesión de calibración NTEXECG. Config leída en vivo de la pestaña GC; métricas del CSV autorizado `ae6f0`.*
