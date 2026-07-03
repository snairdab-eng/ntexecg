# Anexo 25 — Batería de pruebas de filtros (todas las estrategias)

**Objetivo:** definir, para cada filtro/gate que NTEXECG aplica hoy, la prueba a
correr sobre **cada estrategia**, con parámetros a barrer, métricas y criterio de
aceptación. Es una lista de trabajo: se ejecuta re-corriendo los backtests y se
va llenando la matriz de seguimiento del final.

**Estado del disparador:** hoy la mayoría de estrategias corren **sin filtros de
calidad ni gate de régimen** (score = 100 por defecto). Esta batería es el paso
previo a activarlos con umbrales justificados por datos (no a ojo).

---

## 1. Estrategias en alcance (7)

| # | strategy_id | Activo | TF | Notas |
|---|---|---|---|---|
| 1 | 6E5m_ConfStrong_NC_WeakConf | M6E | 5m | |
| 2 | 6J5m_ConfNormal_TSR_MF50 | MJY | 5m | |
| 3 | ES5m_ConfNormal_TC_TSR | MES | 5m | comparte símbolo con #4 |
| 4 | ES5m_ConfStrong_TSR_WeakConf | MES | 5m | comparte símbolo con #3 |
| 5 | GC5m_ContraNormal_ST_WeakConf | MGC | 5m | |
| 6 | NQ5m_ConfAny_ST_TC | MNQ | 5m | "ConfAny" = sin filtro de confluencia |
| 7 | RTY15m_ConfNormal_NC_TST | M2K | 15m | |

---

## 1-bis. ESTÁNDAR FIJO — calidad de señal (score ≠ calidad)

**Regla (obligatoria):** un `score = 100` **solo** es confiable si proviene de
**filtros reales activos**. Si la estrategia no tiene filtros, `score = 100` debe
tratarse como **UNKNOWN QUALITY**, nunca como HIGH QUALITY.

**Taxonomía de calidad:**

| Condición | Etiqueta | Significado |
|---|---|---|
| `filters_active = false` | **UNKNOWN** | Pase por defecto. La señal solo pasó los gates estructurales (sistema, ventana, riesgo, SL). La calidad **no se midió**. |
| `filters_active = true` y `score ≥ umbral_alto` | **HIGH** | Calidad medida y alta. |
| `filters_active = true` y `score_minimum ≤ score < umbral_alto` | **MEDIUM** | Calidad medida, media. |
| `filters_active = true` y `score < score_minimum` | **LOW** | Se **bloquea** en N4. |

donde `filters_active = (hay filters configurados) OR (regime.enabled = true)`.

**Implicaciones operativas:**
- **UI / dashboards / detalle de señal:** mostrar **UNKNOWN** de forma explícita
  (p. ej. gris/amarillo), **no** el ✅ verde de calidad. `score=100` sin filtros no
  lleva check de calidad.
- **Payload / trazabilidad:** incluir `ntexecg_quality ∈ {UNKNOWN, LOW, MEDIUM, HIGH}`
  junto al `ntexecg_score`, y `filters_active` en la traza del pipeline (`level_4`).
- **Promoción a capital real:** requerir calidad **EVALUATED** (no UNKNOWN) como
  pre-requisito. Una estrategia sin filtros no debe pasar a real solo porque "da 100".
- El score se sigue usando para el gate (`score ≥ score_minimum`), pero **la etiqueta
  de calidad es independiente** y refleja si hubo medición real.

**Pendiente de implementación** (código): marcar `quality`/`filters_active` en
`FilterPipeline.level_4`, propagarlo al `payload.extras` y mostrarlo en la cinta de
filtros del detalle de señal. Hasta entonces, interpretar todo `score=100` de
estrategias sin filtros como **UNKNOWN**.

---

## 2. Inventario de filtros/gates hoy (qué se puede calibrar)

