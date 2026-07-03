# PROMPT para Claude Code — Revisión de arquitectura NTEXECG

> Pega todo lo que sigue en Claude Code (con Fable 5). Es una revisión de **solo
> lectura**: no debe modificar código, ni hacer commit/push en esta pasada.

---

## Rol
Actúa como **arquitecto de software e ingeniero senior**. Vas a auditar el proyecto
**NTEXECG** buscando **redundancias, duplicaciones, código muerto/desconectado** y,
sobre todo, verificar que **todo esté conectado y sea coherente con el objetivo del
sistema**. Entrega un reporte priorizado con evidencia real (ruta:línea). **No
modifiques nada** en esta pasada; primero diagnóstico y plan.

## Objetivo de NTEXECG (tu ancla — todo se juzga contra esto)
NTEXECG es un **gateway fail-closed de señales de trading** entre LuxAlgo/TradingView
(alertas por webhook) y TradersPost. Recibe cada señal, la **normaliza, deduplica,
valida y filtra** por un pipeline de 5 niveles, la **enriquece** (SL/TP por ATR,
score de calidad, régimen, entrada escalonada, perfiles de riesgo) y solo entonces la
**despacha** a TradersPost, dejando **auditoría y observabilidad** completas. Opera
**solo en paper/demo** y por **un único camino**. La entrada es **fail-closed**: una
entrada sin SL calculable se BLOQUEA, nunca se despacha desnuda.

## Invariantes que NO se deben romper (si algo los viola, es P0)
- **Paper/demo únicamente.** Un solo camino LuxAlgo → NTEXECG → TradersPost.
- **Fail-closed para entradas:** sin `sl_price` → BLOCK (nunca orden sin stop).
- **Kill-switch por capas:** `dry_run` (cualquier nivel lo activa) y
  `traderspost_enabled` (global AND estrategia). Envío real solo con todos abiertos.
- **Despliegue:** se hace `git push` desde NTDEV; el servidor solo **consume**. No
  ejecutes comandos que envíen a TradersPost ni toques cuentas reales.

## Rutas (léelas en este orden)
**Documentación (primero, para internalizar el objetivo y la arquitectura):**
- `CONTRATO/MEMORIA_TECNICA_NTEXECG_v1_0.md` (visión general, §9 arquitectura)
- `CONTRATO/03_ARQUITECTURA_ESTRUCTURA_v1_0.md`
- `CONTRATO/07_INFRAESTRUCTURA_ENTORNOS_v1_0.md`
- `CONTRATO/00_*`..`14_*` (contrato completo)
- `CONTRATO/Anexo_21..25*.md` (calibración de filtros, comparativos, playbook,
  batería de pruebas, y el **estándar de calidad de señal** del Anexo 25)

**Código:**
- `app/main.py` (arranque, routers, lifespan/jobs)
- `app/api/` — `webhooks_luxalgo.py` (ingreso, `process_signal`, `_dispatch_approved`),
  `health.py`, `auth_routes.py`
- `app/services/` — `filter_pipeline.py`, `config_resolver.py`, `payload_builder.py`,
  `dispatch_profiles.py`, `sl_tp_calculator.py`, `market_data_service.py`,
  `bar_store.py`, `symbol_mapper.py`, `position_service.py`, `exit_manager.py`,
  `forced_exit.py`, `traderspost_client.py`, `audit_service.py`, `repositories.py`,
  `session_validator.py`, `performance_tracker.py`, `results_import.py`,
  quality scorer / hmm service
- `app/models/` — `strategy.py`, `strategy_profile.py`, `decision.py`,
  `normalized_signal.py`, `raw_signal.py`, `webhook_delivery.py`, `position_state.py`,
  `ohlcv_bar.py`, `symbol_map.py`, `market_data_status.py`, perfiles global/asset, audit
- `app/web/` — `routes_dashboard.py`, `routes_strategies.py`, `routes_signals.py`,
  `routes_analytics.py`, `routes_positions.py`, `routes_assets.py`,
  `routes_symbol_map.py`, `routes_api.py`, `routes_strategy_templates.py`,
  `routes_settings.py`, `routes_audit.py`, `common.py`
- `app/templates/` — `base.html`, `dashboard.html`, `signal_detail.html`,
  `signals.html`, `strategy_detail.html`, `analytics.html`, `_macros.html`, `partials/`
- `app/core/` — `config.py`, `logging.py`, `auth_middleware.py`, `scheduler.py`
- `scripts/` — todos (dispatch/config/estudios: `manage_profiles.py`,
  `manage_strategies.py`, `set_scale_execution.py`, `apply_quality_filter.py`,
  `eval_quality_filters.py`, `eval_strategy_battery.py`, `pullback_timing.py`,
  `check_leg_touch.py`, `trade_forensics.py`, `show_signal_filters.py`,
  `simulate_webhook.py`, `audit_signal_flow.py`, etc.)
- `tests/`, `alembic/` (migraciones)

**Carpetas a evaluar como posible legado/redundancia (¿siguen aportando?):**
`A REVISAR/`, `FASES_ANTERIORES/`, `PROMPTS/`, `DOCS/`, `ClaudeCodeTV_290626/`,
`NINJATRADER/`.

