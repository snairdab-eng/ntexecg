# MEMORIA TÉCNICA — Sistema de Estrategias NTEXECG

**Versión:** 1.1 · **Fecha:** 2026-06-28 · **Estado:** especificación oficial
**Ámbito:** gateway de señales de trading (LuxAlgo/TradingView → NTEXECG → TradersPost)
**Repositorio:** `C:\NTEXECG` (NTDEV) · servidor `cadmin@ntexecg` (`ntexecg.lipatolicucho.com`)

> Este documento es la **especificación técnica oficial, el manual de reconstrucción y el
> registro científico de pruebas** del sistema de estrategias NTEXECG. Está diseñado para
> reconstruir el sistema **desde cero sin contexto previo**. Donde un dato no existe de forma
> explícita se marca **`pendiente NTDEV`** y/o se documenta el supuesto de diseño. No es
> limitativo: cualquier información implícita necesaria para reproducir el sistema se incluye
> como parte del diseño.

---

## 0. ÍNDICE

1. Principio fundamental
2. Origen de los datos
3. Consolidación en ClaudeCode/Cowork
4. Pruebas core del sistema (Pruebas 1–7)
5. Prueba 8 — QualityScorer + HMM
6. Prueba 9 — Decision Replay (con/sin filtro)
7. Validación en paper
8. Limpieza del sistema
9. Arquitectura final del sistema
10. Regla de no limitación
11. Notas de reproducibilidad y output
12. Tabla comparativa final (decisión)
13. Dispatch a TradersPost: payload y ejecución escalonada
14. Registro de cambios (sesión 2026-06-28)
- Apéndice A: inventario de scripts
- Apéndice B: parámetros de calibración escritos
- Apéndice C: conflictos de datos y reconciliación
- Apéndice D: modelo de datos mínimo para reconstrucción

---

## 1. PRINCIPIO FUNDAMENTAL

NTEXECG es un **gateway determinista** que se interpone entre las alertas de una estrategia
LuxAlgo (publicadas por TradingView vía webhook) y el ejecutor de órdenes (TradersPost). Su
responsabilidad NO es generar señales, sino **filtrarlas, calibrar el riesgo (Stop Loss por
ATR obligatorio) y despacharlas de forma auditable**.

Principios de diseño:

- **Fail-fast pipeline de 5 niveles**: la primera regla que falla detiene la evaluación.
- **Calibración por estrategia (señal), NO por instrumento.** La calibración se derivó de los
  trades de una estrategia LuxAlgo específica; por tanto vive en `StrategyProfile`, no en
  `asset_profiles`.
- **Seguridad por capas**: `dry_run` y `traderspost_enabled` se resuelven con semántica de
  kill-switch (ver §9). Todo arranca en simulación/paper.
- **Auditabilidad total**: cada decisión se persiste con su traza de pipeline
  (`pipeline_execution_json`), score, régimen, SL/TP y snapshot de config.
- **Reproducibilidad**: toda calibración proviene de scripts versionados con modo dry-run,
  backup JSON y registro de auditoría antes de aplicar.

Este documento cumple cinco funciones: especificación técnica, manual de reconstrucción,
registro de pruebas, sistema de auditoría de decisiones y base de validación en producción.

---

## 2. ORIGEN DE LOS DATOS

### A) Fuentes
- **ClaudeCode + TradingView Strategy Tester**: backtests de cada estrategia LuxAlgo.
- **Backtests exportados (CSV)**: `ListaDeOperaciones/LuxAlgo®_-_Backtester_(S&O)_[3.3.3]_*.csv`
  (un archivo por instrumento: ES, NQ, YM, GC, RTY, CL, 6E, 6J).
- **Logs de trades** (LuxAlgo / Pine `PUB;bd27017692354be0877227c3b822dcdd` v38) — Pine protegido.
- **Reportes de performance**: `ListaDeOperaciones/Reporte_<INSTR>_LuxAlgo.md`.
- **Barras HOLC** (NinjaTrader): `NINJATRADER/HOLC/<INSTR>_<tf>.csv` (5m/15m/1h/4h), horario **ET**,
  cobertura 2021-01-03 → 2026-06-22.
- **Ejecución simulada en NTDEV** (scripts de simulación, ver Apéndice A).

### B) Métricas base (por estrategia)
Profit Factor (PF), Win Rate (WR), Max Drawdown (MaxDD), Expectancy / avg trade, avg winner,
avg loser, worst trade, distribución de MAE (movimiento adverso máximo). Valores reales por
instrumento en §12 y Apéndice C.

### C) Normalización
- **Conversión a NY time** (America/New_York) — las barras HOLC ya están en ET; las marcas de
  los backtests LuxAlgo alinean **exactamente** a la barra ET (verificado, ver §3 y Prueba 8).
- **Alineación de sesiones**: RTH (cash), 24h/Globex (18:00→17:00 ET con `next_day_end`),
  overnight.
- **Limpieza de datos**: descarte de filas sin OHLCV válido; deduplicado por timestamp.
- **Validación de consistencia**: el precio de entrada debe caer dentro del rango de la barra
  alineada (sanity ≥ ~90% tras corrección de roll; ver §3).
- **Corrección de roll offsets**: el HOLC es continuo **back-ajustado**; la serie de TradingView
  (`NQ1!`, etc.) usa otro empalme. La diferencia es un **offset de nivel ~constante por roll**
  (≈ −282 pts NQ, ≈ −65 pts GC) que salta en cada vencimiento. Se corrige con
  `δ = mediana(close_HOLC − precio)` sobre los ±5 trades vecinos. **Importante:** `atr_normalized`,
  `volume_relative`, `time_of_day` y el régimen son **invariantes** al offset (usan rangos,
  volumen, hora o cocientes); solo `vwap_position` y el chequeo de sanity dependen del nivel.

---

## 3. CONSOLIDACIÓN EN CLAUDECODE / COWORK

Procesos internos ejecutados para preparar el dataset de calibración:

- **Reconstrucción de dataset limpio** desde CSV de trades + HOLC.
- **Validación temporal (NY session alignment)**: confirmado que el timestamp del backtest
  corresponde exactamente a la barra HOLC en ET (la barra `@ts` coincide con la entrada).
- **Recálculo de métricas reales** con ATR(14) Wilder real (no el proxy del Pine), por
  instrumento y micro (÷10 del contrato grande).