| Nivel | Filtro / parámetro | Config (clave) | Default |
|---|---|---|---|
| L2 | Ventana de sesión | `session_config_json.windows`, `avoid_open_minutes`, `avoid_close_minutes` | por activo |
| L2 | Frescura (staleness) | `signal_max_age_entry_seconds` / `_exit_seconds` | off |
| L3 | Guardarraíles | `max_open_positions`, `max_open_positions_symbol`, `daily_loss_stop`, `max_trades_day`, `allow_reversal` | laxos |
| L4 | **QualityScorer** (4 subscores) | `filters.{volume_relative, atr_normalized, vwap_position, time_of_day}` + `score_minimum` | ∅ → score 100 |
| L4 | **Régimen (HMM)** | `regime.{enabled, timeframe, allowed_regimes}` | off |
| L5 | SL por ATR | `sl_atr_multiplier`, `atr_period`, `atr_timeframe` | 1.5 / 14 / 5m |
| L5 | TP por ATR | `tp_atr_multiplier` | off (Builtin-Exits) |
| — | Entrada escalonada | `scale_entry.{levels, quantities, max_micro_contracts, mode}` | por estrategia |
| TP* | Cancel entry after (TradersPost) | del lado TradersPost | 3600 s (interino) |

\* No es de NTEXECG pero se parametriza por estrategia con `pullback_timing`.

---

## 3. Métricas comunes (todas las pruebas)

Para cada configuración probada, reportar sobre el mismo set de trades:

- **WR** (win rate), **PF** (profit factor), **Expectancy** (por trade).
- **# trades** (potencia estadística — descartar configs con muestra insuficiente).
- **Max Drawdown** y **cola**: p95 / p99 del MAE (máxima excursión adversa).
- **% señales filtradas** por el filtro (cuánto recorta).
- **Lift** vs baseline: ΔPF, ΔWR, ΔExpectancy, Δcola.
- **Decorrelación** (entre estrategias): correlación de señales (<30% solape) y de P&L (<0.3).

**Regla de oro de aceptación:** mantener un filtro/umbral **solo si** sube PF/expectancy
o recorta cola de forma **material y estable**, sin dejar la muestra por debajo del
mínimo (sugerido ≥ 100 trades post-filtro), y **validado out-of-sample** (partición
temporal o walk-forward) para evitar sobreajuste.

---

## 4. Pruebas por filtro

### 4.1 QualityScorer — subscores (núcleo)
Herramienta: `eval_quality_filters.py` (offline) → luego `apply_quality_filter.py`.

- **QS-0 Baseline:** sin filtros (score 100). Fija las métricas base por estrategia.
- **QS-1 por subscore aislado:** activar **solo uno** de {volume_relative, atr_normalized, vwap_position, time_of_day} y barrer `score_minimum` ∈ {50, 60, 70, 80}. Medir lift y % filtrado.
- **QS-2 combinaciones:** mejores 2–3 subscores juntos (pesos iguales vs ponderados). Buscar la combinación con mejor ΔPF sin colapsar # trades.
- **QS-3 umbral final:** fijar `score_minimum` por estrategia según la curva lift vs % filtrado.
- **Criterio:** conservar subscore solo si su lift aislado es positivo y aditivo en combinación.

### 4.2 Régimen (HMM)
Herramienta: harness offline (score+régimen por trade); baseline Kaufman ER.

- **RG-0 Baseline:** sin gate.
- **RG-1 por régimen permitido:** medir WR/PF de la estrategia condicionado a cada régimen {trend, range, breakout/unknown}. Definir `allowed_regimes`.
- **RG-2 timeframe del régimen:** 1h (default) vs 4h — cuál separa mejor.
- **RG-3 baseline vs entrenado:** Kaufman ER vs modelo `hmmlearn` entrenado (cuando exista).
- **Criterio:** activar gate solo si excluir un régimen mejora PF sin perder demasiados trades buenos.

**Regímenes disponibles** (`hmm_service.py`): `trending_bull`, `trending_bear`, `ranging`,
`unknown`. El régimen se lee en un **TF más alto** (default **1h**), independiente del TF de la
señal. `unknown` **siempre pasa** (fail-open). El gate **bloquea** una entrada solo si el
régimen es conocido y **no** está en `allowed_regimes`. ⚠ `enabled` con `allowed_regimes`
vacío = no-op (bug P2-12 del backlog).

