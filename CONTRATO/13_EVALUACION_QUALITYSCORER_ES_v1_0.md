# Anexo 13 — Evaluación del QualityScorer en ES (descartado) · v1.0

**Fecha:** 2026-06-25
**Ámbito:** Estrategia `ES5m` (LuxAlgo), filtros de calidad Nivel 4 (Fase 5).
**Estado:** Evaluado y **DESCARTADO**. QualityScorer se mantiene APAGADO en ES.

## 1. Motivo

Tercera y última de las tres capas analíticas opcionales (tras SL por ATR y HMM).
Se evalúa si el QualityScorer (volume_relative, atr_normalized, vwap_position,
time_of_day) aporta valor sobre la señal cruda de ES.

## 2. Método

- 120 operaciones reales de ES (15-mar a 25-jun 2026) del backtester de LuxAlgo.
- Sub-scores calculados con las **funciones de producción** del QualityScorer sobre
  barras 5m de `ohlcv_bars` (387,766 barras) **anteriores o iguales a cada entrada**
  → sin lookahead por construcción.
- Análisis: discriminación (sub-score bajo <0.5 vs alto ≥0.5), delta de net al
  bloquear bajo umbral con particiones IN/OUT-of-sample (70/30) y **RTH**, barrido de
  umbral (0.4/0.5/0.6) e inspección de los trades bloqueados.

## 3. Discriminación (bajo <0.5 vs alto ≥0.5)

| filtro | lado | n | WR% | PF | net$ |
|---|---|---|---|---|---|
| volume_relative | bajo | 53 | 84.9 | 1.71 | 15,475 |
| volume_relative | alto | 67 | 79.1 | 1.92 | 18,950 |
| atr_normalized | bajo | 12 | 66.7 | 0.67 | −4,125 |
| atr_normalized | alto | 108 | 83.3 | 2.29 | 38,550 |
| vwap_position | bajo | 63 | 87.3 | 2.90 | 26,525 |
| vwap_position | alto | 57 | 75.4 | 1.28 | 7,900 |
| time_of_day | bajo | 5 | 100 | inf | 4,262 |
| time_of_day | alto | 115 | 80.9 | 1.71 | 30,162 |

## 4. Deep-dive de atr_normalized (el único candidato del primer pase)

Barrido de umbral (delta = mejora de net al bloquear < umbral):

| umbral | TODOS | IN-SAMPLE | OUT-SAMPLE | **RTH** |
|---|---|---|---|---|
| 0.4 | +4,425 | −275 | +4,700 | **−2,562** |
| 0.5 | +4,125 | +1,500 | +2,625 | **−4,800** |
| 0.6 | +1,700 | −38 | +1,738 | **−6,075** |

Trades bloqueados a 0.5: 12, suma P&L −4,125. De los 6 que caen en **RTH**, son net
**+$4,799** (ganadores, incluidos +2,075 y +2,875). El peor loser del histórico,
#108 (−$10,162), tiene score ≥0.5 → **NO lo bloquea**.

## 5. Hallazgos

1. **volume_relative**: no discrimina (PF 1.71 vs 1.92). Bloquear destruye valor.
2. **vwap_position**: discrimina **al revés** — los de bajo score (contra-VWAP)
   rinden mejor (PF 2.90 vs 1.28). El filtro premia la alineación con VWAP, pero la
   estrategia es contra-tendencia → la alineación es anti-señal. (Tercera confirmación
   independiente del carácter mean-reversion, junto al Anexo 12 y al MAE del Anexo 11.)
3. **time_of_day**: solo 5 trades en "bajo" → muestra insuficiente, sin conclusión.
4. **atr_normalized**: el "beneficio" del primer pase era un **espejismo**: en RTH
   (lo que se opera) el delta es **negativo en los tres umbrales** (bloquea ganadores);
   el beneficio 24h venía de bloquear trades **overnight** que la ventana RTH ya
   excluye; no es robusto al umbral (solo positivo in-sample en 0.5); y no caza el
   peor outlier (#108).

## 6. Decisión

- **QualityScorer: APAGADO en ES.** Los cuatro filtros descartados con evidencia.
- Junto con el Anexo 12 (HMM descartado), se concluye: **para ES, ninguna capa
  analítica opcional de Nivel 4 (QualityScorer ni HMM) aporta valor.** El edge está
  en la señal cruda; el rol de NTEXECG para ES es SL + riesgo + seguridad + auditoría.

## 7. Config validada de ES (cierre del expediente)

Señal cruda LuxAlgo (RTH) · SL 2.5×ATR · TP 6.0 (bracket) · guardarraíles
symbol+timeframe · sin QualityScorer · sin HMM.

## 8. Caveats

- Muestra modesta (120 trades, 3 meses) y una sola estrategia/instrumento. La
  conclusión es "sin valor para ES", no "los filtros son inútiles en general":
  otras estrategias (p. ej. de tendencia) podrían beneficiarse y deben evaluarse
  por separado con el mismo método.
- Lección de método: el primer pase (24h, umbral único 0.5) mostró +$4,125 y se veía
  prometedor; la lente RTH + barrido + inspección trade a trade lo descartó. Validar
  siempre sobre el subconjunto que realmente se opera y con robustez de umbral.