- **Segmentación**: por sesión (RTH / Globex / overnight), por régimen (trend/range/volátil vía
  Kaufman ER), por volatilidad (ATR).
- **Detección de inconsistencias**:
  - *Gaps de precio* (rolls) → corrección δ (§2.C).
  - *Diferencias de roll* HOLC vs TradingView → offset ~constante documentado.
  - *Series no alineadas* → verificación de sanity (precio∈[low,high]).
  - *Conflictos de export* (CL): CC reportó PF 2.08/$41,700 desde un export distinto; el Anexo
    16 reconcilió a **PF 1.34/$20,450** con el CSV autorizado `f9857` (ver Apéndice C).

---

## 4. PRUEBAS CORE DEL SISTEMA (Pruebas 1–7)

> Nomenclatura: el esquema conceptual de pruebas usa nombres idealizados de script
> (`sim_sl_atr_grid.py`, etc.). El **mapeo real** a los scripts existentes está en cada prueba y
> en el Apéndice A. Donde un script independiente no existe con ese nombre, se indica el script
> equivalente o `pendiente NTDEV`.

### PRUEBA 1 — BACKTEST ORIGINAL
- **Herramienta:** TradingView Strategy Tester (Pine LuxAlgo v38).
- **Salida:** export CSV de trades por instrumento + `Reporte_<INSTR>_LuxAlgo.md`.
- **Métricas base:** PF, WR, MaxDD, expectancy, avg win/loss (ver §12 y Apéndice C).

### PRUEBA 2 — OPTIMIZACIÓN SL (ATR MULTIPLIER)
- **Script real:** `scripts/sim_sl_matrix.py` (equivalente al conceptual `sim_sl_atr_grid.py`);
  apoyo: `scripts/calibrate_sl_from_trades.py`, `scripts/sweep_matrix.py`.
- **Multiplicadores probados:** 1.5× / 2.0× / 2.5× / 3.0× / 4.0× (reportes CC) y
  Nativo/2.0/2.5/3.0/4.0/6.0/8.0× (Anexo 17). TP de referencia 6×ATR.
- **Salida:** PF, WR, MaxDD, worst trade por multiplicador e instrumento.
- **Resultados (resumen, micro $; "→" = SL elegido):** ver matriz completa en Apéndice C.
  - ES (RTH): nativo PF 1.91; **2.5× → PF 1.58, MaxDD $292, peor −$194** (elegido).
  - NQ (24h): k bajos degradan; **8.0× → PF 1.05** único positivo (elegido, vigilar).
  - YM (24h): **nativo PF 1.92** domina; 8.0× solo como tope catastrófico.
  - RTY (RTH/AM): **nativo PF 6.90/24.0**; 4.0× como tope (elegido 4.0×).
  - GC (RTH): **2.5× → PF 2.73, peor −$262** (elegido); 24h alternativa 8.0×.
  - CL (24h): **nativo PF 1.34**; 8.0× como tope catastrófico (elegido conservador).
  - 6E (RTH): **8.0× → PF 1.80**; política final usa 2.0× directo (entrada única).
  - 6J (24h): **nativo PF 3.99** domina; cualquier stop degrada; 8.0× solo-catástrofe.

### PRUEBA 3 — MAE DISTRIBUTION
- **Script real:** análisis integrado en los reportes CC y en `sim_sl_matrix.py` (no existe
  `sim_mae_distribution.py` como archivo independiente → `pendiente NTDEV` si se desea aislar).
- **Objetivo:** entender el movimiento adverso antes del profit para situar niveles de escalado.
- **Salida disponible:** media, mediana (=p50) y %>1.5×ATR por instrumento.
  **p25/p75/p90 NOMINALES: `pendiente NTDEV`** (los reportes no publican esos percentiles).
  - ES: media 18.7 pt (2.7×ATR), mediana ~8–9 pt (~1.2×ATR), %>1.5×ATR ~45%.
  - NQ: media 106.8 pt (1.24×ATR), mediana 79.9 pt (0.93×ATR), %>1.5×ATR 31%.
  - YM: media 247.7 pt (2.69×ATR), mediana 130.0 pt (1.41×ATR), %>1.5×ATR 44%.
  - RTY: media 15.8 pt (1.93×ATR), mediana 10.4 pt (1.27×ATR), %>1.5×ATR 46%.
  - GC: media 18.3 pt, mediana 8.8 pt, %>1.5×ATR 73% (⚠ proxy ATR sesgado).
  - CL: media 0.8 $/bbl (2.77×ATR), mediana 0.3 (1.02×ATR), %>1.5×ATR 37%.
  - 6E: media $121 (3.22×ATR), mediana $88 (2.33×ATR), %>1.5×ATR 58%.
  - 6J: media $125 (proxy malo), mediana $56, %>1.5×ATR 64%.

### PRUEBA 4 — ESCALADO DE POSICIONES
- **Script real:** `scripts/sim_scaled_entry.py` (+ `scripts/sim_sizing.py` para cantidades por
  nivel). Anexos 18 / 18b / 19.
- **Evaluación:** combinaciones de cantidades por nivel ATR — (0-1-2), (1-2-0), (2-1-1),
  (2-2-2), (3-0-0), (0-0-3), (0-2-2), etc.
- **Métricas:** net PnL, PF, MaxDD, estabilidad.
- **Salida (niveles ATR + cantidades elegidas, Anexo 20):** ver Apéndice B. El escalado es
  **solo diseño** (no hay motor de ejecución; ver §9).

### PRUEBA 5 — STRESS TEST SIN SL
- **Script real:** escenario "Nativo" dentro de `sim_sl_matrix.py` (sin SL fijo, solo salida
  nativa LuxAlgo). No existe `sim_no_sl_emergency.py` independiente → `pendiente NTDEV` si se
  desea aislar.
- **Objetivo:** medir tail risk real (cola de pérdidas sin protección fija).
- **Salida (peor trade nativo, micro $):** NQ −$3,172 · GC −$2,939 (24h) / −$1,547 (RTH) ·
  CL −$2,083 · YM −$918 · ES −$1,016 · 6E −$188 · 6J −$67. Worst del backtest completo (std):
  GC −$15,470, NQ −$7,885, 6E −$1,875; resto `pendiente NTDEV`.

