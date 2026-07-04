# Motor de Riesgo NTEXECG — Especificación para desarrollo (Fable5)

## 1. Objetivo
Un **motor único** que reciba el *listado de operaciones* de una estrategia LuxAlgo, lo **acumule semana a semana**, y con un comando (`calcular`) genere un **estudio de gestión de riesgo** completo: analítica vs línea base + configuraciones sugeridas. El estudio se **guarda junto al listado** para poder **recrearlo idénticamente** en cualquier momento (determinista).

Filosofía: **honestidad estadística por encima de números bonitos.** Siempre comparar contra la línea base (el listado crudo), reportar el número **fuera de muestra** (no el in-sample inflado), y marcar banderas cuando los datos no dan (HOLC corto, N bajo, parámetro que no generaliza).

## 2. Flujo de uso (diario o semanal — da igual la cadencia)
Cada CSV que se deja es un **re-export COMPLETO** de LuxAlgo (todo el histórico). Por tanto `integrar` **SOBRESCRIBE, no fusiona**: el export nuevo reemplaza al master. No hace falta merge/dedup incremental.
```
1) integrar  <export_crudo.csv>   -> SOBREESCRIBE master con el export (archiva snapshot con fecha), recalcula ATR
2) calcular                        -> corre todos los estudios, guarda reporte + métricas + snapshot
3) recrear   <fecha>              -> reproduce el estudio de esa fecha desde el snapshot archivado
   estado                          -> resumen: nº trades, rango fechas, cobertura HOLC, última corrida
```
El **último export es la fuente de verdad** (contiene todo el histórico). Se puede reintegrar cuantas veces se quiera; siempre sobrescribe. Los **snapshots archivados** (uno por fecha de integración) preservan la historia para poder **recrear cualquier corrida pasada**.

## 3. Persistencia (estructura de archivos)
```
C:\NTEXECG\MotorRiesgo\<ACTIVO>_<codigo>\
  master.csv               # ULTIMO export completo (se sobrescribe en cada integrar)
  enriched.csv             # por-trade: +ATR14, MAE_ATR, MFE_ATR, lado, duracion, sesion
  manifest.json            # para recrear: hash master, HOLC usado + su ultima barra, version grids, timestamp
  snapshots\
    export_<YYYY-MM-DD>.csv # copia inmutable de cada export integrado (historia para recrear)
  runs\
    Riesgo_<ACTIVO>_<codigo>_<YYYY-MM-DD>.md      # reporte legible
    configs_<...>_<YYYY-MM-DD>.csv                # metricas de TODAS las configs probadas
    heatmap_<...>_<YYYY-MM-DD>.png                # mapa de calor Top-N
    recomendacion_<...>_<YYYY-MM-DD>.json         # config elegida (params aplicables)
```
Reusa los HOLC existentes en `C:\NTEXECG\HOLC\<ACTIVO>_5m.csv` (solo lectura).

## 4. Scripts sugeridos (módulos)
| Script | Responsabilidad |
|---|---|
| `nt_riesgo.py` | **Motor / CLI** (`integrar`, `calcular`, `recrear`, `estado`). Orquesta todo. |
| `io_export.py` | Parseo del export LuxAlgo (empareja filas Entrada/Salida, extrae MAE/MFE/PnL/dur/lado). Glob por `®`. **Merge con dedup por clave estable** (ver §9). |
| `atr.py` | Carga HOLC, calcula **ATR(14) Wilder/RMA**, mapea ATR a la barra de entrada. **Valida cobertura** del HOLC vs rango del listado; marca trades con ATR estimado. |
| `metrics.py` | Métricas base: PnL, PF, WinRate, MaxDD (high-water mark), pérdida bruta, peor trade, por-lado, por-sesión. |
| `sims.py` | Simuladores de los estudios que **dan valor** (§5). Cada uno devuelve {config, métricas}. |
| `robustez.py` | Walk-forward: parte el master en 2, optimiza in-sample / mide out-of-sample. Devuelve el número de confianza. |
| `report.py` | Renderiza el `.md`, el `configs.csv`, el `heatmap.png` y el `recomendacion.json`. |
| `cfg_<ACTIVO>.json` | Config por instrumento: $/punto, ruta HOLC, glob símbolo, periodo ATR, rejillas de parámetros. |

## 5. Motor de cálculo — estudios INCLUIDOS (los que dieron valor)
Validados en el estudio ES de referencia. Todos derivan de [export + ATR de entrada].
1. **Distribución MAE→ATR** (suelo del SL): percentiles del retroceso que aguantan las ganadoras.
2. **Backstop catastrófico en $ fijos** (barrido): nivel óptimo en $, pts y ×ATR; nº trades que toca; ahorro. *(Núcleo de riesgo.)*
3. **Escalera de entradas por MAE** (laddering): profundidades óptimas; participación; PnL/fill.
4. **TP asimétrico** (MFE): TP largo profundo vs TP corto para reversión; give-backs.
5. **Asimetría Long/Short**: PF/net/DD por lado; ¿el motor es largo?; ¿cortos rentables o a recortar?
6. **Comparativa de configs** (Top-N) con score compuesto.
7. **Robustez walk-forward** sobre cada config candidata.