**Hipótesis inicial de `allowed_regimes` por estrategia** (según su indicador/tipo — es una
HIPÓTESIS a validar con RG-1, no config final):

| Estrategia | Indicador base | Tipo | Hipótesis `allowed_regimes` |
|---|---|---|---|
| 6E5m_ConfStrong_NC_WeakConf | Neo Cloud | Tendencia | `["trending_bull","trending_bear"]` |
| ES5m_ConfNormal_TC_TSR | Trend Catcher | Tendencia | `["trending_bull","trending_bear"]` |
| NQ5m_ConfAny_ST_TC | Trend Catcher + Smart Trail | Tendencia | `["trending_bull","trending_bear"]` |
| RTY15m_ConfNormal_NC_TST | Neo Cloud + Trend Strength Trending | Tendencia (doble) | `["trending_bull","trending_bear"]` |
| GC5m_ContraNormal_ST_WeakConf | Contrarian + Smart Trail | Reversión/rango | `["ranging"]` |
| 6J5m_ConfNormal_TSR_MF50 | Trend Strength Ranging + Money Flow | Mixto/momentum | probar 3: sin gate · `["trending_*"]` · `["ranging"]` |
| ES5m_ConfStrong_TSR_WeakConf | Trend Strength Ranging | Mixto | probar 2: sin gate · `["trending_*"]` |

Notas de calibración:
- Estas hipótesis salen del **nombre/indicador**; la **RG-1 (lift por régimen)** decide el
  `allowed_regimes` final. Los "Mixto" (TSR-based) son ambiguos a propósito → se prueban varias.
- **Afinar por dirección** después si aporta: p. ej. una estrategia solo-largos podría permitir
  únicamente `["trending_bull"]` para no entrar largo en tendencia bajista.
- Empezar con el gate en **1h** (RG-2 prueba 4h). El HMM entrenado (RG-3) puede reetiquetar,
  pero conserva las mismas 4 clases.

### 4.3 SL por ATR
Herramienta: `eval_strategy_battery.py` (test SL×ATR + SL catastrófico).

- **SL-1 barrido k:** `sl_atr_multiplier` ∈ {1.5, 2, 2.5, 3, 4, 6, 8}. Reportar WR/PF/DD/cola. Buscar la k que **capa la cola** sin degradar WR/PF.
- **SL-2 catastrófico en $:** comparar k×ATR vs **SL fijo en $** dimensionado sobre p95 del MAE nativo (que solo dispare en crash, sin costar neto).
- **Criterio:** preferir la k que minimiza cola manteniendo expectancy; documentar el SL en $ equivalente por instrumento (tick value).

### 4.4 TP por ATR
- **TP-0:** TP off (Builtin-Exits de LuxAlgo) — expectancy base.
- **TP-1 barrido:** `tp_atr_multiplier` ∈ {3, 4, 6}. Medir expectancy vs runners (dejar correr).
- **Criterio:** activar TP solo si mejora expectancy neta vs dejar la salida a LuxAlgo/trailing.

### 4.5 Ventana de sesión / hora del día
- **SW-1:** ventana actual vs alternativas (RTH vs extendido; recorte por primeras/últimas horas). Liga con el subscore `time_of_day` de QS.
- **Criterio:** recortar franjas con WR/PF sistemáticamente pobres.

### 4.6 Frescura (staleness)
- **ST-1:** `signal_max_age_entry_seconds` ∈ {30, 60, 120, 300}. Medir impacto en fills y slippage (señales viejas → peor entrada).
- **Criterio:** fijar el máximo que no descarta señales válidas por latencia normal.

### 4.7 Entrada escalonada + Cancel after
Herramientas: `pullback_timing.py`, `check_leg_touch.py`.

- **SC-1 fill-rate por nivel:** con `pullback_timing`, medir % de piernas que se llenan y el **tiempo al pullback** por nivel ×ATR. Ajustar `levels`/`quantities` a lo que realmente se toca.
- **SC-2 escalonado vs entrada única:** expectancy de C1+adds vs una sola entrada a mercado.
- **SC-3 cancel_after por estrategia:** fijar el valor (TradersPost) = **p90 del tiempo al pullback + colchón**, topado a 3600 s.
- **Criterio:** niveles alcanzables con fill-rate razonable; cancel_after que cubra el p90 real.