### PRUEBA 6 — STOP DE EMERGENCIA (k×ATR)
- **Script real:** mismo `sim_sl_matrix.py` (escenarios 2.5/4/6/8×). No existe
  `sim_emergency_stop.py` independiente → equivalente documentado.
- **Valores:** 2.5× / 4× / (6×) / 8× ATR.
- **Objetivo:** balance PF vs protección de cola. Conclusión por instrumento en Prueba 2 y §12.
  Para activos donde el nativo domina (YM, 6J, RTY), el k×ATR se usa **solo como tope
  catastrófico** (8×), no como SL operativo.

### PRUEBA 7 — SESSION FILTER
- **Script real:** segmentación de sesión en `sim_sl_matrix.py` / reportes CC (RTH vs 24h vs
  overnight). No existe `sim_session_filter.py` independiente → `pendiente NTDEV` si se aísla.
- **Comparación:** RTH vs 24h vs overnight (PF por segmento).
- **Resultado (ventana óptima elegida):**
  - RTH: **ES** (09:20–15:45), **GC** (09:30–15:45), **RTY/M2K** (AM 09:30–12:00),
    **6E** (09:30–15:45).
  - 24h/overnight: **NQ, YM, CL, 6J** (18:00–17:00 ET, `next_day_end=True`).

---

## 5. PRUEBA 8 — QUALITYSCORER + HMM

Evaluación avanzada del sistema de calidad de señales. Fuente: **Anexo 21**
(`Anexo_21_Filtros_Calidad.md`) y `scripts/eval_quality_filters.py`.

### A) QualityScorer (Nivel 4, opt-in)
Cuatro subscores (0..1) ponderados → 0–100. Si no hay filtros habilitados → score 100 (passthrough).
- `volume_relative`: volumen de la barra vs media de 20 barras previas.
- `atr_normalized`: volatilidad reciente (TR 5) vs su línea base (TR 20); mejor cerca de 1.0.
- `vwap_position`: precio de entrada vs VWAP, con signo según dirección (longs sobre VWAP).
- `time_of_day`: calidad horaria de la sesión (penaliza apertura/almuerzo/cierre).
Implementación: `app/services/quality_scorer.py`. El gate bloquea entradas con score < `score_minimum`.

### B) HMM / régimen (Nivel 4, opt-in)
Clasificador de régimen en timeframe lento (default **1h**): `trending_bull`, `trending_bear`,
`ranging`, `unknown`. Implementación actual: **baseline determinista** por **Kaufman Efficiency
Ratio** (`app/services/hmm_service.py::classify_regime`, ER threshold 0.30, lookback 30); con
soporte para un modelo HMM entrenado (`scripts/train_hmm.py`, `hmm_trainer`) si existe. El gate
bloquea si el régimen es **conocido** y no está en `allowed_regimes`; `unknown` = fail-open.
**Nota crítica:** el gate es **independiente de la dirección** del trade.

### C) Evaluación por instrumento (NQ, YM, GC)
Metodología: se reaplicaron las **funciones reales** del pipeline a cada trade del backtest,
reconstruyendo la ventana de barras en la entrada y corrigiendo el offset de roll (sanity
post-corrección: NQ 91% · YM 88% · GC 91%).

**Baseline (corregido):**

| Instr | n | WR | Net (std $) | PF |
|------|---|------|-------------|------|
| NQ (5m) | 65 | 83.1% | 28,660 | 1.44 |
| YM (15m) | 48 | 89.6% | 22,690 | 1.92 |
| GC (5m) | 107 | 60.7% | 135,390 | 1.95 |

**Hallazgos:**
- **GC** → activar **QualityScorer**. Sweep de `score_minimum`: 55 → net +$6,280; **60 → net
  +$22,650, PF 1.95→2.94** (conserva 90/107). El composite discrimina (Q1 48% win / −$438 vs
  Q4 71% / +$2,016); drivers: `volume_relative` y `time_of_day`. Región 55–60 robusta.
- **YM** → activar **gate de régimen** `allowed_regimes=["ranging"]`. Conserva 28/48, **100%
  win, net $30,965 (Δ +$8,275)**, elimina el cúmulo de 20 trades counter-trend (PF 0.66). El
  score, en cambio, resta net en todos los umbrales.
- **NQ** → **sin cambios**. El edge es contrarian: los counter-trend ganan (PF 2.78);
  bloquearlos resta −$11,555. La mejora por score es frágil (solo thr=65 positivo, +$2,505).

**Conclusión:** los filtros **no son globales**; dependen de **instrumento + estrategia** y
pueden mejorar o degradar el edge. Por eso se aplican selectivamente y se validan en demo.

### D) Decisión aplicada (forward-test en TradersPost demo)
- **GC (MicroGC5mContrarianNormal):** `score_minimum=55` + 4 filtros (peso igual 25) — APLICADO.
- **YM (MicroYM15m_Contrarian):** `regime{enabled, tf=1h, allowed=["ranging"]}` — APLICADO.
- Resto: filtros OFF. (Cableado per-estrategia de `score_minimum` añadido al `ConfigResolver`.)

---

## 6. PRUEBA 9 — DECISION REPLAY ENGINE (CON/SIN FILTRO)

Evaluación contrafactual del sistema de decisiones: comparar qué hubiera pasado **con y sin**
filtros, evitando overfitting.

### Input
- Log `strategy_decisions` (`pipeline_execution_json`, `score`, `regime`, `outcome`,
  `block_reason`, `block_level`).
- Trades ejecutados (resultados TradersPost — `scripts/import_results.py`).
- QualityScorer score y HMM regime por señal.

### Escenarios
- **A) SIN FILTROS:** todas las señales pasan; solo reglas base (SL ATR + ventana).
- **B) CON FILTROS:** QualityScorer threshold + gate de régimen + pipeline completo.

### Métricas
Net PnL, PF, WR, MaxDD, nº de trades filtrados, impacto del filtro en el edge.

### Output
- Tabla CON vs SIN por instrumento (GC / YM / NQ).
- Histograma de scores; sensibilidad de thresholds (50 / 55 / 60).

### Implementación
- **Offline / histórico:** `scripts/eval_quality_filters.py` (ya produjo la tabla CON/SIN de la
  Prueba 8 sobre el backtest).
