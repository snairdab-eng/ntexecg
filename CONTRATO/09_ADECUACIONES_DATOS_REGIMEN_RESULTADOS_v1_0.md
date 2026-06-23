# NTEXECG — Adecuaciones: Datos de Mercado, Régimen HMM y Verdad de Resultados v1.0
### Anexo 09 al Contrato Técnico (complementa los documentos 00–08)

**Fecha:** 2026-06-23 · **Estado:** para incorporación al contrato
**Origen:** decisiones tomadas durante la implementación de las Fases 6 y 8 y de la
persistencia de datos de mercado. Confirmadas por el operador (autoridad del contrato).

---

## 0. Naturaleza de este documento

Complementa, **no reemplaza**, a los documentos 00–08. Precisa **cómo** se implementan
partes que el contrato (doc 05, backlog) dejaba a nivel de objetivo: persistencia OHLC,
régimen de mercado por HMM y métricas de resultados. Reafirma la corrección de alcance de
riesgo del **Anexo 08 §0-bis**. No renumera el pipeline de 5 niveles ni cambia el endpoint.

---

## 1. Persistencia de datos de mercado (`ohlcv_bars`)

- La tabla `ohlcv_bars` (existía como placeholder de Fase 5) es ahora la **fuente de verdad
  histórica** para entrenar el HMM y para backtests futuros.
- **Backfill** único desde los CSV HOLC exportados de NinjaTrader
  (`scripts/backfill_market_bars.py`): 2021→presente, ~4.4 M barras, 8 símbolos ×
  {5m, 15m, 1h, 4h}. Idempotente.
- **Actualización automática**: job `MarketBarsUpdater` (APScheduler, cada
  `MARKET_BARS_UPDATE_MINUTES` = 15 min) lee las barras frescas del bridge y hace *upsert*
  idempotente. Constraint único `symbol + timeframe + bar_time + provider`; ambos writers
  usan `provider="ninjatrader"` para deduplicar historia ↔ feed. **No** se re-exporta de
  NinjaTrader a diario.
- **Decisión vs contrato:** el doc 05 (Fase 6) decía "OHLCV de DB propia guardados desde
  Fase 5". Se materializa con backfill + feed en vivo, no con un guardado por señal.
- **Zona horaria:** `bar_time` se guarda como hora de NinjaTrader (ET), consistente entre
  backfill y feed (dedup exacto). Para features tipo "hora del día" habría que reinterpretar.
- **Corrección al exportador** `NTraderDataExporter.cs`: el 5m se exportaba desde la serie
  primaria del chart (frágil → duplicó/truncó datos de ES y 6E). Se agregó una **serie 5m
  explícita** (`AddDataSeries(Minute, 5)`); el export ya no depende del TF del chart.

---

## 2. Fase 6 — Régimen de mercado (HMM)

- **Motor:** `hmmlearn` (GaussianHMM, 3 estados) por símbolo. Estados etiquetados a
  `trending_bull / trending_bear / ranging` por la media de retorno de cada estado.
- **Timeframe del régimen CONFIGURABLE** (`HMM_REGIME_TIMEFRAME`, default **1h**; 4h
  soportado), **independiente del TF de entrada (5m)**. El régimen es un estado de mayor
  nivel; 5m es demasiado ruidoso. (Precisa el contrato, que no fijaba TF.)
- **Features:** log-returns, volatilidad rolling, volume ratio; **estandarizadas (z-score)**
  antes del fit; un guard de NaN rechaza fits degenerados.
- **Entrenamiento:** `HMMTrainerJob` **semanal** (cron `HMM_TRAIN_DAY_OF_WEEK` /
  `HMM_TRAIN_HOUR`, default domingo 02:00 UTC) + script manual `scripts/train_hmm.py`.
  Lee de `ohlcv_bars`.
- **Persistencia:** modelos en disco (**joblib**) en `MODELS_DIR`; cache por mtime (el
  reentrenamiento se recoge sin reiniciar el servicio).
- **Gate opt-in por estrategia:** `pipeline_config_json["regime"]` =
  `{enabled, timeframe, allowed_regimes}`, configurable en la UI (tab Config). Bloquea la
  entrada en **Nivel 4** si el régimen actual no está permitido; `unknown` (datos
  insuficientes) **nunca** bloquea (*fail-open*).