### 4.8 Guardarraíles / portafolio
- **PF-1 decorrelación:** matriz de correlación de señales y P&L entre las 7. Marcar pares con solape >30% o corr >0.3.
- **PF-2 mismo símbolo:** las **dos ES sobre MES** — cuantificar cuánto se pisan/netean; decidir ruta separada o regla `symbol_busy`.
- **Criterio:** cartera decorrelacionada; una posición por símbolo salvo diseño explícito.

---

## 5. Matriz de seguimiento (llenar al correr)

Estado: ⬜ pendiente · 🟨 en curso · ✅ hecho. En cada celda anotar el **mejor umbral** y el **ΔPF/ΔWR** encontrado.

| Estrategia | QS volume | QS atr_norm | QS vwap | QS time | HMM régimen | SL k×ATR | TP | Ventana | Staleness | Escalonado | Cancel_after |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 6E5m_ConfStrong_NC_WeakConf | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| 6J5m_ConfNormal_TSR_MF50 | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| ES5m_ConfNormal_TC_TSR | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| ES5m_ConfStrong_TSR_WeakConf | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| GC5m_ContraNormal_ST_WeakConf | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| NQ5m_ConfAny_ST_TC | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| RTY15m_ConfNormal_NC_TST | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |

**Decorrelación (cartera):** correlación de señales ⬜ · correlación de P&L ⬜ · caso ES/MES ⬜.

---

## 6. Orden sugerido de ejecución

1. **Re-correr backtests** de las 7 (datos frescos) → set común de trades por estrategia.
2. **QS-0 / RG-0 / SL baseline** para fijar métricas base.
3. **SL-1/SL-2** (capar cola primero, es lo de mayor impacto en riesgo).
4. **QS-1→QS-3** (calidad, el gran pendiente hoy).
5. **RG-1→RG-3** (régimen).
6. **SC / cancel_after** con datos reales de pullback.
7. **PF-1/PF-2** (decorrelación y caso ES/MES).
8. Consolidar umbrales por estrategia → aplicar con `apply_quality_filter.py` / config (dry-run → preview → apply).

---

## 7. Herramientas existentes

- `eval_strategy_battery.py` — batería consolidada (SL×ATR, catastrófico, escalonado, QualityScorer, HMM).
- `eval_quality_filters.py` — lift por filtro y umbral.
- `apply_quality_filter.py` — aplica filtros/umbral a una estrategia (dry-run + backup).
- `pullback_timing.py` / `check_leg_touch.py` — fill-rate y tiempo al pullback.
- `show_signal_filters.py` — verifica qué filtros corrieron en una señal real.

> Nota: los umbrales finales SIEMPRE se validan out-of-sample antes de activar en paper/real.

---

## 8. Roadmap (futuro) — Módulo "Laboratorio" dentro de NTEXECG

Implementar esta batería como **feature de NTEXECG** (no solo scripts): por estrategia, subir
los 2 documentos de Claude+TV (**lista de operaciones** + reporte) y, con las **barras OHLC**
(`NINJATRADER/HOLC/` + bridge `OhlcvBar`), correr la batería reusando los **mismos scorers del
pipeline** (QualityScorer, HMM, SLTPCalculator). Entrega por estrategia: SL correcto, filtros
que dan lift, HMM sí/no, TP — y **botón "aplicar config recomendada"** por el camino guardado
(dry-run → preview → apply).

Principios: es **offline** (no toca dispatch/posición/TradersPost); reutiliza la lógica viva
(resultado offline = comportamiento real); único write = aplicar config con confirmación.
Requisito de datos: el OHLC debe cubrir las fechas de la lista de operaciones.

Secuencia: **después** de cerrar los P0/P1 de seguridad del backlog de arquitectura (es
feature nueva, no arreglo). Camino corto interino: correrlo por CLI (`eval_strategy_battery` +
`eval_quality_filters`) contra la lista + OHLC y emitir el doc de resultados por estrategia.