- **En vivo / demo:** `scripts/compare_filter_decisions.py` (solo lectura) lee `strategy_decisions`
  y reporta, por ventana de días: outcomes por estrategia, distribución de score (GC), bloqueadas
  vs ejecutadas, régimen (YM), y "qué pasaría" a 50/55/60. El pipeline registra score y régimen
  de **todas** las señales —incluidas las bloqueadas—, de modo que el contrafactual es medible sin
  correr dos versiones en paralelo.
- **P&L con/sin filtro en vivo:** se añadirá cuando lleguen resultados de TradersPost demo
  (`pendiente NTDEV` hasta acumular muestra).

**Importancia:** este módulo valida si los filtros realmente mejoran el sistema (anti-overfitting).

---

## 7. VALIDACIÓN EN PAPER

Requisito antes de cualquier promoción a capital real:

- **Mínimo 5 días** operando en paper (idealmente más para baja frecuencia: YM ~1/semana).
- **Validación del pipeline completo** por señal: recepción → símbolo (1.4) → bridge activo
  (1.6) → ventana/sesión (Nivel 2) → riesgo/estado de posición (Nivel 3) → score/régimen
  (Nivel 4) → SL/TP por ATR (Nivel 5) → dispatch.
- Verificar que se aplican: score, régimen, SL, ventana, ATR, y el diseño de escalado (si aplica).
- Herramienta de verificación: `scripts/show_strategy_configs.py` (config efectiva por estrategia)
  y `scripts/compare_filter_decisions.py` (actividad de filtros).

**Estado actual:** las 8 estrategias están en `status=paper` y **despachando a una cuenta DEMO de
TradersPost** (`dry_run=False`, `traderspost_enabled=True`). No hay capital real en riesgo.

---

## 8. LIMPIEZA DEL SISTEMA

Objetivo: dashboard limpio, solo estrategias activas o en paper.

- **Retiradas (status=retired, bloqueadas en Nivel 1):**
  - `TEST_ES_5M` — estrategia de prueba.
  - `NQ5m_ConfirmationNormal_TrendTracer_WeakConfluence` — duplicado obsoleto de NQ.
- Estas se **excluyen** por defecto de los scripts de activación/diagnóstico (gate `HIDDEN =
  {retired, quarantined}`).
- Recomendación: archivar (no borrar) para conservar historial de auditoría. Borrado físico:
  `pendiente NTDEV` (decisión del operador).

---

## 9. ARQUITECTURA FINAL DEL SISTEMA

### 9.1 Stack
FastAPI + PostgreSQL + SQLAlchemy (async) + Jinja2/HTMX/Alpine.js. Servicio gestionado por
systemd (`ntexecg`, uvicorn en 127.0.0.1:8000). Despliegue: NTDEV `git push` → servidor
`git fetch && git reset --hard origin/main` (+ `systemctl restart` si cambia código).

### 9.2 Jerarquía de configuración (ConfigResolver)
`app/services/config_resolver.py` fusiona, en orden (los posteriores sobrescriben):

```
defaults  <  GlobalProfile  <  AssetProfile  <  StrategyProfile
```

- **`asset_profiles` = NEUTRAL**: catálogo del instrumento (contract_type, pine_script, sesión de
  respaldo, riesgo a nivel símbolo). **NO contiene calibración** (SL/ventana/score por activo se
  revirtieron a neutral). No tiene columnas `instrument/enabled/status` (equivalentes:
  `contract_type`, `active`; el estado operativo vive en `Strategy.status`).
- **`StrategyProfile` = FUENTE ÚNICA DE VERDAD** de la calibración por estrategia: `sl_atr_multiplier`,
  `tp_atr_multiplier`, `atr_timeframe`, y `pipeline_config_json` con: `windows` (ventanas
  repetibles), `filters` (QualityScorer), `regime` (gate HMM), `score_minimum` (override per-estrategia),
  `scale_entry` (diseño), `guardrails`.
- Vínculo estrategia↔activo por **string**: `Strategy.asset_symbol == AssetProfile.symbol` (sin FK).

### 9.3 Pipeline de 5 niveles (fail-fast)
`app/services/filter_pipeline.py`:
1. **Nivel 1 — Validación de sistema** (binario): modo global, status de estrategia, mapeo de
   símbolo, guardarraíles symbol/timeframe, heartbeat del bridge de market-data.
2. **Nivel 2 — Contexto temporal**: staleness + día/sesión (`SessionValidator`, ventanas
   `windows` ANY-match si existen; días Domingo=0).
3. **Nivel 3 — Gestión de riesgo** (entradas; salidas exentas): estado de posición (UNKNOWN/LOCKED
   bloquean), daily_loss_stop / max_positions (stubs Fase 1).
4. **Nivel 4 — Calidad** (solo entradas): gate de régimen (opt-in) → QualityScorer score vs
   `score_minimum`.
5. **Nivel 5 — SL/TP por ATR** (solo entradas, tras APPROVE): ATR(14) en el timeframe de señal;
   SL = `sl_atr_multiplier`×ATR (obligatorio); TP opcional (bracket).
Las **salidas** omiten Niveles 3–5 y se permiten incluso con bridge inactivo o modo paused
(prioridad de cierre). Cada nivel se registra en `pipeline_execution_json`.

### 9.4 Resolución de market-data (alias micro→padre)
Para leer barras/ATR/heartbeat se resuelve el símbolo del bridge (micro reusa al padre, p. ej.
MES→ES) vía `SymbolMapper`. Las decisiones, estado de posición y payload usan el símbolo del
contrato mapeado. Nunca se transforman precios.

### 9.5 Estados de estrategia (`Strategy.status`)
`candidate` → `shadow` → `paper` → `micro` → `limited_live` → `live`; más `paused`,
`quarantined`, `retired`. Semántica: `candidate` → QUEUE_FOR_REVIEW; `quarantined/retired` →
BLOCK; `paused` → BLOCK entradas, permite salidas.

### 9.6 Semántica de kill-switch (dispatch)
- `dry_run_efectivo = global.dry_run OR strategy.dry_run` (cualquier capa que pida dry_run gana).
- `traderspost_enabled = global.traderspost_enabled AND strategy.traderspost_enabled` (ambas).
Consecuencia: una estrategia solo puede **restringir** más, nunca escalar por encima del global.
Estado actual: GlobalProfile `dry_run=False, traderspost_enabled=True`; cada estrategia controla
su propio gate. Las 8 estrategias están habilitadas hacia **cuenta DEMO**.

