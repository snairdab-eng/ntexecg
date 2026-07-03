# PROMPT para Claude Code — Módulo Laboratorio (camino A / CLI offline)

> Pégalo en Claude Code. Construye el **motor de analítica offline** que ingiere la lista
> de operaciones de LuxAlgo + el OHLC histórico y produce, **por estrategia**, todo lo del
> **Anexo 25 §8.1**. Es una **feature nueva**, no un arreglo. **Por fases**, deteniéndote a
> esperar mi aprobación entre fases. **No hagas commit/push** (yo lo hago desde NTDEV).

---

## Rol
Actúa como **ingeniero senior**. Construye un módulo de backtesting/calibración **offline**,
**read-only** respecto al sistema vivo (NO toca dispatch, DB de producción, TradersPost ni
posiciones). Cambios quirúrgicos, con tests.

## Objetivo
Dado el CSV de una estrategia (lista de operaciones de LuxAlgo) + el OHLC del instrumento,
producir un **reporte por estrategia** con: **línea base** (LuxAlgo nativo), **lift de filtros**
(4 subscores de calidad, régimen, EMA-bias, ventana/edge por hora), **SL/TP re-simulados**,
**profundidad y tiempo de pullback** (para el escalonado y el `cancel_after`), todo con
**partición in/out-of-sample** y **contra la línea base**.

## DATOS (rutas y formatos ya verificados)
**Listas de operaciones** (el ÚNICO insumo de trades): `ListaDeOperaciones/*.csv`, 1 por
instrumento. **El reporte `.md` NO se usa** (narrativo).
- Formato CSV (LuxAlgo Backtester): **2 filas por trade**, pareadas por `Trade number`
  (una `Entrada en largo/corto`, una `Salida ...`). Columnas: `Trade number`, `Tipo`,
  `Fecha y hora`, `Señal`, `Precio USD`, `Tamaño (cant.)`, `Tamaño de la posición (valor)`,
  `PyG netas USD`, `PyG netas %`, **`Desviación favorable USD/%` (= MFE)**,
  **`Desviación adversa USD/%` (= MAE)**, `PyG acumuladas USD/%`. (Encabezado con BOM.)
- Mapeo instrumento→archivo: el nombre trae `..._ES1!_...`, `..._NQ1!_...`, `..._RTY1!_...`,
  `..._GC1!_...`, `..._CL1!_...`, `..._6E1!_...`, `..._6J1!_...`, `..._YM1!_...`.

**OHLC — DOS fuentes distintas (no confundir):**
- **`NINJATRADER/HOLC/{SYM}_{tf}.csv`** (`tf ∈ {5m,15m,1h,4h}`): export **histórico estático**
  (fechado ~22-jun-2026), **5+ años** (ES_5m ≈ 387k barras). Columnas
  `DateTime,Open,High,Low,Close,Volume`. `{SYM}=ES,NQ,RTY,GC,CL,6E,6J,YM`. **Esta es la fuente
  del BACKTEST** (cubre mar–jun 2026, el periodo de los trades). NO se actualiza cada 15 min.
- **PostgreSQL `OhlcvBar`** (tabla del bridge, `app.services.bar_store`): la que se **actualiza
  cada ~15 min** y usa el pipeline vivo, pero es una **ventana rodante RECIENTE** (arrancó con el
  bridge ~fin de junio) → **NO tiene historia profunda**, no sirve para marzo.
- **Uso correcto:** backtest histórico ← HOLC CSV; **cola reciente** (fechas posteriores al
  export del HOLC) ← **coser** el `OhlcvBar` de Postgres para llegar hasta hoy. **Validar
  consistencia** en el solape (misma TZ, mismo símbolo, valores OHLC que cuadren) — son dos
  almacenes separados.

## PRINCIPIOS (Anexo 25 §8.1 — obligatorios)
1. **Línea base SIEMPRE presente:** LuxAlgo nativo (WR, PF, expectancy, curva de equity, cola)
   como referencia; cada filtro/SL/TP se reporta como **delta vs esa base**.
2. **In/out-of-sample:** partición **temporal 70/30**; reportar métricas en AMBAS. Solo confiar
   en configs que aguanten fuera de muestra. Marcar buckets con **n bajo**.
