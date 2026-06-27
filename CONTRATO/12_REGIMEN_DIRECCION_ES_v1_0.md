# Anexo 12 — Régimen × dirección de la estrategia ES (hallazgo) · v1.0

**Fecha:** 2026-06-25
**Ámbito:** Estrategia `ES5m` (LuxAlgo), análisis del filtro de régimen (Fase 6, HMM).
**Estado:** Hallazgo registrado. **HMM gate sigue APAGADO**; pendiente validación point-in-time.

## 1. Motivo

NTEXECG se diseñó con tres elementos analíticos: SL por ATR (Nivel 5), análisis
técnico de la señal (QualityScorer, Nivel 4) y filtro de régimen HMM (Nivel 4).
Este anexo evalúa, con datos, si el **gate de régimen** aporta valor a ES, y
responde la pregunta abierta "¿rinde distinto en tendencia vs rango?".

## 2. Fuente de datos y método

- 120 operaciones reales de ES exportadas del backtester de LuxAlgo (15-mar a
  25-jun 2026), con hora de entrada, dirección y P&L.
- Para cada trade, el **HMM real de NTEXECG** (modelo `hmm_ES_1h`) etiqueta el
  régimen usando barras 1h de `ohlcv_bars` (≈32k barras) **anteriores o iguales
  a la hora de entrada**.
- Regímenes: `ranging`, `trending_bull`, `trending_bear`. Agregación por régimen,
  por régimen+dirección, y subconjunto RTH (09:20–15:45 NY).

## 3. Resultados

### 3.1 Por régimen (24h)

| Régimen | n | WR% | PF | exp$ | net$ |
|---|---|---|---|---|---|
| ranging | 51 | 84.3 | 3.06 | 373 | 19,012 |
| trending_bull | 38 | 81.6 | 1.61 | 260 | 9,875 |
| trending_bear | 31 | 77.4 | 1.33 | 179 | 5,538 |

Los tres regímenes son net-positivos; el mejor es **rango** (PF 3.06).

### 3.2 Por régimen + dirección (24h) — la tabla clave

| Bucket | n | WR% | PF | net$ |
|---|---|---|---|---|
| ranging/long | 29 | 89.7 | 5.55 | +13,925 |
| ranging/short | 22 | 77.3 | 1.83 | +5,088 |
| trending_bear/long (contra-tendencia) | 16 | 81.2 | 37.93 | +13,388 |
| trending_bull/short (contra-tendencia) | 20 | 85.0 | 3.27 | +11,938 |
| trending_bull/long (a favor) | 18 | 77.8 | 0.81 | **−2,062** |
| trending_bear/short (a favor) | 15 | 73.3 | 0.53 | **−7,850** |

### 3.3 RTH (09:20–15:45)

| Régimen | n | WR% | PF | net$ |
|---|---|---|---|---|
| trending_bull | 26 | 84.6 | 3.64 | 13,375 |
| trending_bear | 18 | 83.3 | 1.30 | 3,400 |

(En RTH no hubo trades clasificados como `ranging`; la sesión de día tiende a tendencia.)

## 4. Hallazgo

**La estrategia es de reversión a la media / contra-tendencia.** Gana cuando opera
EN CONTRA del régimen (long en bajista, short en alcista, y en rango) y **pierde
cuando opera A FAVOR** de la tendencia. Los únicos dos buckets perdedores son
exactamente los "a favor de tendencia" (`trending_bull/long` y `trending_bear/short`),
juntos −$9,912. Esto explica el perfil de losers grandes (−40 pts): son las entradas
a favor de tendencia que la tendencia arrolla.

## 5. Implicación para el gate

1. **El gate de régimen actual (permitir/bloquear regímenes enteros) NO sirve para
   ES**: los tres regímenes son net-positivos, así que bloquear cualquiera quita
   ganancia. El edge no es "evita el régimen X".
2. **Lo que aportaría es un filtro DIRECCIONAL**: bloquear las entradas alineadas
   con la tendencia detectada (long en `trending_bull`, short en `trending_bear`).
   En este backtest habría cortado 33 trades perdedores y subido el net de ~$34k a
   ~$44k (+29%). Eso NO está implementado hoy (el gate es ciego a la dirección);
   sería una mejora nueva.

## 6. Decisión

- **HMM gate: se mantiene APAGADO** en ES (el gate simple no ayuda).
- El hallazgo "estrategia contra-tendencia; mayor fuga = operar a favor" queda
  registrado como **hipótesis a validar**, no como cambio de config.

## 7. Caveats

- Muestras pequeñas por celda (15–29 trades). El PF 37.93 de `trending_bear/long`
  es de pocos trades → inestable, no tomar literal.
- **Sesgo de hindsight**: el régimen se etiquetó con el modelo HMM actual,
  entrenado sobre todo el histórico (incluido el futuro de cada trade). Una prueba
  justa requiere régimen **point-in-time** (modelo entrenado solo con el pasado).
- 6 celdas → riesgo de sobreajuste.

## 8. Próximo paso

Validar la hipótesis "bloquear entradas a favor de tendencia" con:
- régimen **point-in-time** (re-etiquetar cada trade con un modelo que solo vea su pasado),
- partición **out-of-sample** (entrenar en un periodo, validar en otro),
antes de codificar un filtro direccional en el Nivel 4.

## 9. Validación (RECHAZADA) — prueba sin lookahead + out-of-sample

Se re-etiquetó cada trade con el clasificador **baseline** (Kaufman efficiency
ratio), que solo usa barras anteriores a la entrada → **cero hindsight**, y se
partió 70% in-sample / 30% out-of-sample.

**Régimen + dirección (baseline, sin lookahead):**

| bucket | n | WR% | PF | net$ |
|---|---|---|---|---|
| ranging/long | 48 | 85.4 | 2.60 | 18,938 |
| ranging/short | 43 | 79.1 | 1.24 | 5,738 |
| trending_bear/long | 3 | 100 | inf | 3,988 |
| trending_bear/short | 4 | 75.0 | 0.76 | −588 |
| trending_bull/long | 12 | 75.0 | 1.93 | **+2,325** |
| trending_bull/short | 10 | 80.0 | 3.98 | 4,025 |

**Hipótesis "bloquear entradas a favor de tendencia":**

| split | n | n_blq | net_all | net_filt | delta |
|---|---|---|---|---|---|
| TODOS | 120 | 16 | 34,425 | 32,688 | −1,738 |
| IN-SAMPLE | 84 | 14 | 36,950 | 32,338 | −4,612 |
| OUT-SAMPLE | 36 | 2 | −2,525 | 350 | +2,875 |

**Resultado:** el patrón "a favor pierde" NO se replica sin lookahead
(`trending_bull/long` pasó de −2,062 con el HMM a **+2,325** con el baseline). Los
trades a-favor-de-tendencia son **net +$1,737** en conjunto → bloquearlos destruye
valor (delta negativo en TODOS e IN-SAMPLE). El único delta positivo (OOS) proviene
de bloquear apenas **2 trades** = ruido estadístico.

**Conclusión:** hipótesis **RECHAZADA**. El hallazgo de la sección 4 fue un
artefacto del etiquetado HMM con lookahead (el modelo se entrenó sobre todo el
histórico, incluido el futuro de cada trade). No se implementa filtro direccional;
el HMM gate se mantiene APAGADO. Ejemplo de por qué validar point-in-time + OOS
antes de codificar cualquier compuerta.
