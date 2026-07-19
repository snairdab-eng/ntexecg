# Pestaña Activos + Ventana de operación en el estudio · 2026-07-07

> Evaluación del arquitecto saliente (Fable) para que **Opus 4.8** implemente.
> Patrón de referencia: pestaña Riesgo (identidad del dato, fuente única,
> honestidad de recomendaciones, fail-closed, tests).

## A. Evaluación de la pestaña Activos (verificado en producción)

**Qué ES esta pestaña** (su función legítima): la capa MEDIA de la herencia
de configuración `GlobalProfile < AssetProfile < StrategyProfile`. Por
activo define: ventana de sesión (la consume el L2 Temporal vía herencia),
SL/TP ×ATR de fallback, atr_timeframe y score_minimum. NO debe retirarse:
el L2 y el ConfigResolver la usan. Pero como está, no informa:

1. **La mitad de las filas son ruido**: 8 activos "sin estrategias" (los
   contratos grandes 6E/6J/CL/ES/GC/NQ/RTY/YM — todo se opera en micros).
2. **Defaults que ya nadie hereda**: "SL ATR 2.0x ⚠" en casi todos, pero
   HOY las 7 estrategias tienen bracket propio del estudio
   (backstop/tp_nominal en su pipeline_config_json) → el SL/TP del activo
   es letra muerta y la tabla no lo dice.
3. **Columnas vacías**: TP ATR "—" en todos; ATR TF "—" en casi todos.
4. **Bug visual**: "paperpaper ⚠ 2 estrategias" (statuses concatenados sin
   separador en MES).
5. **Sin conexión con la realidad operativa**: la ventana del activo genera
   bloqueos N2 reales (9 en los últimos 7 días = 35% de los bloqueos) y la
   pestaña no muestra ni cuáles estrategias heredan su ventana ni cuánto
   bloquea.