## Qué revisar (checklist)
1. **Coherencia con el objetivo.** Traza el flujo **end-to-end**: webhook → RawSignal →
   normalización/dedupe → FilterPipeline (L1 sistema, L2 temporal+staleness, L3 riesgo,
   L4 calidad+régimen, L5 SL/TP) → decisión → `_dispatch_approved` (multi-perfil) →
   PositionState → UI/analítica/audit. ¿Cada módulo aporta al gateway fail-closed?
   ¿Hay pasos que sobran o que rompen el propósito?
2. **Redundancias / duplicaciones.** Lógica repetida (p. ej. parsers de
   cantidades/niveles en `scripts` y en `routes_strategies`), config duplicada (UI vs
   scripts vs ConfigResolver), scripts que hacen lo mismo, docs solapados, y
   **strategy_id legacy** en decisiones históricas.
3. **Conexión / consistencia.** ¿Todo lo definido se usa? Código muerto, imports
   huérfanos, rutas no enlazadas, columnas de DB sin uso, claves de config **leídas
   pero nunca escritas** (o al revés). Divergencias entre lo que la UI guarda y lo que
   ConfigResolver lee.
4. **Correctitud y seguridad del pipeline.** Fail-closed real (invariante de
   `SLTPCalculator`/`PayloadBuilder`); dedupe robusto; kill-switch por capas; manejo de
   errores/reintentos en `traderspost_client`; idempotencia; zonas horarias; salidas
   forzadas.
5. **Estado de posición.** Es **estimado**, sin reconciliación con el broker (si el SL
   se llena del lado del broker, NTEXECG no se entera). Evalúa el riesgo y **propón un
   diseño de reconciliación**.
6. **Riesgo por portafolio.** Falta la regla "una posición por símbolo entre
   estrategias" (caso: dos ES sobre MES). Propón implementación (`symbol_busy` en L3).
7. **Calidad de señal (Anexo 25) — PENDIENTE CONOCIDO A RESOLVER.** Defecto de diseño
   actual: **la calificación PARTE en 100** (`score = 100` por defecto en
   `FilterPipeline`, y `QualityScorer.score` devuelve 100 cuando no hay filtros). Eso
   hace que una señal **sin ninguna medición** aparezca como "perfecta" y pase como si
   fuera de alta calidad — es exactamente lo que causó la entrada MNQ sin contexto.
   **Regla a implementar:** la calificación **NO debe empezar en 100**; sin **filtros
   reales activos** no puede valer 100. El estado por defecto debe ser **UNKNOWN**
   (calidad no medida), y el 100 solo alcanzable cuando hay filtros que lo justifiquen.
   Revisa dónde se inicializa/propaga el score (`filter_pipeline.py`, `quality_scorer`,
   `payload_builder` extras, UI), confirma si ya existe `quality`/`filters_active`, y
   **propón la implementación**: default UNKNOWN (o partir neutral, no en 100),
   etiqueta `ntexecg_quality ∈ {UNKNOWN, LOW, MEDIUM, HIGH}`, y que la UI muestre
   UNKNOWN sin el ✅ verde. No basta con documentarlo: debe quedar como hallazgo con
   plan de corrección.
8. **Observabilidad.** Analítica, cinta de filtros, audit: ¿huecos? ¿falta trazar algo
   crítico (p. ej. fills reales)?
9. **Tests.** Cobertura de los caminos críticos: fail-closed, dispatch multi-perfil,
   dedupe, kill-switch, escalonado, perfiles. Qué falta y qué tests agregarías.
10. **Deuda técnica.** N+1 queries (p. ej. bucles que consultan por señal), manejo de
    sesiones async, higiene de migraciones, dependencias, logging/secretos en `.env`.
11. **Feature de perfiles de riesgo (`dispatch_profiles.py`).** Casos borde: dedupe por
    webhook, herencia base↔perfil, cap `max_contracts`, override de SL/TP, reconciliación
    de estado por perfil/cuenta.

## Validación CRÍTICA — la pestaña "Config" de cada estrategia, end-to-end
Cada estrategia tiene **su propia configuración** en la pestaña **Config** de la UI.
Debes verificar que **cada control/campo** de esa pestaña esté **realmente conectado y
funcionando**, entendiendo que **solo actúa lo que está activo en esa estrategia**: un
filtro desactivado debe ser un **no-op** (no un "always-on" ni un "always-off"
accidental), y uno activado debe correr y poder afectar/bloquear la decisión.

Para **CADA sección/campo** de la pestaña Config —identidad/modo/webhook; despacho
(kill-switch `dry_run` / `traderspost_enabled`); **SL/TP por ATR**; **ventana de
sesión**; **frescura (staleness)**; **guardarraíles**; **filtros de calidad
(QualityScorer)**; **régimen (HMM)**; **compras escalonadas**; **perfiles de riesgo**—
traza la **cadena completa** y confirma que existe y es coherente en cada eslabón:

