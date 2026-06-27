# Reporte de calibración — ES (CME_MINI:ES1!)

**Instrumento:** E-mini S&P 500 (ES) · se opera en micro **MES** ($/pt ÷10)
**Timeframe:** 5 min · **Estrategia:** LuxAlgo® Backtester (S&O), Pine **protegido** (`PUB;bd27017692354be0877227c3b822dcdd` v38)
**Periodo backtest:** 15-mar-2026 → 25-jun-2026 · **Fuente:** CSV Lista de operaciones (119 trades cerrados + 1 abierta)
**Multiplicador:** ES $50/pt · MES $5/pt · **ATR proxy 5m:** ~7 pts (28 ticks)

> Métricas en dólares = contrato estándar ES. Para **MES** dividir $ entre 10; los % no cambian.

---

## 1. Señal / Repintado
- **Carácter:** contra-tendencia / **fade en rango puro**.
- **Constructor de condiciones (legible):**
  ```
  @long()  = {bullish_confirmation} AND {bearish_catcher} AND {trend_strength_ranging} AND fecha ≥ ~2026-03-04
  @short() = {bearish_confirmation} AND {bullish_catcher} AND {trend_strength_ranging} AND fecha ≥ ~2026-03-04
  @exit_all() = {confirmation_exits}
  ```
- **Filtro de régimen:** `trend_strength_ranging` → **solo opera cuando el mercado está "Ranging"**.
- **Confirmations:** modo Confirmation, "Any Bullish/Bearish", set "1234".
- **Ejecución:** en cierre de barra (entradas en límites de vela 5m). Pine protegido → no se puede *probar* ausencia de repintado; usar alerta **"Once Per Bar Close"**.
- **Salida:** por señal contraria (`Scripted Exit All`) o reversa; **sin SL/TP propio**.
- **EMAs:** 3 en el chart, **visuales** (no alimentan la señal).

## 2. Key Stats (backtest, contrato estándar)
| Métrica | ES estándar | MES (÷10) |
|---|---|---|
| Trades | 119 | 119 |
| Win% | 82.35% (98/21) | 82.35% |
| Profit Factor | 1.81 | 1.81 |
| Net PnL | $34,450 | $3,445 |
| Max DD | $11,750 | $1,175 |
| Expectancy | $289 (5.8 pt) | $28.9 |
| Avg winner | $784 (15.7 pt) | $78 |
| Avg loser | −$2,018 (40.4 pt) | −$202 |

- **Perfil:** alto win rate, ganancias pequeñas, pérdidas ocasionales grandes (avg loss 2.6× avg win).
- **Duración:** mediana ~70–90 min (~15 barras); media ~2.5–3.5 h (sesgada por holds nocturnos/fin de semana).
- **Señales/día:** ~1.7 (opera 24h Globex).

## 3. Riesgo — MAE de ganadores (medido del CSV)
| Estadístico | Puntos | Ticks | ×ATR(7) |
|---|---|---|---|
| Media | 18.7 | 75 | ~2.7× |
| Mediana | ~8–9 | ~33–36 | ~1.2× |
| % ganadores con MAE > 1.5×ATR | **~45%** | | |

> ~45% de los ganadores aguantan **más de 1.5×ATR** en contra antes de cerrar en verde. Un SL de 1.5×ATR cortaría ~la mitad de los ganadores.

## 4. Análisis por ventana (resultado real)
| Ventana | n | WR% | PF | exp$ | net$ | maxDD$ |
|---|---|---|---|---|---|---|
| **24h** | 119 | 82.4 | 1.81 | 289 | 34,450 | 11,750 |
| **RTH 09:20–15:45** | 43 (36%) | 86.0 | **2.03** | **391** | 16,800 | 10,162 |

> **El edge mejora en RTH**: WR 82→86%, PF 1.81→2.03, expectancy +35%. Operar solo la sesión es de mayor calidad por trade.

## 5. Barrido de SL (TP fijo 6×ATR; proxy 7 pt)
**RTH 09:20–15:45:**
| k×ATR | WR% | PF | exp$ | net$ | maxDD$ |
|---|---|---|---|---|---|
| real (sin SL) | 86.0 | **2.03** | 391 | 16,800 | 10,162 |
| 1.5 | 37.2 | 1.08 | 27 | 1,175 | 3,150 |
| 2.0 | 48.8 | 1.31 | 111 | 4,775 | 3,312 |
| **2.5** | 55.8 | **1.31** | **119** | 5,138 | **3,138** |
| 3.0 | 58.1 | 1.15 | 68 | 2,925 | 4,700 |
| 4.0 | 62.8 | 1.07 | 36 | 1,562 | 6,475 |

- El **TP 6×ATR casi nunca dispara** (2–4 hits).
- **1.5×ATR es el peor punto.** Óptimo entre stops fijos: **~2.5×ATR**.
- Riesgo-ajustado (net/maxDD): RTH nativo 1.65 ≈ k=2.5 1.64, pero k=2.5 con 1/3 del DD.

## 6. Alertas
- 0 alertas configuradas en TV; payload gestionado por el gateway externo. Para fidelidad: alerta sobre el Backtester, "Once Per Bar Close".

---

## ✅ Configuración final ES (cerrada)
**Señal cruda LuxAlgo en RTH 09:20–15:45 ET + SL 2.5×ATR + TP 6.0 bracket, sin QualityScorer ni HMM.**

**Justificación:** la señal cruda basta; el edge en RTH es superior al de 24h (PF 2.03); 2.5×ATR es el mejor stop fijo dentro del bracket obligatorio del gateway; el TP 6× rara vez actúa pero no daña.

> **Caveats:** ATR proxy fijo 7 pt (aprox.); RTH = 43 trades (muestra pequeña); un solo régimen (tramo alcista 2026). Ranking robusto, magnitudes aproximadas.

---
*Generado en sesión de calibración NTEXECG. Datos del CSV autorizado de la pestaña ES.*
