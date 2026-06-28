# Anexo 21 — Evaluación de Filtros de Calidad (QualityScorer / HMM) en NQ, YM, GC

**Fecha:** 2026-06-28 · **Alcance:** NQ (5m), YM (15m), GC (5m) · **Estado:** análisis offline, **nada aplicado** (todo sigue en `paper`).

## 1. Qué se evaluó

Se reaplicaron las **funciones reales del pipeline** (`app/services/quality_scorer.py` y `hmm_service.classify_regime`) a cada trade del backtest LuxAlgo, reconstruyendo la ventana de barras HOLC en el instante de entrada. Objetivo: medir si **filtrar por score (Nivel 4) o por régimen** habría mejorado PF / neto / win% frente al baseline (todos los trades).

- **QualityScorer** = 4 subscores ponderados → 0-100: `volume_relative`, `atr_normalized`, `vwap_position`, `time_of_day`. Bloquea entries con score < `score_minimum`.
- **Régimen (HMM baseline)** = Kaufman Efficiency Ratio en 1h → `trending_bull/bear`, `ranging`, `unknown`. El gate bloquea si el régimen es conocido y no está en `allowed_regimes` (`unknown` = fail-open). **Nota:** el gate es *independiente de la dirección* del trade.

Script reproducible: `scripts/eval_quality_filters.py` (solo lectura; no toca DB ni config).

## 2. Validez de los datos (corrección crítica)

El primer pase dio un *sanity* de 4-26% (el precio de entrada casi nunca caía dentro de la barra alineada). El diagnóstico mostró que **el tiempo alinea exacto**, pero las series de precio difieren: el HOLC es continuo *back-ajustado* y el `NQ1!/YM1!/GC1!` de TradingView usa otro empalme de roll → un **offset de nivel casi constante** (~−282 pts NQ, ~−65 GC) que salta en cada roll trimestral.

Corrección aplicada: δ por roll = mediana de `(close_HOLC − precio)` en los ±5 trades vecinos. Tras corregir, el *sanity* sube a **88-91%**. Importante: `atr_normalized`, `volume_relative`, `time_of_day` y el régimen son **invariantes** a ese offset (usan rangos, volumen, hora o cocientes); solo `vwap_position` y el chequeo de sanity dependían del nivel.

## 3. Resultados por instrumento

### NQ (5m) — baseline n=65, win 83.1%, neto $28,660, PF 1.44
| Palanca | Resultado |
|---|---|
| **Score** `score_minimum=65` | neto $31,165 (**Δ +$2,505**), PF 1.44→**2.69**, conserva 40/65. Único umbral con Δneto positivo. |
| Régimen | counter-trend **gana** (90% win, PF 2.78). Bloquearlo **resta** −$11,555. |

→ El edge de NQ *es* contrarian; el régimen es contraproducente. La mejora por score es real pero **frágil** (solo un umbral) y casi sin ganancia de neto.

### YM (15m) — baseline n=48, win 89.6%, neto $22,690, PF 1.92
| Palanca | Resultado |
|---|---|
| Score | **Todos** los umbrales reducen neto (mejor Δ −$2,825). Win% ya altísimo: filtrar quita ganadores. |
| **Régimen** `allowed_regimes=["ranging"]` | conserva 28/48 (las de régimen *ranging*), **100% win**, neto **$30,965** (**Δ +$8,275**), elimina el cúmulo de 20 trades counter-trend que concentran las pérdidas (PF 0.66). |

→ Palanca clara para YM: **el gate de régimen**, no el score. (Funciona porque YM no tuvo trades alineados a tendencia en la muestra; el contrarian muere peleando una tendencia fuerte de 1h.)

### GC (5m) — baseline n=107, win 60.7%, neto $135,390, PF 1.95
| Palanca | Resultado |
|---|---|
| **Score** `score_minimum≈55-60` | 55→ neto $141,670 (Δ +$6,280); **60→ neto $158,040 (Δ +$22,650)**, PF 1.95→**2.94**, conserva 90/107. Región 55-60 robusta y positiva. |
| Régimen | counter-trend gana (PF 3.72); bloquearlo resta −$30,050. |

→ El composite **discrimina bien** en GC (Q1 48% win / avg −$438 vs Q4 71% / +$2,016). Driver principal: `volume_relative` (Q1 52%/−$273 vs Q4 64%/+$1,604) y `time_of_day`; `atr_normalized` es ligeramente inverso.

## 4. Recomendación (a validar en paper, **no** aplicar a live)

| Estrategia | Acción | Config sugerida (diseño) |
|---|---|---|
| **GC** | Activar **QualityScorer** | `score_minimum: 55`, 4 filtros activos peso igual (lo probado). A probar después: subir peso de `volume_relative`/`time_of_day` y quitar `atr_normalized`. |
| **YM** | Activar **gate de régimen** | `regime: {enabled, timeframe: "1h", allowed_regimes: ["ranging"]}` |
| **NQ** | **Sin cambios** | Edge contrarian; score frágil, régimen contraproducente. |

Snippets `pipeline_config_json` (diseño):

```jsonc
// GC
{ "score_minimum": 55,
  "filters": {
    "volume_relative": { "enabled": true, "weight": 25 },
    "atr_normalized":  { "enabled": true, "weight": 25 },
    "vwap_position":   { "enabled": true, "weight": 25 },
    "time_of_day":     { "enabled": true, "weight": 25 } } }

// YM
{ "regime": { "enabled": true, "timeframe": "1h", "allowed_regimes": ["ranging"] } }
```

## 5. Caveats (importantes)

- **Muestras pequeñas** (48-107 trades) y **óptimos in-sample**: elegir el mejor umbral es sobreajuste. GC 55-60 es robusto (umbrales vecinos positivos); NQ no (un solo umbral). Tratar todo como **hipótesis a validar en paper/shadow**, no como ajuste para live.
- El gate de régimen **no mira dirección**; `allowed_regimes=["ranging"]` en YM funciona porque no hubo trades alineados a tendencia. Si aparecieran, el gate también los bloquearía.
- `vwap_position` depende de la corrección de back-adjustment; es el subscore menos confiable offline.
- Salida nativa de LuxAlgo intacta; estos filtros solo **reducen** entries (nunca añaden ni cambian SL/TP).

## 6. Próximo paso sugerido

Activar las configs de GC e YM en **modo shadow/paper** sobre esas estrategias, dejar correr ≥4-6 semanas, y comparar entries bloqueadas vs ejecutadas con datos en vivo antes de cualquier cambio de estado operativo.