### 9.7 Escalado de posiciones
**Solo diseño** (metadata en `pipeline_config_json["scale_entry"]`: `mode=design_only`, `levels`,
`quantities`, `max_micro_contracts`, `stop_mode=common_position_stop`). **No existe motor de
ejecución escalonada**; `scale_entry_mode=enabled` se **rechaza (422)**. Es un roadmap (Anexo 14 §8).

### 9.8 Auditoría
Cada cambio de configuración pasa por scripts con **dry-run por defecto**, `--apply` explícito,
**backup JSON** (`REPORTES/`) y `AuditService.log` (before/after). Cada decisión de trading se
persiste en `strategy_decisions` con su traza completa.

---

## 10. REGLA DE NO LIMITACIÓN

Este documento no es limitativo. Para garantizar reconstrucción sin memoria previa:
- Incluye información implícita necesaria (modelo de datos mínimo en Apéndice D, flujo de
  despliegue, semántica de resolución y kill-switch).
- Infiere y documenta dependencias técnicas faltantes como **supuestos de diseño** (marcados).
- Donde un dato no existe, se marca **`pendiente NTDEV`** en lugar de inventarlo.
- Es **expandible**: nuevas estrategias se añaden creando su `Strategy` + `StrategyProfile`
  (toda la calibración en el profile); nuevas pruebas se añaden como scripts `sim_*` siguiendo el
  patrón dry-run/backup/auditoría.

**Supuestos de diseño documentados:**
- Las marcas de tiempo del backtest LuxAlgo están en **ET** (verificado por alineación exacta con
  HOLC ET).
- El HOLC es **continuo back-ajustado**; los precios absolutos difieren de TradingView por un
  offset de roll (no afecta rangos/volumen/hora/régimen).
- $/punto del micro = $/punto del contrato grande **÷10** (salvo dato explícito).
- ATR = ATR(14) Wilder sobre el timeframe de la señal.

---

## 11. NOTAS DE REPRODUCIBILIDAD Y OUTPUT

### 11.1 Reconstrucción desde cero (resumen)
1. Clonar repo; crear venv; instalar dependencias (FastAPI, SQLAlchemy, asyncpg, pandas_ta, etc.).
2. Provisionar PostgreSQL; aplicar migraciones (`app/db/migrations`).
3. Sembrar catálogo neutral de `asset_profiles` y los `symbol_maps` (tick value/size por símbolo).
4. Crear cada `Strategy` (status inicial `paper`) + su `StrategyProfile` con la calibración del
   Apéndice B (vía `scripts/apply_strategy_calibration_v1.py`).
5. Aplicar diseño de escalado (`apply_scale_entry_design_v1.py`) y, si procede, filtros Anexo 21
   (`apply_anexo21_demo.py`).
6. Configurar webhooks de TradersPost por estrategia y habilitar dispatch
   (`enable_traderspost_demo.py`) — apuntando a **cuenta DEMO** para forward-test.
7. Verificar con `show_strategy_configs.py`.

### 11.2 Entorno de pruebas (sandbox)
Para correr la suite sin Postgres: `DATABASE_URL=sqlite+aiosqlite:///:memory:`, stub de
`pandas_ta`, `PYTHONPYCACHEPREFIX` aislado. Tests en `tests/` (pipeline, quality scorer, config
API/resolver, web).

### 11.3 Output de esta memoria
Documento Markdown estructurado por secciones/pruebas, con scripts utilizados (Apéndice A),
resultados reales (§§4–6, §12, Apéndice C), decisiones tomadas (§5.D, §12), riesgos y supuestos
(§10), y notas de reproducibilidad (§11). Donde falta dato: `pendiente NTDEV`.

---

## 12. TABLA COMPARATIVA FINAL (DECISIÓN)

> **⚠ HISTÓRICO (nota 2026-07-03, NX-25):** esta tabla corresponde a la
> generación ANTERIOR de estrategias (`ES5m`, `NQ5m_ConfirmationAny`, …). La
> generación vigente (7 estrategias `*_Conf*_*`) está en **Anexo 23**
> (calibración) y **Anexo 24** (playbook); la config efectiva real se consulta
> con `scripts/show_strategy_configs.py`.

> PF/WR/MaxDD = backtest LuxAlgo (MaxDD en **micro $**, escenario nativo/elegido). SL/TF/ventana/
> escalado = config **efectiva en vivo** (verificada con `show_strategy_configs.py`, coincide con
> Anexo 20 §4.4). `Status` = estado actual; `Decisión` = estado recomendado (Anexo 20). Todas
> despachan a **TradersPost DEMO** (`dry_run=False`). No se inventan datos: faltantes = `pend. NTDEV`.

| Estrategia | Activo | Status | PF | WR | MaxDD (micro) | SL ATR | ATR TF | Ventana | Escalado (niveles·qty, max) | QualityScore | HMM | Decisión |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ES5m | MES | paper (demo) | 1.81 (RTH 2.03) | 82.4% | $1,016 | 2.5× | 5m | RTH 09:20–15:45 | 0.75/1.25 · 0-1-4, max 5 | OFF | OFF | **production** |
| NQ5m_ConfirmationAny | MNQ | paper (demo) | 1.44 | 84.4% | $3,588 | 8.0× (cap) | 5m | 24h 18:00–17:00 | 4/5 · 0-2-2, max 4 | OFF (evaluado) | OFF (evaluado) | **production** (vigilar) |
| MicroYM15m_Contrarian | MYM | paper (demo) | 1.92 | 89.6% | $918 | 8.0× (cap; nativo domina) | 15m | 24h 18:00–17:00 | 1.5/2 · 0-0-4, max 4 | OFF | **ranging** (aplicado) | **production** (vigilar DD) |
| MicroGC5mContrarianNormal | MGC | paper (demo) | 1.95 (RTH 2.73 @2.5×) | 60.4% | $3,869 | 2.5× | 5m | RTH 09:30–15:45 | 0.5/0.75 · 0-0-3, max 3 | **score≥55, 4 filtros** (aplicado) | OFF | **production** |
| M2K15mConfirmationNormal | M2K (RTY) | paper (demo) | 2.15 (RTH 6.90) | 86.6% | $746 | 4.0× (cap; nativo domina) | 15m | RTH AM 09:30–12:00 | 0.5/1.5 · 3-0-0, max 3 | OFF | OFF | **shadow** (n bajo AM) |
| 6E5mConfirmationStrong | M6E | paper (demo) | 1.44 (RTH 1.84) | 84.8% | $299 | 2.0× | 5m | RTH 09:30–15:45 | 0.5/0.75 · 3-0-0, max 3 | OFF | OFF | **shadow** (net bajo) |
| 6J5mContrarianAny | MJY | paper (demo) | 3.99 | 93.5% | $68 | 8.0× (cap; nativo domina) | 5m | 24h 18:00–17:00 | 2/3 · 0-3-0, max 3 | OFF | OFF | **shadow** (retorno bajo) |
| CL15mContrarianNormal | MCL | paper (demo) | 1.34 (reconciliado) | 78.1% | $2,237 | 8.0× (cap) | 15m | 24h 18:00–17:00 | 0.5/2.5 · 0-0-3, max 3 | OFF | OFF | **shadow** (PF/cola débil) |