**Gating de "dar valor" (automático):** una config entra a la recomendación solo si (a) **supera la línea base** en el score, y (b) **sobrevive OOS**. Los estudios net-negativos (p.ej. SL duro ×ATR) o sin ganancia (filtro de sesión) se **muestran marcados como "descartado – no aporta"**, no se recomiendan. Parámetros que no generalizan (p.ej. TP corto) se presentan como **rango, no decimal**.

## 6. Métricas exactas (definiciones — que Fable5 no improvise)
- **Total PnL** = Σ PnL de trades (en $ del contrato configurado).
- **Profit Factor (Factor de ganancia)** = ganancia bruta / |pérdida bruta|.
- **WinRate** = trades ganadores / trades totales.
- **Max Drawdown ($)** = mayor caída pico-valle de la equity acumulada.
- **Max DD %** = Max DD$ / **pico de equity (high-water mark)** del periodo *(convención NTEXECG ya establecida; evita el blow-up del % relativo a pico local).*
- **Operaciones rentables** = nº de trades con PnL > 0 (y su %).
- **Pérdida bruta** y **peor trade** (para control de riesgo).
- **$/punto** por instrumento: ES=50 (MES=5), NQ=20 (MNQ=2), RTY=50 (M2K=5), etc. — verificar contra `Tamaño de la posición (valor)`/precio del export.

## 7. Reporte + mapa de calor (estructura del `.md`)
1. **Cabecera**: activo, código, nº trades, rango fechas, **cobertura HOLC** (✓/⚠ truncado), bandera N-bajo.
2. **Línea base (listado crudo)**: Total PnL · Max DD · Trades · Operaciones rentables · WinRate · Profit Factor · Peor trade · Pérdida bruta.
3. **Análisis de control de riesgo**: distribución MAE→ATR · backstop óptimo · asimetría L/S · give-backs.
4. **Configuraciones sugeridas — Top 5/10 en MAPA DE CALOR**: filas = configs (backstop/escalera/TP), columnas = métricas (PnL, PF, MaxDD, WinRate, Peor), celdas coloreadas por rank. PNG (`matplotlib`) + tabla equivalente en el `.md`.
5. **Comparativa vs línea base**: Δ de cada métrica por config.
6. **Robustez (OOS)**: PF in-sample vs out-of-sample + bandera de confianza.
7. **Recomendación**: config elegida, racional, y el **número de confianza OOS** (no el in-sample).

## 8. Entregables por corrida
`Riesgo_*.md` (informe) · `configs_*.csv` (todas las métricas) · `heatmap_*.png` · `recomendacion_*.json` (params aplicables al Backtester/ejecución) · `manifest.json` (reproducibilidad).

## 9. Gotchas / validaciones que Fable5 DEBE implementar
1. **Integrar = SOBRESCRIBIR (no fusionar).** Cada export es el histórico completo → reemplaza el master. No hay merge incremental ni dedup por trade. *Validación opcional de seguridad:* avisar si el export nuevo NO es superconjunto del anterior (podría ser un export parcial/erróneo) comparando por clave `(entry_time, side, entry_price)`. No bloquear, solo advertir. El nº de trade de LuxAlgo **renumera** en cada export → no usarlo como identidad estable; usar la clave temporal para las validaciones y para casar snapshots.
2. **Cobertura HOLC.** Si el HOLC no llega al último trade → esos trades usan ATR estimado (última barra). **Avisar en el reporte** y marcar los trades afectados. *(Ya pasó en ES: HOLC truncado al 22-jun.)*
3. **Determinismo total.** Nada de aleatoriedad ni timestamps internos que rompan `recrear`. El timestamp de la corrida se pasa explícito o se toma del nombre de archivo/manifiesto.
4. **N bajo.** Si trades < ~80 o mitades de walk-forward < ~40 → bandera "robustez frágil".
5. **Reportar OOS, no IS.** El número destacado siempre el fuera de muestra.
6. **Hallazgos por estrategia.** No trasladar conclusiones de ES (p.ej. asimetría L/S) a otras; cada listado se estudia solo.
7. **Horas en ET.** El export ya viene en America/New_York; no reconvertir.
8. **Python `py -3`** (3.14); PowerShell no disponible → usar Bash. `python`/`python3` son stubs.

## 10. Fases de desarrollo sugeridas (para Fable5)
- **F1 – Ingesta y master**: `io_export.py` + `atr.py` + `integrar`/`estado`. Validar merge/dedup y cobertura HOLC con ES.
- **F2 – Métricas y línea base**: `metrics.py` + reporte mínimo (solo baseline). Cuadrar contra el export (PnL acumulado debe coincidir).
- **F3 – Estudios de valor**: `sims.py` (backstop, escalera, TP, L/S) + gating vs baseline.
- **F4 – Robustez**: `robustez.py` (walk-forward) integrado a cada config.
- **F5 – Reporte + heatmap**: `report.py` (md + csv + png + json).
- **F6 – Recrear + manifiesto**: reproducibilidad end-to-end.
- **Validación de aceptación**: correr el motor sobre el listado ES 2026-07-04 y **reproducir los hallazgos que obtuvimos a mano** (backstop ~$5k/100pts, motor-largo, PF OOS ~3.5–4).

## Referencia
Estudio ES completo y hallazgos validados: memoria `ntexecg-es-ladder-backstop`. Pipeline base y convenciones: `ntexecg-pipeline`.