`UI (campo del form) → ruta que guarda (app/web/routes_strategies.py) → clave
persistida (StrategyProfile / pipeline_config_json) → ConfigResolver la lee → consumidor
real (FilterPipeline nivel N / dispatch_profiles / PayloadBuilder / SLTPCalculator) →
efecto observable en la decisión y/o en el payload`

Reporta, por cada campo:
- ¿Se **guarda** donde debe? (¿la ruta escribe la clave correcta?)
- ¿ConfigResolver lo **lee**? (¿la clave que lee es **exactamente** la que se escribe?
  caza desajustes de nombre entre UI/route/resolver/pipeline.)
- ¿Algún **consumidor** lo usa de verdad, o queda definido pero **nunca aplicado**?
- ¿Respeta **activo/inactivo por estrategia**? (OFF → no altera la decisión; ON → corre.
  Recuerda el estándar del Anexo 25: `filters` vacío → score 100 por defecto = **UNKNOWN**,
  no HIGH.)
- ¿Hay un **test** que lo cubra? Si no, propónlo.

Marca en **🔴 ROJO** cualquier control de la pestaña Config que: (a) se guarde pero
**nunca se lea**, (b) se lea pero **nunca se consuma**, (c) tenga la **clave desalineada**
entre UI/route/resolver/pipeline, o (d) aparezca "activo" en la UI pero **sin efecto**
en el pipeline (o al revés). Verifícalo con evidencia `ruta:línea`, no por inspección
superficial de la plantilla.

## Método
- Lee primero los docs de objetivo/arquitectura y construye un **modelo mental** del
  sistema antes de opinar.
- **Verifica cada afirmación con evidencia real** (grep/lectura), citando `ruta:línea`.
  No inventes; si algo no está claro o falta contexto, dilo explícitamente.
- Distingue "por diseño" (documentado) de "deuda/accidente".

## Entregable (escríbelo en `CONTRATO/REVISION_ARQUITECTURA_<fecha>_<modelo>.md`)
> En la **primera línea** del reporte indica el **modelo/identificador con el que estás
> corriendo** (verifícalo con `/model` antes de empezar; p. ej. `claude-fable-5`). Usa ese
> mismo `<modelo>` en el nombre del archivo para no sobrescribir revisiones previas.
1. **Resumen ejecutivo:** ¿el sistema es coherente con su objetivo? (sí/no + por qué,
   3–6 líneas).
2. **Hallazgos por severidad** — P0 (rompe fail-closed / paper-only / seguridad) → P1
   (correctitud/riesgo) → P2 (deuda/mantenibilidad) → P3 (nits). Cada hallazgo con:
   evidencia (`ruta:línea`), impacto, y **recomendación concreta**.
3. **Mapa de redundancias/duplicaciones** (tabla: qué se duplica, dónde, cómo unificar).
4. **Matriz de trazabilidad de la pestaña Config** (una fila por campo):
   `campo (UI) | ruta que guarda | clave persistida | ConfigResolver la lee | consumidor
   real | efecto | respeta ON/OFF | estado ✅/🔴 | test`. Esta matriz es obligatoria y es
   la evidencia de que "todo lo de Config está conectado y funcionando".
5. **Código muerto / desconectado** (lista).
6. **Ideas de mejora adicionales** más allá del checklist (arquitectura, resiliencia,
   escalabilidad, DX, seguridad).
7. **PLAN DE ACCIÓN CONSOLIDADO (backlog priorizado)** — la sección más importante.
   Fusiona en **una sola lista ordenada** TODO: (a) cada punto que te pedí en este prompt
   y (b) **tus propios hallazgos**. Una fila por ítem, con este formato, lista para
   atacar **uno por uno**:

   `ID (NX-01, NX-02…) | título | severidad (P0–P3) | área/archivo | evidencia
   (ruta:línea) | propuesta concreta | criterio de aceptación | dependencias | esfuerzo
   (S/M/L) | riesgo de romper invariantes | estado (⬜/🟨/✅)`

   Reglas del backlog:
   - Ordénalo por **prioridad y bajo riesgo primero** (quick wins seguros arriba; los
     cambios que tocan dispatch/fail-closed van con más cuidado y tests).
   - Incluye como ítems propios los **pendientes conocidos**: `score ≠ 100` /
     calidad UNKNOWN (Anexo 25), reconciliación con el broker, riesgo por portafolio
     (symbol_busy / caso ES-MES), `cancel_after` por estrategia.
   - Cada ítem debe ser **auto-contenible** (se puede hacer y verificar por separado),
     con **criterio de aceptación** claro y, si aplica, el **test** que lo valida.

## Cierre
- **No modifiques código ni hagas commit/push en esta pasada.**
- Deja el reporte completo (incluido el backlog del punto 7) en
  `CONTRATO/REVISION_ARQUITECTURA_<fecha>.md`.
- Termina resumiendo el backlog y **espera mi aprobación**. A partir de ahí
  **atacaremos los ítems uno por uno**, en orden: por cada ítem harás el cambio, sus
  tests, y esperarás mi visto bueno antes de pasar al siguiente. No agrupes varios
  ítems en un mismo cambio salvo que yo lo pida.