**Notas de la tabla:**
- "cap" = el SL k×ATR actúa como tope catastrófico; para YM/RTY/6J el nativo LuxAlgo domina y el
  stop fijo solo limita la cola. La salida principal es **siempre la nativa LuxAlgo**.
- TP por ATR = **6.0×** en las 8 (bracket); `tp_atr_multiplier` configurable por estrategia.
- Guardarraíles `enforce_symbol_match` y `enforce_timeframe_match` = **ON** en las 8.
- `worst trade` del backtest completo: GC −$15,470, NQ −$7,885, 6E −$1,875 (std); resto
  `pend. NTDEV` (solo "peor por escenario" en Anexo 17).
- PF/WR de CL están **reconciliados** (Anexo 16); el reporte CC original (PF 2.08) usaba otro export.

### Criterio de decisión (production / shadow / paper / retire)
- **production**: PF robusto + MaxDD acotado + muestra suficiente (ES, NQ, YM, GC). Mantener en
  paper/demo hasta cerrar validación ≥5 días.
- **shadow**: edge presente pero muestra pequeña o net bajo (RTY AM n=11, 6E, 6J, CL). Observar
  sin promover.
- **paper**: estado operativo actual de las 8 (forward-test en demo).
- **retire**: `TEST_ES_5M`, `NQ5m_ConfirmationNormal_TrendTracer_WeakConfluence`.

---

## 13. DISPATCH A TRADERSPOST: PAYLOAD Y EJECUCIÓN ESCALONADA

### 13.1 Constructor de payload (`PayloadBuilder`)
Reglas (doc 00 §8, doc 10): `ticker` = `mapped_symbol` (contrato, no el alias micro); **toda
entrada incluye `stopLoss`** (si falta `sl_price` → `ValueError`); las **salidas nunca** llevan
`stopLoss`/`takeProfit`. Stop absoluto bajo `stopLoss.stopPrice` (no `price`); TP absoluto bajo
`takeProfit.limitPrice`. `extras` viaja siempre para cross-check.

**Ejemplo — entrada simple (GC, sin escalonado):**
```json
{
  "ticker": "MGCQ2026", "action": "buy", "signalPrice": 5000.0, "quantity": 1,
  "sentiment": "bullish",
  "stopLoss":   { "type": "stop",  "stopPrice":  4985.0 },
  "takeProfit": { "type": "limit", "limitPrice": 5036.0 },
  "extras": { "strategy_id": "MicroGC5mContrarianNormal", "ntexecg_score": 78,
              "atr_value": 6.0, "sl_multiplier": 2.5, "provider": "NinjaTraderBridge" }
}
```
**Ejemplo — salida (sin SL/TP):**
```json
{ "ticker": "MGCQ2026", "action": "exit", "signalPrice": 5036.0, "quantity": 1,
  "extras": { "ntexecg_score": 100, "atr_value": null, "sl_multiplier": null } }
```

### 13.2 Motor de ejecución escalonada (`PayloadBuilder.build_scaled`)
Implementa el Anexo 14 §8. Ante una **entrada** con `scale_entry.mode ∈ {execute, live}`, expande
la señal en **varias órdenes** (un POST por leg):

- **C1** = entrada base a mercado (sin `orderType`), `quantity = quantities[0]`.
- **C2..Cn** = órdenes **límite** (`orderType:"limit"`, `limitPrice = señal ∓ levels[i-1]×ATR`;
  `−` para long, `+` para short), `quantity = quantities[i]`.
- **Stop común**: el mismo `stopLoss.stopPrice` (= señal ∓ `sl_atr_multiplier`×ATR del Nivel 5) en
  todos los legs; `takeProfit` común si está definido.
- **Fallback a entrada única** (`[build(...)]`) cuando: es salida, `mode` no es de ejecución, no hay
  `quantities`, `total ≤ 0`, `total > max_micro_contracts`, o falta precio/ATR para un add.
- Legs con `quantity = 0` se omiten. Formato verificado contra la doc de TradersPost
  (`orderType:"limit"` + `limitPrice`).

### 13.3 Ejemplos reales del webhook escalonado

**ES5m (MES) — LONG escalonada → 2 webhooks (órdenes límite):** señal @5000, ATR(5m)=5.0,
SL 2.5× → 4987.5, TP 6.0× → 5030, `quantities=[0,1,4]`, `levels=[0.75,1.25]`.
```json
// leg 1/2
{ "ticker": "MESU2026", "action": "buy", "quantity": 1, "sentiment": "bullish",
  "signalPrice": 5000.0, "orderType": "limit", "limitPrice": 4996.25,
  "stopLoss": { "type": "stop", "stopPrice": 4987.5 },
  "takeProfit": { "type": "limit", "limitPrice": 5030.0 },
  "extras": { "leg_index": 1, "leg_quantity": 1, "level_atr": 0.75, "atr_value": 5.0,
              "sl_multiplier": 2.5, "strategy_id": "ES5m" } }
// leg 2/2
{ "ticker": "MESU2026", "action": "buy", "quantity": 4, "sentiment": "bullish",
  "signalPrice": 5000.0, "orderType": "limit", "limitPrice": 4993.75,
  "stopLoss": { "type": "stop", "stopPrice": 4987.5 },
  "takeProfit": { "type": "limit", "limitPrice": 5030.0 },
  "extras": { "leg_index": 2, "leg_quantity": 4, "level_atr": 1.25, "atr_value": 5.0,
              "sl_multiplier": 2.5, "strategy_id": "ES5m" } }
```