- **Fallback:** sin modelo entrenado o sin `hmmlearn`, `get_regime` usa un **clasificador
  baseline determinista** (efficiency ratio). El sistema es seguro aunque falte la
  dependencia ML.

---

## 3. Fase 7 — Portfolio Risk + Conflict Resolver: alcance recortado

Reafirma el **Anexo 08 §0-bis**. Cuando se implemente la Fase 7:

- **SÍ** (con lo que NTEXECG tiene: señales + estado estimado de posición):
  - **Conflict Resolver:** mismo símbolo, dirección opuesta → resolver por QualityScore;
    empate → rechazar ambas; registrar en `conflict_log`.
  - **Límites de cartera por NÚMERO de posiciones**: total y por **grupo de correlación**.
  - **Modo de cartera** (normal / defensive / flatten_only / paused) como override global;
    `defensive` = **endurecer filtros** (subir score mínimo, restringir regímenes, bajar el
    tope de correlacionadas).
- **NO** (sin P&L ni fills en vivo): **daily loss stop en $** y **reducción de tamaño** de
  contratos → los administra el broker/prop. A lo más, referencia documental.

---

## 4. Fase 8 — Verdad de resultados (reconciliación + métricas reales)

El contrato (doc 05, Fase 8) asumía métricas; este anexo define **cómo**, dado que NTEXECG
**no recibe P&L ni fills en tiempo real**:

- **Reporte semanal manual** del operador: una fila por operación cerrada. Formato canónico
  en `DOCS/resultados_semanales_PLANTILLA.csv` (`signal_id, strategy_id, symbol, direction,
  quantity, entry_time, entry_price, exit_time, exit_price, pnl, exit_reason, fees`).
- **Import idempotente** (`scripts/import_results.py` → tabla `execution_results`, dedup por
  `row_hash`). `pnl` es **opcional**: si falta, se calcula desde precios × tick value del
  catálogo de instrumentos.
- **Reconciliación** contra lo que NTEXECG envió (`WebhookDelivery` SENT): exacta por
  `signal_id` (que NTEXECG manda en `extras`), o heurística por símbolo + dirección + ventana
  de tiempo (±15 min). Cada trade queda `signal_id` / `heuristic` / `unmatched`.
- **Métricas reales por estrategia:** nº de trades, win rate, profit factor, expectativa,
  P&L total, max drawdown, % reconciliado.
- **Alcance:** esto NO es protección en tiempo real (el reporte llega con días de retraso).
  Su valor: **evaluación real** de estrategias (promoción/retiro), detección de desviación
  estimado ↔ real, y **calibración basada en datos** de los filtros (Fase 5) y del régimen
  (Fase 6).
- **Pendiente (Fase 8 completa):** UI de subida del reporte + dashboard de métricas (hoy es
  por script).

---

## 5. Decisiones confirmadas (2026-06-23)

- **D-09.1** Régimen en TF superior configurable (default 1h, opción 4h), **no** 5m.
- **D-09.2** Persistencia OHLC en Postgres alimentada por **backfill HOLC + feed del bridge**
  (no re-export diario de NinjaTrader).
- **D-09.3** Modelos HMM **en disco (joblib)** en `MODELS_DIR`.
- **D-09.4** Resultados por **reporte semanal manual** (no hay P&L/fills en vivo); formato
  canónico definido en `DOCS/resultados_semanales_PLANTILLA.csv`.
- **D-09.5** Fase 7 **sin gestión monetaria en vivo** (reafirma Anexo 08 §0-bis).

---

## 6. Dependencias y despliegue nuevos

- **`hmmlearn>=0.3.0`** (+ `scikit-learn`, `scipy`) — instalar en el venv de NTDEV y de
  producción (`pip install hmmlearn`).
- **`MODELS_DIR`** (config). En producción: `/home/cadmin/ntexecg/models` (ruta **absoluta**).
  Gitignored como `/models/` (anclado a la raíz; **no** ignora `app/models/`).
- **Migración `c3d4e5f6a7b8`** (add `execution_results`) → `alembic upgrade head` en prod.
- **Jobs APScheduler nuevos** en el lifespan: `MarketBarsUpdater` (15 min) y `HMMTrainerJob`
  (semanal). Se suman a `HeartbeatMonitor` (30 s) y `ExitManagerJob` (60 s).