3. **Matriz de features por trade** precomputada UNA vez: `{entry_ts, side, pnl%, mae%, mfe%,
   atr_entry, atr%, mae_atr, mfe_atr, sub_volume, sub_atr, sub_vwap, sub_time, regime(1h/4h),
   ema_side(1h/4h·20/50), hour, in_sample}`.
4. **Reutiliza la lógica VIVA** para que offline == producción: importa
   `app.services.quality_scorer` (los 4 subscores), `app.services.hmm_service.classify_regime`
   (Kaufman ER), y la fórmula de SL de `app.services.sl_tp_calculator`. No reimplementar.
5. **Sustractivo vs cambia-desenlace:** filtros (calidad, régimen, EMA, ventana) solo
   INCLUYEN/EXCLUYEN trades (re-agregar). **SL/TP CAMBIAN el desenlace** → re-simular:
   `SL activa ⟺ |mae%| ≥ k·atr%` → resultado `−k·atr%`; `TP ⟺ mfe% ≥ tp·atr%` → `+tp·atr%`;
   para SL+TP juntos hace falta el **orden** de toques (usar el camino intrabar del OHLC 5m).

## ⚠️ BLOQUEANTE antes de cualquier cálculo: validar la ZONA HORARIA
El `Fecha y hora` del CSV (TZ del chart de TradingView) y el `DateTime` del OHLC deben
**alinearse**. Verifícalo así y **no sigas si no cuadra**: para una muestra de entradas, compara
el `Precio USD` del CSV contra el rango [Low,High] de la barra OHLC 5m en ese `DateTime`; si el
precio no cae en/junto a la barra, prueba offsets horarios (UTC/ET/CT) hasta que cuadre y
**documenta el offset detectado**. Todo el análisis depende de esto.

## Entregable (CLI, offline)
`scripts/lab_analyze.py`:
- `python -m scripts.lab_analyze --instrument ES [--csv <ruta>] [--oos 0.3]`
  → escribe `REPORTES/LAB_<instrumento>_<fecha>.md` con las secciones de abajo.
- Read-only; sin escrituras a la DB de producción. Puede cachear la matriz de features en
  `REPORTES/` (parquet/json) para reuso.

## FASES (detente entre cada una)
**Fase 1 — cimientos + validación (empieza por ES):**
- Parser del CSV (pareado por `Trade number`) → trades con entry/exit ts+precio, side, pnl%,
  mae%, mfe%. **Línea base** (WR, PF, expectancy, DD, cola p95 MAE, #trades).
- Cargar OHLC 5m, alinear cada entrada, calcular **ATR(14)** en la entrada (reusa la lógica de
  `sl_tp_calculator`/`market_data`). **VALIDAR TZ** (bloqueante).
- **SL sweep** `k ∈ {1.5,2,2.5,3,4,6,8}` (re-sim con mae% vs k·atr%) y **edge por hora**
  (WR/PF/avg pnl% por hora), todo vs base, con split in/out-of-sample.
- Reporte ES. **Test**: parser con CSV sintético de respuesta conocida + chequeo de TZ.

**Fase 2 — filtros de calidad + régimen + EMA:**
- Subscores (volume/atr/vwap/time con `quality_scorer`), régimen (1h/4h con `classify_regime`),
  EMA-bias (1h/4h · 20/50) por trade → **lift** por filtro/umbral, desglose por régimen, in/out.
- **TP sweep** y **SL+TP conjunto** (con el orden de toques del OHLC 5m).

**Fase 3 — pullback (escalonado + cancel_after):**
- Histograma de **profundidad de retroceso** (fill-rate por nivel ×ATR) **× desenlace**, y
  **tiempo al pullback** (p90 → `cancel_after` = `entry_reserve_timeout_seconds`). Ventana de
  entrada configurable.

Generaliza a los 8 instrumentos al final de cada fase.

## Protocolo por fase
Re-verifica supuestos con datos reales; tests (parser/métricas con respuesta conocida; TZ);
corre el análisis sobre ES y **pega el reporte resultante**; diff resumido; y **detente** con
los commits sugeridos. **No commit/push.** Un fase a la vez, esperando mi visto bueno.

## Fuera de alcance (por ahora)
La UI interactiva (camino B) y su preview en la pestaña Config — eso va después, cuando el motor
offline esté validado. Referencia de diseño completa: **Anexo 25 §4 y §8.1**.