**RTY (M2K) — LONG a mercado → 1 webhook:** señal @2300, ATR(15m)=4.0, SL 4.0× → 2284,
TP → 2324, `quantities=[3,0,0]` (C1 a mercado, sin adds).
```json
{ "ticker": "M2KU2026", "action": "buy", "quantity": 3, "sentiment": "bullish",
  "signalPrice": 2300.0,
  "stopLoss": { "type": "stop", "stopPrice": 2284.0 },
  "takeProfit": { "type": "limit", "limitPrice": 2324.0 },
  "extras": { "leg_index": 1, "leg_quantity": 3, "level_atr": 0.0, "atr_value": 4.0,
              "sl_multiplier": 4.0, "strategy_id": "M2K15mConfirmationNormal" } }
```

### 13.4 Dispatch multi-leg (`_dispatch_approved`)
En un APPROVE de entrada se obtiene la lista de legs y se envía **cada uno** vía
`TradersPostClient.send`, registrando **un `WebhookDelivery` por leg**; el estado de posición se
actualiza **una vez** con la cantidad **total**. Con un solo payload (salidas / entradas no
escalonadas) el comportamiento es idéntico al previo. Sigue rigiendo el gate de dispatch
`dry_run = env AND traderspost_enabled AND not dry_run` (en dry-run cada leg se registra como
`DRY_RUN` sin HTTP).

### 13.5 Activación / reversión
`scripts/set_scale_execution.py` cambia `scale_entry.mode` entre `execute` y `design_only`
(`--strategy <id>` o `--all`; dry-run + backup + auditoría). Reversión: `--all --off --apply`.

### 13.6 Caveats (ejecución escalonada)
- Los diseños **solo-límite** (C1=0: ES, GC, YM, 6J, CL, NQ) **solo entran si hay pullback** al
  nivel; si el precio no retrocede, no hay fill (se omite el trade). **NQ** usa niveles muy
  profundos (−4/−5×ATR) → llenará pocas veces. RTY y 6E tienen C1 a mercado (entran al instante).
- El stop se calcula desde el **precio de señal**, no desde el fill → el riesgo fill→stop de un add
  es menor (p. ej. add a −0.75×ATR con SL 2.5× ⇒ ~1.75×ATR).
- **Verificar en demo** que TradersPost deja las límite **reposando** y gestiona SL/TP como **una
  sola posición** (no brackets independientes por leg).

---

## 14. REGISTRO DE CAMBIOS (sesión 2026-06-28)

| Cambio | Detalle | Estado |
|---|---|---|
| `ConfigResolver`: score_minimum per-estrategia | lee `pipeline_config_json["score_minimum"]` | desplegado |
| Anexo 21 — GC | QualityScorer `score_minimum=55` + 4 filtros (peso igual) | aplicado (DB) |
| Anexo 21 — YM | gate de régimen `allowed_regimes=["ranging"]` (1h) | aplicado (DB) |
| TradersPost **demo** | 8 estrategias `dry_run=False`, `traderspost_enabled=True` (cuenta demo) | aplicado (DB) |
| **Motor escalonado** | `PayloadBuilder.build_scaled` + dispatch multi-leg | desplegado |
| Escalonado **activado** | `scale_entry.mode=execute` en las 8 (números de backtest) | aplicado (DB) |
| UI | Scale Entry dentro de **Config**; pestaña *Efectivo* eliminada; editor de Activos simplificado | desplegado |
| Diagnóstico | `show_strategy_configs.py` marca `EJECUTA ⚠` cuando `mode=execute` | desplegado |

**Backups generados:** `REPORTES/anexo21_backup_*.json`, `REPORTES/traderspost_enable_backup_*.json`,
`REPORTES/scale_exec_backup_*.json`. Reversiones: `set_scale_execution --all --off --apply`,
`enable_traderspost_demo` (flags), `apply_anexo21_demo` (vía backup).

**Pendiente / observación:** dejar correr 1 semana en demo y revisar (a) actividad de filtros
GC/YM (`compare_filter_decisions.py`), (b) fills escalonados y comportamiento del stop común en
TradersPost. Ampliar el reporte para incluir legs enviados/llenados.

---

## APÉNDICE A — INVENTARIO DE SCRIPTS (`scripts/`)

**Calibración / aplicación (dry-run + backup + auditoría):**
- `apply_strategy_calibration_v1.py` — escribe la calibración por estrategia en `StrategyProfile`.
- `apply_scale_entry_design_v1.py` — siembra el diseño `scale_entry` (design_only).
- `apply_profile_policy_v1.py` — política a `asset_profiles` (v1.1; superseded por calibración
  per-estrategia; los activos quedaron neutrales vía `revert_asset_profiles_v1.py`).
- `revert_asset_profiles_v1.py` — revierte `asset_profiles` a neutral.
- `apply_anexo21_demo.py` — aplica GC (QualityScorer 55) + YM (régimen ranging).
- `enable_traderspost_demo.py` — habilita dispatch a TradersPost (demo) en estrategias con webhook.
- `set_scale_execution.py` — activa/desactiva la ejecución escalonada (`scale_entry.mode`), por estrategia o `--all`.
- `sync_strategy_windows_v1.py` — sincroniza ventanas repetibles.
- `calibrate_all.py`, `calibrate_sl_from_trades.py` — cálculo de SL desde listas de trades.

**Simulación / análisis:**
- `sim_sl_matrix.py` — matriz SL k×ATR (Prueba 2/5/6/7).
- `sim_scaled_entry.py` — escalado de posiciones (Prueba 4).
- `sim_sizing.py` — cantidades de microcontratos por nivel.
- `sweep_matrix.py` — barridos combinados.
- `eval_quality_filters.py` — Prueba 8/9 offline (QualityScorer/HMM con/sin filtro).
- `compare_filter_decisions.py` — Prueba 9 en vivo (log de decisiones; solo lectura).
- `train_hmm.py` — entrenamiento del modelo HMM (opcional; baseline = Kaufman ER).