6. Lo que NO duplica (bien): tick_size/tick_value viven en Symbol Mapper
   (fuente única, Anexo 08 #4) — mantenerlo así, solo enlazar.

**Veredicto: rediseñar, no retirar.** Convertirla en la vista de
"herencia con datos reales": qué hereda quién, qué default está vivo o
muerto, y cuánto bloquea cada ventana.

## B. Contexto para la ventana de operación (petición del operador)

El operador pide que **el estudio del Motor de Riesgo arroje la ventana de
operaciones recomendada según cómo se comportaron los trades**. Dos hechos
del sistema que el implementador DEBE respetar:
- El **filtro de sesión/hora como palanca de edge fue DESCARTADO por
  diseño** ("no aporta", validado 2026-07-04, `descartados_por_diseno`).
  La recomendación de ventana NO es para mejorar la señal.
- La filosofía vigente es **participación 100%** (R-obs-2): capar pérdidas
  SIN saltar señales. Una ventana L2 más angosta que las horas donde el
  backtest generó sus trades RECORTA participación — eso es exactamente lo
  que hay que hacer visible (los 9 bloqueos N2 de esta semana pueden ser
  señales del edge que la ventana está tirando).
- El dato ya existe: el enriched del motor trae `sesion` y `hora_et` por
  trade (`scripts/nt_riesgo._ENRICHED_COLS`), y la ventana VIGENTE por
  estrategia sale de `readable_window(session_config_json)` efectivo.

---

## C. 📋 PROMPT PARA OPUS 4.8 — LOTE ACT-1: pestaña Activos conectada a la realidad

Eres el implementador de NTEXECG (FastAPI + Jinja2/HTMX; solo paper/demo).
Archivos: `app/web/routes_assets.py`, `app/templates/assets.html` (+ el
detalle/form si existe), tests `tests/test_asset_profiles_ui.py`. NO
commit/push. Verifica con `.venv\Scripts\python.exe -m pytest -q`
(cuelgue flaky → `-o faulthandler_timeout=300 --timeout=600`).

Contexto: la pestaña es la capa media de herencia (ventana L2 + SL/TP/score
fallback). Problemas verificados: 8/16 filas sin estrategias, defaults de
SL/TP que ninguna estrategia hereda ya (todas tienen bracket propio del
estudio en pipeline_config_json), columnas siempre vacías, "paperpaper"
concatenado, y cero conexión con los bloqueos N2 que su ventana produce.

Tareas:
1. **Partir la tabla en dos**: "Activos EN USO" (con estrategias) arriba y
   "Sin estrategias" colapsado en un `<details>` abajo — el ruido fuera de
   la vista principal sin perder el alta/edición.
2. **Columna "Herencia efectiva"** por activo: para cada estrategia del
   activo, computa si su SL/TP realmente hereda del asset o está
   overrideado (StrategyProfile.sl_atr/tp_atr o
   pipeline_config_json.backstop_points/tp_nominal_* presentes → NO
   hereda). Muestra "SL/TP: 0/2 heredan (bracket propio del estudio)" y
   marca el default del activo como `fallback sin herederos` en gris
   cuando nadie lo usa. Lo mismo para la ventana: "ventana: 2/2 heredan"
   vs "override propio".
3. **Ventana con datos reales**: junto a la ventana del activo, el número
   de bloqueos N2 Temporal del rango (query a StrategyDecision:
   block_level == 2, strategy_id de las estrategias del activo, últimos
   7 días) con link a la pestaña Señales filtrada. Una ventana que
   bloquea señales debe verse aquí.
4. **Limpieza**: fix del render "paperpaper" (join con separador y dedupe);
   columnas TP ATR / ATR TF: muéstralas solo si algún activo tiene valor
   (o "—" con tooltip "sin calibrar — hereda global"); elimina del
   template los placeholders de campos que NO existen en el esquema
   (scale_entry_* — el docstring del route ya lo advierte); agrega link
   "contrato/ticks → Symbol Mapper" en vez de duplicar datos.
5. Tests: partición en uso/sin uso; herencia efectiva (heredan vs bracket
   propio); conteo N2 por activo; el fix del render; placeholders fuera.

Invariantes: el esquema de AssetProfile NO cambia (solo lectura/render y
queries); la herencia del ConfigResolver no se toca; Symbol Mapper sigue
siendo la fuente única de tick data. "LISTO PARA COMMIT" solo con suite
verde + `git diff --stat`.

---

## D. 📋 PROMPT PARA OPUS 4.8 — LOTE RIES-W: ventana de operación en el estudio

Mismo marco operativo. Archivos: `scripts/nt_riesgo.py` (o
`scripts/mr_sims.py` si encaja mejor — decide y justifica),
`scripts/mr_report.py`, `app/web/routes_riesgo.py`,
`app/templates/riesgo.html`, tests (`tests/test_robs2.py` o nuevo).
⚠ Tocas el MOTOR: tras deploy hay que recalcular los 7 estudios (botón
Calcular por estrategia) para persistir la sección nueva; los estudios
viejos sin el campo NO deben romper la ficha (guarda con `{% if %}`).

Contexto (léelo dos veces): el operador quiere que el estudio arroje la
**ventana de operaciones recomendada según el comportamiento de los
trades**. PERO el filtro de sesión como palanca de edge está DESCARTADO
por diseño ("no aporta", validado 2026-07-04) y la filosofía es
participación 100% (capar pérdidas sin saltar señales). Por tanto la
sección es de **COBERTURA, no de filtrado**: su recomendación por defecto
es la ventana MÍNIMA que cubre los trades del backtest (para no dejar
señales fuera), nunca un recorte para "mejorar" la señal — salvo que un
recorte pase el gate OOS como cualquier palanca, igual que todo el motor.

Tareas:
1. **Estudio** (en `run_studies` o en `_listado_crudo` extendido — el dato
   viene del master enriched: `sesion` y `hora_et` por trade): sección
   `ventana_operacion` con:
   - distribución por sesión ET (asia/europa/rth/overnight — reusa
     `sesion_et`): n, net_usd, pf, peor_trade, y % de los trades ROJOS
     (pérdida ≥ umbral de alarma de la cuenta) que cae en cada sesión;
   - rango horario observado de ENTRADAS (min–max hora_et + p05–p95) por
     lado y total;
   - `ventana_minima_cobertura`: días + horas ET que cubren el 100% de las
     entradas del backtest (y la variante p95 como referencia);
   - honestidad: campo `nota` recordando que el filtro de sesión no aporta
     edge (descartado) — esta ventana es de cobertura.
2. **Comparación con la ventana VIGENTE**: en la ficha (routes_riesgo),
   junto a la sección nueva, resuelve la ventana L2 efectiva de la
   estrategia (ConfigResolver / readable_window) y computa
   `pct_trades_fuera`: % de trades del backtest cuya entrada cae FUERA de
   la ventana vigente. Si > 0, banner ámbar: "tu ventana actual dejaría
   fuera el X% de los trades del backtest (participación perdida) —
   ventana mínima de cobertura: <días/horas>". Si 0: "✓ tu ventana cubre
   el 100% de los trades observados".
3. **Reporte md** (`mr_report.py`): tabla por sesión + la ventana mínima
   de cobertura + el % fuera si se puede resolver (si el reporte no tiene
   acceso a la ventana vigente, solo la parte del estudio).
4. **Ficha** (`riesgo.html`): sección "⏰ Ventana de operación" debajo del
   rango por lado (R-obs-2), con la tabla por sesión compacta, la ventana
   mínima, y el banner de cobertura. Estudios viejos sin el campo → la
   sección no aparece (sin errores).
5. **NO aplicar automático**: la ventana NO entra en `_activacion_json` ni
   en el botón Aplicar (cambiar ventana es config sensible de L2 — se
   aplica a mano en Estrategias/Activos). Solo informar con precisión.
6. Tests: estudio con trades sintéticos en 2 sesiones (distribución,
   ventana mínima, p95); pct_trades_fuera con ventana vigente angosta y
   con 24h; ficha con y sin el campo; reporte md.

Invariantes: determinismo del motor (recrear bit-a-bit debe seguir
pasando — la sección se persiste en el estudios json como todo lo demás),
paridad ficha=reporte, participación 100% como criterio, descarte del
filtro de sesión respetado y citado. "LISTO PARA COMMIT" solo con suite
verde. Recuerda en tu resumen final que el operador debe RECALCULAR los 7
estudios tras el deploy.

---

## E. Orden

ACT-1 y RIES-W son independientes (deployables por separado). Si se hacen
ambos: RIES-W primero (el estudio manda; la pestaña Activos puede luego
enlazar la ventana mínima de cobertura de cada estrategia bajo su activo —
mejora opcional de segunda pasada).