**Diagnóstico / operación:**
- `show_strategy_configs.py` — config efectiva por estrategia + estado Anexo 21 (solo lectura).
- `diag_profiles.py` — diagnóstico de perfiles.
- `import_results.py` — importa resultados de TradersPost.
- `backfill_market_bars.py`, `backup_db.py`, `seed_dev_data.py`, `simulate_webhook.py`,
  `rollover_alert.py`, `mount_ntbridge.sh`.

> Scripts conceptuales sin archivo independiente (funcionalidad integrada en `sim_sl_matrix.py` o
> reportes CC): `sim_sl_atr_grid`, `sim_mae_distribution`, `sim_no_sl_emergency`,
> `sim_emergency_stop`, `sim_session_filter`. Aislarlos = `pendiente NTDEV` si se requiere.

## APÉNDICE B — PARÁMETROS DE CALIBRACIÓN ESCRITOS (Anexo 20 §4.4)

`sl_atr_multiplier / atr_timeframe / scale_entry levels / max_micro` (ventana en §12):

| Micro | SL ATR | ATR TF | Niveles ATR | Cantidades | max_micro |
|---|---|---|---|---|---|
| MES | 2.5 | 5m | [0.75, 1.25] | 0-1-4 | 5 |
| MNQ | 8.0 | 5m | [4, 5] | 0-2-2 | 4 |
| MYM | 8.0 | 15m | [1.5, 2] | 0-0-4 | 4 |
| M2K | 4.0 | 15m | [0.5, 1.5] | 3-0-0 | 3 |
| M6E | 2.0 | 5m | [0.5, 0.75] | 3-0-0 | 3 |
| MJY | 8.0 | 5m | [2, 3] | 0-3-0 | 3 |
| MGC | 2.5 | 5m | [0.5, 0.75] | 0-0-3 | 3 |
| MCL | 8.0 | 15m | [0.5, 2.5] | 0-0-3 | 3 |

TP ATR = 6.0× en todas. Escalado = **diseño** (no ejecuta).

## APÉNDICE C — CONFLICTOS DE DATOS Y MATRIZ SL

**Reconciliaciones (Anexo 16):**
- **CL**: CC reportó PF 2.08 / net $41,700 (export distinto). Autorizado (CSV `f9857`, 105 trades):
  **PF 1.34 / net $20,450 / WR 78.1% / avg loss −$2,607**. CC sobreestimó ~2×.
- **n discrepante (CC vs Anexo 16)**: ES 119/122 · NQ 64/65 · GC 106/107 · CL 103/105 · 6J 77/78.
  YM 48 y RTY 112 coinciden.
- **ATR proxy vs real**: el proxy del Pine estaba inflado en varios (NQ ~86 vs real 22; GC ~4.0 vs
  real 6.0). La calibración usa **ATR(14) real**.

**Matriz SL — Anexo 17 (micro $, Net/PF/peor/MaxDD; escenario elegido en negrita):**
- ES RTH (n=45): Nativo 1,586/1.91/−1,016 · **2.5× 915/1.58/−194/292** · 8× 690/1.29/−621
- NQ 24h (n=65): Nativo 2,866/1.44/−3,172 · 4× −2,189/0.69 · **8× 287/1.05/−481/2,126**
- YM 24h (n=48): **Nativo 2,269/1.92/−918/918** · 4× 213/1.12 · 8× 115/1.05/−294
- RTY RTH (n=22): **Nativo 1,708/6.90/−144** · 4×~ · 8× 771/1.86/−312 (AM n=11: Nativo 1,322/24.0)
- GC 24h (n=107): **Nativo 13,539/1.95/−2,939** · 8× 4,652/1.36/−817 (RTH n=25: **2.5× 2,948/2.73/−262**)
- CL 24h (n=105): **Nativo 2,045/1.34/−2,083** · 4× 68/1.02 · 8× −440/0.92/−809
- 6E 24h (n=99): Nativo 366/1.44/−188 · **8× 463/1.80/−53** (final usa 2.0× RTH directo)
- 6J 24h (n=78): **Nativo 383/3.99/−67** · 8× 5/1.02/−39

**PF/DD/peor por perfil FINAL (Anexo 20 §2, micro):** ES PF 2.34/DD $891/peor −$524 · NQ
2.16/$1,789/−$842 · YM 1.88/$3,292/−$883 · RTY 14.30/$290–483/−$290 · 6E 6.53/$45–60/−$45 ·
6J 1.52/$320/−$87 · GC(RTH) 5.70/$1,127/−$549 · CL 1.41/$2,435/−$1,669 (agresivo peor −$5,563).

## APÉNDICE D — MODELO DE DATOS MÍNIMO (reconstrucción)

- **`asset_profiles`**: symbol, name, contract_type, active, pine_script_config,
  session_config_json (respaldo neutral), atr_period/atr_timeframe (neutral), riesgo símbolo. Sin
  calibración.
- **`strategies`**: strategy_id (único), name, asset_symbol (string→symbol), timeframe, status,
  enabled, created_at.
- **`strategy_profiles`** (1:1 con strategy): traderspost_webhook_url, traderspost_enabled,
  dry_run, mode, sl_atr_multiplier, tp_atr_multiplier, atr_period, atr_timeframe,
  `pipeline_config_json` { windows[], filters{}, regime{}, score_minimum, scale_entry{}, guardrails{} },
  overrides de riesgo/horario, version, updated_by.
- **`strategy_decisions`**: normalized_signal_id, strategy_id, outcome, block_reason, block_level,
  score, score_breakdown_json, pipeline_execution_json, sl_price/tp_price/atr_value,
  config_snapshot_json, decided_at.
- **`global_profile`**: mode, dry_run, traderspost_enabled, score_minimum, max_open_positions,
  daily_loss_stop, allow_*, news_*, timezone.
- **`symbol_maps`**: symbol→contract_type, tick_value, tick_size (catálogo de instrumento).
- **`normalized_signals`**, **`audit_log`**, **`strategy_performance`** (resultados importados).

> Esquema autoritativo: `CONTRATO/04_MODELO_DATOS_v1_0.md` y `app/models/`. Migraciones en
> `app/db/migrations/`.

---

*Fin de la Memoria Técnica NTEXECG v1.0. Documento vivo: ampliar con cada nueva estrategia,
prueba o resultado de validación en demo/paper. Datos faltantes marcados `pendiente NTDEV`.*
