# REVISIÓN DE ARQUITECTURA — NTEXECG

**Fecha:** 2026-07-02 · **Alcance:** solo lectura (sin cambios de código)
**Método:** lectura completa del pipeline (`app/`), scripts, tests, plantillas y contrato
(`CONTRATO/`), verificando cada afirmación con evidencia `ruta:línea`.

---

## 1. RESUMEN EJECUTIVO

**¿El sistema es coherente con su objetivo? Sí, con reservas serias.** El camino único
LuxAlgo → NTEXECG → TradersPost existe y es el único; el fail-closed de entradas se cumple
(sin SL calculable → BLOCK en L5, y `PayloadBuilder` lanza `ValueError` como segunda
barrera); el kill-switch de despacho por capas (env AND global AND estrategia, dry_run OR)
está bien implementado y testeado. **Pero** hay un defecto que anula el freno global de
modo (`paused`/`flatten_only` nunca bloquea porque el `mode` del StrategyProfile lo pisa),
el filtro de frescura (staleness) es un no-op porque `signal_ts` se fija a la hora de
recepción, el Nivel 3 de riesgo es casi todo stub (los guardarraíles monetarios/conteo
configurables en UI no se aplican), y la calificación de calidad parte en 100 (una señal
sin medición aparece como perfecta — el caso MNQ). El estado de posición es estimado, se
comparte entre estrategias del mismo símbolo y no se reconcilia con el broker.

---

## 2. HALLAZGOS POR SEVERIDAD

### P0 — Rompe un control de seguridad del sistema

**P0-1 · El modo global `paused`/`flatten_only` NO bloquea nada (clave `mode` pisada).**
- Evidencia: `app/services/config_resolver.py:203` — al mergear el StrategyProfile se hace
  `updates = {"mode": strategy_profile.mode, ...}` **incondicionalmente**. `GlobalProfile.mode`
  ∈ {normal, defensive, flatten_only, paused} (semántica: freno del sistema,
  `app/models/global_profile.py:49-50`) y `StrategyProfile.mode` ∈ {paper, micro,
  limited_live, live} (semántica: madurez de la estrategia,
  `app/models/strategy_profile.py:34-35`) **comparten la misma clave** en el config plano.
  El chequeo L1.1 (`app/services/filter_pipeline.py:286-289`) lee `config["mode"]`, que para
  toda estrategia con perfil (todas) vale "paper" → `paused`/`flatten_only` global **jamás
  bloquea entradas**.
- El test existente no lo detecta porque inyecta el config a mano sin pasar por el resolver
  (`tests/test_filter_pipeline.py:111` → `config = {"mode": "paused"}`).
- Impacto: el botón de emergencia "pausar el sistema" de `/ui/settings`
  (`app/web/routes_settings.py:66-67`) es inoperante. Los otros dos candados (dry_run,
  traderspost_enabled) sí funcionan, por eso no hay envío real fuera de control, pero un
  control de seguridad documentado (doc 00 §8, L1.1) está muerto.
- Recomendación: separar claves — p. ej. `global_mode` (sistema) vs `execution_mode`
  (madurez) — y test end-to-end resolver→pipeline con GlobalProfile.mode=paused.

### P1 — Correctitud / riesgo

**P1-1 · Calidad parte en 100: señal sin medición = "perfecta" (Anexo 25 §1-bis, pendiente conocido).**
- Evidencia: `app/services/filter_pipeline.py:69` (`score: int = 100` en `PipelineResult`),
  `:180` (`score = 100`), `:212` (`execution["level_4"] = {"score": score, "passed": passed}`
  sin `filters_active`); `app/services/quality_scorer.py:126-127` (sin filtros activos →
  `return 100`). La UI muestra "Score 100 — ≥ mínimo, pasó" con estilo de aprobación
  (`app/templates/signal_detail.html:137-139`) y el payload lleva `ntexecg_score: 100`
  (`app/services/payload_builder.py:79`) sin distinción.
- Impacto: exactamente lo que causó la entrada MNQ sin contexto: una estrategia sin filtros
  reporta calidad máxima. Además `avg_score` del performance tracker
  (`app/services/performance_tracker.py:56`) queda inflado con 100s por defecto.
- Recomendación (plan de corrección, no solo documentar):
  1. En L4 calcular `filters_active = bool(filters con enabled+weight>0) or regime.enabled`
     y escribirlo en `execution["level_4"]`.
  2. Etiqueta `ntexecg_quality ∈ {UNKNOWN, LOW, MEDIUM, HIGH}` (UNKNOWN si
     `filters_active=false`; LOW se bloquea; MEDIUM/HIGH por umbral) propagada a
     `StrategyDecision` (columna o dentro de `pipeline_execution_json`) y a
     `payload.extras`.
  3. UI: `signal_detail.html`/`signals.html`/`dashboard.html` muestran UNKNOWN en
     gris/ámbar, nunca el ✅ verde para score-100-sin-filtros.
  4. `avg_score` excluye señales UNKNOWN (o se guarda aparte).
  5. Regla de promoción: no pasar a real una estrategia cuya calidad sea UNKNOWN.

**P1-2 · Staleness es un no-op: `signal_ts` = hora de recepción, no la del payload.**
- Evidencia: `app/services/signal_normalizer.py:130` — `signal_ts=datetime.now(timezone.utc)`.
  El payload de TradingView trae `time`, que se guarda como `RawSignal.time_raw`
  (`app/api/webhooks_luxalgo.py:495`) y **nunca se parsea ni se usa**. El chequeo
  `_check_staleness` (`app/services/filter_pipeline.py:361-381`) mide `now - signal_ts`
  ≈ milisegundos (mismo proceso), así que `signal_max_age_entry_seconds` — configurable en
  la UI de guardarraíles (`app/templates/strategy_detail.html:399-411`) — nunca dispara.
  Igual el "entry staleness" del cliente (`app/services/traderspost_client.py:98-105`).
- Impacto: una alerta entregada tarde por TradingView (reintentos, colas) entra como fresca
  y se ejecuta a un precio ya viejo. El control aparece "activo" en UI sin efecto → 🔴.
- Recomendación: parsear `payload["time"]` → `signal_ts` (con fallback a now si falta),
  guardar `received_ts` aparte, y test con señal artificialmente vieja.

**P1-3 · PositionState compartido por (account_id, symbol): dos estrategias sobre MES se pisan; no hay `symbol_busy` ni bloqueo de re-entrada.**
- Evidencia: la búsqueda ignora `strategy_id` — `app/services/position_service.py:160-170`
  y `app/services/repositories.py:108-114` filtran solo por `account_id` + `symbol`
  (constraint `uq_position_account_symbol`,
  `app/db/migrations/versions/20260615_..._initial_schema.py:161`). `account_id` **nunca se
  escribe** en el config (siempre cae al default `"paper_default"`,
  `app/api/webhooks_luxalgo.py:238,402`; `app/services/filter_pipeline.py:396`).
- Consecuencias concretas (caso Anexo 25 §4.8: dos ES sobre MES):
  - Si A entra LONG MES y B recibe buy MES: L3 **no bloquea** (solo bloquea UNKNOWN/LOCKED,
    `filter_pipeline.py:401-406`), B despacha otra compra, y `on_entry_approved`
    **sobrescribe** la fila compartida (quantity, entry_price, entry_signal_id,
    strategy_id implícito) → el broker acumula posición mientras NTEXECG cree que hay una.
  - Mismo efecto para re-entrada de la misma estrategia (piramidación silenciosa).
  - Si A está LONG y B manda sell, se clasifica como reversal **de la posición de A** y
    dispara `dispatch_forced_exit` con el config de B (`webhooks_luxalgo.py:252-254`).
- Recomendación: regla `symbol_busy` en L3 (bloquear entrada si existe posición abierta del
  mismo símbolo/cuenta de otra estrategia — o de la misma, si no se permite piramidar),
  y/o llevar el estado por (strategy_id, account_id, symbol) con una vista agregada por
  símbolo para el gate. Test: dos estrategias, mismo símbolo, entradas cruzadas.

**P1-4 · Salidas forzadas y reversals ignoran los perfiles de riesgo (multi-cuenta).**
- Evidencia: `_dispatch_approved` itera `resolve_destinations(config)` para entradas *y
  salidas* (`app/api/webhooks_luxalgo.py:325-331`), pero `dispatch_forced_exit` envía **solo
  al webhook base** (`app/services/forced_exit.py:72-75`). El Exit Manager (EOD,
  max_holding, overnight) y los cierres por reversal usan ese camino.
- Impacto: una posición abierta en la cuenta de un perfil (fondeadora) **no se cierra** en
  EOD/forced/reversal — queda abierta overnight en esa cuenta.
- Recomendación: en `dispatch_forced_exit`, iterar los mismos destinos de
  `dprof.resolve_destinations(config)` (un delivery por destino). Test correspondiente.

**P1-5 · El reversal despacha el cierre ANTES de las validaciones de Nivel 1.**
- Evidencia: `_classify_and_handle_reversal` se llama antes de `pipeline.evaluate`
  (`app/api/webhooks_luxalgo.py:153-157`) y ejecuta `dispatch_forced_exit` sin chequear
  `strategy.status` (`:252-254`). Una estrategia **quarantined/retired** (que L1 bloquearía
  incluso para salidas, `filter_pipeline.py:297-299`) todavía puede despachar el cierre si
  su señal se clasifica como reversal.
- Impacto: acotado (solo cierra, nunca abre, y pasa por el gate dry_run), pero contradice
  la semántica de quarantine y el orden documentado del pipeline.
- Recomendación: cortocircuitar el reversal si `status in (quarantined, retired)`;
  registrar el BLOCK normal.

**P1-6 · Nivel 3 es stub pero la UI permite configurarlo como si funcionara.**
- Evidencia: `app/services/filter_pipeline.py:408-409` — "3.1 daily_loss_stop / 3.2
  max_positions — Phase 1 stubs". Sin embargo se resuelven y se exponen como configurables:
  `daily_loss_stop`, `max_open_positions` en `/ui/settings`
  (`app/web/routes_settings.py:54-56,79-83`), `max_trades_day`, `max_quantity`,
  `max_open_positions_symbol` en el resolver (`config_resolver.py:44-47,115-119,227-237`).
  Ninguno tiene consumidor. `max_quantity` tampoco capa la cantidad del payload
  (`payload_builder.py:47` usa `signal.quantity` tal cual).
- Impacto: "activo en la UI pero sin efecto en el pipeline" → 🔴 por definición del
  estándar de esta revisión. En paper el riesgo es de expectativas, no de dinero; en real
  sería grave.
- Recomendación: implementar 3.1/3.2 (con `strategy_decisions`/`execution_results` del día)
  o retirar los campos de la UI y marcar "no disponible (Fase X)".

**P1-7 · Scale Entry: la UI/API contradicen el motor real y pueden degradarlo silenciosamente.**
- Evidencia: el motor de ejecución escalonada SÍ existe (`PayloadBuilder.build_scaled`,
  `payload_builder.py:93-184`; dispatch multi-leg `webhooks_luxalgo.py:354-397`; memoria
  §13.2-13.4 dice `mode=execute` aplicado a las 8). Pero la UI fuerza
  `scale_entry_mode=design_only` con un hidden field (`strategy_detail.html:281`), el
  handler rechaza "enabled" con el mensaje **"el motor escalonado no existe"**
  (`routes_strategies.py:872-877,923-929`) y la API igual (`routes_api.py:287-291,309`).
- Impacto: (a) pulsar "Guardar Scale Entry" en una estrategia con `mode=execute` (puesto por
  `scripts/set_scale_execution.py`) lo **resetea a design_only sin aviso** — desactiva el
  escalonado en producción con un click; (b) el mensaje de UI/API y los docstrings mienten
  sobre el estado del sistema.
- Recomendación: la UI debe leer y mostrar el `mode` real, preservarlo al guardar (o exigir
  confirmación para bajarlo), y actualizar los textos. `execute/live` puede seguir siendo
  solo-scripts si se quiere fricción, pero sin pisarlo.

### P2 — Deuda / mantenibilidad / correctitud menor

**P2-1 · `atr_timeframe` es un knob fantasma: se configura y resuelve pero el pipeline no lo usa.**
- Evidencia: resolver lo produce (`config_resolver.py:49,114,225-226`), la API lo edita
  (`routes_api.py:245-257`), pero L5 lee ATR con `signal.timeframe or "5m"`
  (`filter_pipeline.py:230-233`), igual que las barras de calidad (`:206-208`). Hoy no hay
  daño porque el ATR TF calibrado coincide con el TF de la señal en las 8 estrategias
  (memoria Apéndice B), pero si alguien lo cambia esperando efecto, no lo hay.
- Recomendación: usar `config["atr_timeframe"]` en `get_atr` (decisión de diseño explícita)
  o eliminar el campo de API/modelo.

**P2-2 · `score_minimum`: el form de creación lo recibe y lo descarta; la pestaña Config no permite editarlo.**
- Evidencia: `routes_strategies.py:132` declara `score_minimum: str = Form("")` y el cuerpo
  de `create_strategy_ui` jamás lo usa. La sección "Filtros de calidad" del Config habla del
  "score mínimo" (`strategy_detail.html:420`) pero no tiene campo; solo se escribe vía
  scripts (`apply_anexo21_demo.py`, `apply_quality_filter.py`) en
  `pipeline_config_json["score_minimum"]`, que el resolver sí lee
  (`config_resolver.py:185-188`).
- Recomendación: añadir el campo al form de filtros (persistiendo en la misma clave) y
  eliminar el parámetro muerto del create.

**P2-3 · `dedup_seconds` se guarda desde la UI pero nadie lo lee.**
- Evidencia: se persiste en `pipeline_config_json["dedup_seconds"]`
  (`routes_strategies.py:246-248,721-725`; campo en `strategy_detail.html:585`), pero
  `Deduplicator.is_duplicate` usa `window_seconds=60` fijo y se llama sin argumento
  (`app/services/deduplicator.py:23`; `webhooks_luxalgo.py:78`).
- Recomendación: pasar `config["dedup_seconds"]` al deduplicador (requiere resolver el
  config antes del dedupe o leer el profile) o quitar el campo.

**P2-4 · `routing.target_account` / `account_id`: clave leída que nunca se escribe.**
- Evidencia: `config.get("account_id", "paper_default")` en 3 sitios
  (`webhooks_luxalgo.py:238,402`; `filter_pipeline.py:396`) pero ConfigResolver nunca
  produce `account_id`; la ficha guarda `routing.target_account`
  (`routes_strategies.py:252-258`) que ningún consumidor lee.
- Recomendación: derivar `account_id` de `routing.target_account` (o documentar que es solo
  referencia y eliminar la ilusión de multi-cuenta hasta la fase de reconciliación).

**P2-5 · Columnas y settings muertos (leídos nunca / escritos nunca).**
- `GlobalProfile`: `retry_attempts`, `retry_backoff_seconds`, `entry_signal_timeout_secs`
  (el cliente hace `getattr(settings, ...)` sobre `Settings`, que no los tiene → siempre 30s,
  `traderspost_client.py:94-95`; la UI de settings edita `retry_attempts` sin efecto,
  `routes_settings.py:58,72`); `days_enabled_json`, `entry_start_time`, `entry_end_time`
  (la sesión global NO se aplica: el resolver no los lee — la sesión solo viene de
  AssetProfile/windows, `config_resolver.py:85-102`); `news_impact_levels_json`,
  `global_daily_profit_lock`, `entry_cutoff_time`, `default_quantity`.
- `StrategyProfile`: `routing_mode`, `allowed_accounts_json`, `allowed_symbols_json`,
  `timezone`, `days_enabled_json`, `entry_start_time`, `entry_end_time`,
  `cooldown_minutes`, `daily_profit_lock`, `profile_name`, `active` — sin consumidor.
- `Settings` (`app/core/config.py`): `MAX_RETRY_ATTEMPTS`, `RETRY_BACKOFF_SECONDS`,
  `DEFAULT_TIMEZONE`, `SECRET_KEY`, `WEBHOOK_TOKEN_SALT`, `MARKET_DATA_FALLBACK_ENABLED`,
  `NEWS_CACHE_TTL_MINUTES` — 0 usos fuera de config.py (verificado por grep).
- `app/core/security.py` (`hash_token`/`verify_token`) — módulo huérfano: nadie lo importa
  (la validación real usa `hmac.compare_digest` en `webhooks_luxalgo.py:484`).
- Recomendación: barrido de limpieza (una migración para columnas + borrado de settings y
  módulo), manteniendo lo que tenga plan concreto de uso.

**P2-6 · Modelos muertos: `ConflictLog` y `EconomicEvent`.**
- Evidencia: solo aparecen en `app/models/__init__.py` y la migración inicial; cero uso en
  servicios/rutas. `ExecutionResult` sí se usa, pero **solo por scripts**
  (`scripts/import_results.py` → `app/services/results_import.py`); ninguna vista UI ni
  analítica lo muestra → los *fills reales* no son observables en la web (hueco de
  observabilidad; la analítica se corta en `WebhookDelivery`,
  `app/web/routes_analytics.py:141-146`).
- Recomendación: eliminar ConflictLog/EconomicEvent (o documentar fase futura); exponer
  `execution_results`/`compute_real_metrics` en Analytics (P&L real con/sin filtro —
  además cierra la Prueba 9 "en vivo").

**P2-7 · `Strategy.enabled` no gobierna nada; `Strategy.traderspost_webhook_url` duplicada.**
- Evidencia: `enabled` se escribe al cambiar status (`routes_strategies.py:1097,1232`) pero
  ningún servicio lo consulta (grep en `app/services` y `app/api`: 0 usos). La URL de
  TradersPost vive en `Strategy` **y** en `StrategyProfile`; el resolver solo lee la del
  profile (`config_resolver.py:213`), `/edit` sincroniza ambas (`routes_strategies.py:788,798`)
  pero el create y el clone pueden dejarlas divergentes.
- Recomendación: derivar `enabled` de `status` (property) o eliminarlo; una sola fuente
  para la URL (profile) y campo de Strategy como espejo de solo-lectura o eliminado.

**P2-8 · `rename_strategy` deja `strategy_id` legacy en el histórico.**
- Evidencia: `scripts/rename_strategy.py` copia Strategy+StrategyProfile y retira/borra la
  vieja, pero no toca `strategy_decisions`, `normalized_signals`, `raw_signals`,
  `webhook_deliveries` ni `strategy_performance` → la analítica por estrategia se parte en
  dos ids (las 7 estrategias del Anexo 25 tienen nombres nuevos vs los de la memoria §12).
- Recomendación: opción `--migrate-history` (UPDATE de strategy_id en las tablas de
  eventos) o tabla de alias que la analítica resuelva.

**P2-9 · Sin reconciliación con el broker (estado estimado) — riesgo conocido, falta diseño.**
- Evidencia: `PositionService` es una máquina de estados optimista
  (`position_service.py:1-14`); si el SL/TP se llena del lado del broker, NTEXECG queda en
  LONG/SHORT para siempre → L3 y el Exit Manager operan sobre un estado falso (p. ej.
  forced exit de una posición ya cerrada = orden exit en flat → puede abrir posición
  inversa según el modo del broker… TradersPost `exit` cierra-solo, mitigado).
- Propuesta de diseño (para backlog):
  1. **Fuente:** import periódico de fills de TradersPost (ya existe
     `results_import.import_results` + `_reconcile`) o webhook de TradersPost si está
     disponible en demo.
  2. **Job:** `PositionReconciler` en scheduler (cada 5-15 min): compara
     `execution_results` recientes contra `position_states`; si hay un fill de cierre
     (SL/TP/exit) posterior a `opened_at` → transición a FLAT con
     `state_source="reconciled"`; discrepancia irresoluble → `UNKNOWN` (que ya bloquea
     entradas en L3 — fail-closed correcto).
  3. **UI:** badge "estimado / reconciliado hace X min" en posiciones.
  4. **Alerta:** discrepancia > N minutos → warning en dashboard/audit.

**P2-10 · Perfiles de riesgo (`dispatch_profiles.py`): casos borde sin test y detalles.**
- No existe ningún test de `resolve_destinations`/`cap_quantities`/`make_dest_config`
  (grep en `tests/`: solo un nombre parecido en `test_dispatch_gate_phase2.py:95` que no
  los ejercita).
- `recompute_sl_tp` con ATR ausente devuelve `(None, None)` y el caller cae al `sl_price`
  base (`webhooks_luxalgo.py:346-352`) — correcto para fail-closed, pero entonces el SL
  enviado no corresponde al multiplicador del perfil y `extras.sl_multiplier` reporta el
  del perfil (`make_dest_config` pisa `sl_atr_multiplier`) → traza engañosa.
- El estado de posición se actualiza con la cantidad del **primer** destino
  (`primary_qty`, `webhooks_luxalgo.py:396-397,410`) — documentado como limitación, pero
  refuerza P1-3/P2-9.
- Dedupe por webhook_url correcto (`dispatch_profiles.py:117-129`).
- Recomendación: suite unitaria de `dispatch_profiles` (herencia, cap, dedupe, dry_run por
  perfil, TP override explícito-None) + e2e multi-perfil con 2 destinos.

**P2-11 · Duplicación interna de parsers y config en tres planos.**
- `_ints`/`_floats` definidos dos veces dentro de `routes_strategies.py`
  (`:887-911` y `:964-988`); parsing equivalente en `scripts/set_scale_execution.py` y
  validación paralela en `routes_api.py:304-307`.
- La misma config se puede escribir por 3 caminos con validaciones distintas: UI
  (`routes_strategies.py`), API JSON (`routes_api.py`), scripts (`apply_*.py`) — p. ej.
  scale_entry UI limpia `mode`, la API valida rangos, el script escribe `execute` sin pasar
  por ninguna de las dos.
- Recomendación: helpers compartidos (p. ej. `app/services/strategy_config.py`) con las
  funciones de parse/merge de `pipeline_config_json`, usados por UI, API y scripts.

### P3 — Nits

- **P3-1** `batch resume` siempre manda a `paper` aunque la estrategia estuviera en
  micro/live (`routes_strategies.py:1216-1217`); guardar el status previo.
- **P3-2** `_time_of_day` del QualityScorer codifica horario RTH; para estrategias 24h
  (NQ/YM/CL/6J) toda la noche vale 0.5 neutro (`quality_scorer.py:88-98`). Documentar o
  parametrizar por sesión.
- **P3-3** VWAP del subscore se calcula sobre las últimas ~100 barras, no anclado a sesión
  (`quality_scorer.py:62-77`) — aproximación; documentar.
- **P3-4** `exit_manager_sweep` hace N+1 (strategy + resolve por posición,
  `forced_exit.py:117-122`); irrelevante a esta escala, anotar.
- **P3-5** Ventana de dedupe fija en 60 s (`deduplicator.py:23`) — ver P2-3.
- **P3-6** `update_ficha` con "Permitir salidas fuera de ventana" desmarcado escribe `None`
  (hereda) y nunca `False` explícito (`routes_strategies.py:743`); imposible negarlo por
  estrategia desde la ficha (sí es posible conceptualmente vía columna).
- **P3-7** El `news_filter` (L2.3) es stub declarado (`filter_pipeline.py:358-359`) pero
  `/ui/settings` permite editar `news_window_minutes` — quitar de UI hasta implementarlo.
- **P3-8** `git status` muestra `Anexo_25_Bateria_Pruebas_Filtros.md` sin commitear —
  añadirlo al repo (es el estándar de calidad citado por esta revisión).

---

## 3. MAPA DE REDUNDANCIAS / DUPLICACIONES

| Qué se duplica | Dónde (A) | Dónde (B) | Cómo unificar |
|---|---|---|---|
| URL de TradersPost | `Strategy.traderspost_webhook_url` | `StrategyProfile.traderspost_webhook_url` (la que rige) | Una fuente (profile); espejo de solo lectura o eliminar columna en Strategy |
| Modo del sistema vs madurez | `GlobalProfile.mode` | `StrategyProfile.mode` → **misma clave** `config["mode"]` | Separar claves (P0-1) |
| Parsers de niveles/cantidades | `routes_strategies.py:887-911` y `:964-988` | `routes_api.py:304-307`, `scripts/set_scale_execution.py` | Helper compartido de parse/merge |
| Escritura de config de estrategia | UI (`routes_strategies`) | API (`routes_api`) y scripts `apply_*` | Servicio único de merge de `pipeline_config_json` con validación común |
| Sesión / ventanas | `AssetProfile.session_config_json` | `StrategyProfile` columnas de horario (muertas) + `pipeline_config_json["windows"]` (la que rige) | Eliminar columnas muertas del profile; windows es la fuente |
| Guardarraíles al crear vs editar | `create_strategy_ui:175-189` | `update_guardrails:438-451` | Misma función de parse |
| Ficha (machote) crear vs editar | `create_strategy_ui:193-273` | `update_ficha:669-753` | Extraer builder común |
| Carpetas legado | `A REVISAR/{DOCS,FASES_ANTERIORES,PROMPTS}` | `DOCS/`, `FASES_ANTERIORES/`, `PROMPTS/` en raíz (mismos archivos) | Conservar UNA copia (raíz), borrar `A REVISAR/` tras verificación de diff |
| Snapshot histórico | `ClaudeCodeTV_290626/` (ListaDeOperaciones+Reportes) | `ListaDeOperaciones/` raíz | Archivar el snapshot fuera del repo o en tag |
| Retries TradersPost | `Settings.MAX_RETRY_ATTEMPTS`/`RETRY_BACKOFF_SECONDS` | `GlobalProfile.retry_*` y hardcode 3/10 + backoff en `traderspost_client.py:93,164-167` | El cliente hardcodea (contrato REQ-0601); borrar los knobs muertos |
| strategy_id nuevo vs legacy | `strategies.strategy_id` actual | Decisiones/señales históricas con id viejo (post-rename) | Migración de histórico o tabla de alias (P2-8) |

**Scripts con solape funcional (consolidables):** `show_strategy_configs.py` /
`diag_profiles.py` / `manage_strategies.py` (tres vistas de config+estado);
`simulate_webhook.py` / `test_buy_all.py`; `preview_scaled.py` / `sim_scaled_entry.py`;
`apply_profile_policy_v1.py` + `revert_asset_profiles_v1.py` (superseded — documentado en
memoria Apéndice A, candidatos a archivo `scripts/archive/`).

---

## 4. MATRIZ DE TRAZABILIDAD — PESTAÑA "CONFIG" (y ficha/ventanas/settings)

Leyenda estado: ✅ conectado end-to-end · 🟨 conectado con defecto · 🔴 roto/desconectado.
"Ruta" = handler en `app/web/routes_strategies.py` salvo indicación.

| Campo (UI) | Ruta que guarda | Clave persistida | ¿Resolver la lee? | Consumidor real | Efecto | ¿Respeta ON/OFF? | Estado | Test |
|---|---|---|---|---|---|---|---|---|
| Nombre | `/edit:785` | `Strategy.name` | n/a (display) | UI | cosmético | n/a | ✅ | test_web |
| Modo (paper/micro/…) | `/edit:799-800` | `StrategyProfile.mode` | sí (`resolver:203`) | **pisa `config["mode"]` global** | anula L1.1 | — | 🔴 **P0-1** | falta (e2e resolver+L1) |
| Activo (asset) | `/edit:786` | `Strategy.asset_symbol` | sí (`resolver:134`) | guardrail 1.7 + lookup AssetProfile | expected_symbol | vía enforce ✅ | ✅ | test_guardrails_anexo08 |
| Timeframe | `/edit:787` | `Strategy.timeframe` | sí (`resolver:135`) | guardrail 1.8 (`_normalize_tf`) | expected_timeframe | vía enforce ✅ | ✅ | test_guardrails_anexo08 |
| TradersPost URL | `/edit:788,798` | Strategy + Profile | sí (profile, `resolver:213`) | `_dispatch_approved` | destino base | n/a | 🟨 duplicada (P2-7) | test_dispatch_gate_phase2 |
| Armar / DRY_RUN | `/dispatch:368-378` | `traderspost_enabled`,`dry_run` (profile) | sí, semántica AND/OR (`resolver:208-212`) | `resolve_effective_dry_run` (`webhooks:279-292`) | kill-switch por capas | ✅ (solo restringe) | ✅ | test_dispatch_gate_phase2 |
| SL × ATR | `/sltp:843` | `profile.sl_atr_multiplier` | sí (`resolver:215-218`) | `SLTPCalculator` (`:52,59-62`) | SL obligatorio | vacío→hereda ✅ | ✅ | test_sl_tp_calculator |
| TP × ATR | `/sltp:844` | `profile.tp_atr_multiplier` | sí (`resolver:219-222`) | `SLTPCalculator` (`:64-70`) | bracket opcional | vacío→sin TP ✅ | ✅ | test_sl_tp_calculator |
| Scale Entry (niveles/cant./max) | `/scale-entry:913-931` | `pipeline_config_json.scale_entry` | sí (`resolver:192-195`) | `build_scaled` (necesita mode execute/live) | multi-leg | **UI fuerza design_only y pisa execute** | 🔴 **P1-7** | test_scaled_entry_engine (motor sí; UI no) |
| Perfiles de riesgo (P1..P4) | `/profiles:999-1047` | `pipeline_config_json.profiles` | sí (`resolver:198-201`) | `dispatch_profiles.resolve_destinations` | multi-destino | enabled por perfil ✅ | 🟨 sin tests; forced-exit los ignora (P1-4) | **falta** |
| Guardarraíl symbol_match | `/guardrails:439-440` | `guardrails.enforce_symbol_match` | sí (`resolver:158-162`) | L1 1.7 (`pipeline:312-317`) | BLOCK mismatch | OFF→no-op ✅ | ✅ | test_guardrails_anexo08 |
| Guardarraíl timeframe_match | `/guardrails:441-442` | `guardrails.enforce_timeframe_match` | sí | L1 1.8 (`pipeline:319-327`) | BLOCK mismatch | OFF→no-op ✅ | ✅ | test_guardrails_anexo08 |
| Antigüedad máx. entrada/salida | `/guardrails:443-451` | `guardrails.signal_max_age_*` | sí | `_check_staleness` (`pipeline:361-381`) | **nulo: signal_ts=now** | OFF→no-op ✅ pero ON→sin efecto real | 🔴 **P1-2** | test_guardrails (con `now` inyectado — no cubre el caso real) |
| Filtros calidad (4 + pesos) | `/filters:556-577` | `pipeline_config_json.filters` | sí (`resolver:172-175`) | `QualityScorer.score` | subscores ponderados | OFF→score 100 (=UNKNOWN, ver P1-1) | 🟨 **P1-1** | test_quality_scorer_phase5 |
| Score mínimo | **no existe campo** (create lo descarta, `:132`) | `pipeline_config_json.score_minimum` (solo scripts) | sí (`resolver:185-188`) | gate L4 (`pipeline:210`) | umbral | — | 🔴 **P2-2** | test_filter_pipeline (backend sí) |
| Régimen (enabled/TF/permitidos) | `/regime:613-630` | `pipeline_config_json.regime` | sí (`resolver:177-180`) | gate L4 (`pipeline:188-202`) | BLOCK si régimen conocido ∉ allowed | OFF→no-op ✅; unknown fail-open ✅ | ✅ | test_hmm_service / test_filter_pipeline |
| Ventanas repetibles | `/windows:489-521` | `pipeline_config_json.windows` | sí (`resolver:165-170`) | `SessionValidator` ANY-match (`:52-56`) | sesión L2 | sin ventanas→config del activo ✅ | ✅ | test_windows_anexo08 |
| Ficha: riesgo referencia (stop_ticks, $/op, máx contratos) | `/ficha:709-720` | `pipeline_config_json.risk_reference` | no | nadie | **documentación** (rotulado "no enforzado") | n/a | ✅ por diseño (rotulado) | n/a |
| Ficha: Dedup (seg) | `/ficha:721-725` | `pipeline_config_json.dedup_seconds` | no | nadie (Deduplicator fijo 60 s) | **ninguno** | — | 🔴 **P2-3** | falta |
| Ficha: Cuenta objetivo / notas ruteo | `/ficha:731-739` | `pipeline_config_json.routing` | no (`account_id` nunca derivado) | nadie | **ninguno** | — | 🔴 **P2-4** | falta |
| Ficha: Cierre EOD (HH:MM) | `/ficha:744-753` | `profile.force_flat_time` | sí (`resolver:245-246`) | `ExitManager.due_exit` (`:57-60`) | forced_close_eod | vacío→None ✅ | ✅ | test_exit_manager_phase4 |
| Ficha: salidas fuera de ventana | `/ficha:743` | `profile.allow_exits_outside_window` | sí (`resolver:239-242`) | L2 (`pipeline:151-158`) | permite/bloquea exit fuera | uncheck→None (hereda, no False) | 🟨 P3-6 | test_filter_pipeline |
| Status (botones + batch) | `/status`, `/batch-action` | `Strategy.status` | vía objeto strategy | L1 1.2 (`pipeline:292-304`) | BLOCK/QUEUE | ✅ | ✅ | test_filter_pipeline |
| Settings: modo global | `routes_settings:66-67` | `GlobalProfile.mode` | leído (`resolver:88`) **y luego pisado** | L1 1.1 | **nulo con profile** | — | 🔴 **P0-1** | falta e2e |
| Settings: score mínimo global | `routes_settings:70` | `GlobalProfile.score_minimum` | sí (`resolver:91`) | gate L4 | umbral default | ✅ | ✅ | test_filter_pipeline |
| Settings: max_open_positions / daily_loss_stop / news_window / retry_attempts | `routes_settings:68-83` | GlobalProfile | parcialmente | **nadie** (L3 stub, news stub, retries hardcoded) | ninguno | — | 🔴 **P1-6/P2-5** | falta |

---

## 5. CÓDIGO MUERTO / DESCONECTADO (lista)

1. `app/core/security.py` — módulo completo sin importadores.
2. Modelos `ConflictLog`, `EconomicEvent` — tablas creadas, cero uso.
3. `Strategy.enabled` — se escribe, nunca gobierna (grep servicios/api: 0 usos).
4. `GlobalProfile`: `retry_attempts`, `retry_backoff_seconds`, `entry_signal_timeout_secs`,
   `days_enabled_json`, `entry_start_time`, `entry_end_time`, `news_impact_levels_json`,
   `global_daily_profit_lock`, `entry_cutoff_time`, `default_quantity`.
5. `StrategyProfile`: `routing_mode`, `allowed_accounts_json`, `allowed_symbols_json`,
   `timezone`, `days_enabled_json`, `entry_start_time`, `entry_end_time`,
   `cooldown_minutes`, `daily_profit_lock`, `profile_name`, `active`.
6. `Settings`: `MAX_RETRY_ATTEMPTS`, `RETRY_BACKOFF_SECONDS`, `DEFAULT_TIMEZONE`,
   `SECRET_KEY`, `WEBHOOK_TOKEN_SALT`, `MARKET_DATA_FALLBACK_ENABLED`,
   `NEWS_CACHE_TTL_MINUTES`.
7. Claves de config: `dedup_seconds` (escrita, no leída), `routing.target_account` (ídem),
   `account_id` (leída, nunca escrita), `atr_timeframe` (resuelta, no consumida por L5),
   `max_quantity`/`max_trades_day`/`daily_loss_stop`/`max_open_positions`/
   `max_open_positions_symbol`/`news_*` (resueltas, sin consumidor).
8. `RawSignal.time_raw` — capturada, jamás parseada (raíz de P1-2).
9. Parámetro `score_minimum` del form de creación (`routes_strategies.py:132`).
10. Providers stub `TradovateAPIProvider`/`DatabentoProvider`
    (`market_data_service.py:287-310`) — por diseño (Fase 5), mantener.
11. Carpeta `A REVISAR/` — duplicado íntegro de `DOCS/`, `FASES_ANTERIORES/`, `PROMPTS/`.
12. Scripts superseded: `apply_profile_policy_v1.py`, `revert_asset_profiles_v1.py`
    (documentado); solape entre `show_strategy_configs`/`diag_profiles`/`manage_strategies`.

---

## 6. IDEAS DE MEJORA ADICIONALES

1. **Idempotencia real del webhook saliente:** TradersPost puede recibir duplicados si el
   proceso muere entre `client.send` y el commit (el retry de LuxAlgo re-procesaría). Un
   `idempotency_key` = hash(decision_id, leg, destino) en extras + verificación previa en
   `webhook_deliveries` cerraría la ventana.
2. **Transacción/commit del background task:** todo `process_signal` corre en una sola
   sesión y se commitea al final (`webhooks_luxalgo.py:441-452`); si el POST a TradersPost
   ya salió y el commit falla, hay orden real sin registro. Considerar commit del delivery
   inmediatamente después de cada send.
3. **Colas de reintento para exits FAILED:** hoy un exit con 10 intentos fallidos queda
   FAILED y nadie lo reintenta después (el Exit Manager solo actúa sobre triggers de
   tiempo). Un re-sweep de deliveries FAILED de rol exit_* daría resiliencia.
4. **Snapshot de config en la decisión:** el contrato menciona `config_snapshot_json`
   (memoria Apéndice D) pero `StrategyDecision` no lo persiste — añadirlo facilitaría el
   replay contrafactual exacto (Prueba 9).
5. **Métricas de latencia end-to-end:** `signal_ts` real (post P1-2) vs `sent_at` del
   delivery → percentiles de latencia en Analytics.
6. **Health-check del despliegue:** endpoint `/health` podría exponer versión de git y
   estado de los 4 jobs del scheduler (hoy solo se ven en logs).
7. **DX:** consolidar los 40+ scripts en subcomandos de un CLI único (`python -m
   scripts.ntexecg <cmd>`) con el patrón dry-run/backup/audit ya estandarizado.
8. **Seguridad:** el token de webhook viaja como query param (queda en logs de nginx);
   considerar header. `LUXALGO_WEBHOOK_SECRET` default `dev_global_token` en código —
   asegurar override en prod (.env del servidor).

---

## 7. PLAN DE ACCIÓN CONSOLIDADO (backlog priorizado)

> Orden: prioridad × bajo riesgo primero. Los ítems que tocan dispatch/fail-closed van
> marcados ⚠ y exigen tests antes de merge. Estado: ⬜ pendiente · 🟨 en curso · ✅ hecho.

| ID | Título | Sev | Área / archivo | Evidencia | Propuesta concreta | Criterio de aceptación | Dep | Esf | Riesgo invariantes | Estado |
|---|---|---|---|---|---|---|---|---|---|---|
| NX-01 | Separar `global_mode` de `execution_mode` (freno global roto) | **P0** | `config_resolver.py:203`, `filter_pipeline.py:286` | resolver pisa mode global con mode del profile | Nueva clave `global_mode` en config; L1.1 la lee; `StrategyProfile.mode` deja de tocar esa clave | Test e2e: GlobalProfile.mode=paused + estrategia con profile ⇒ entrada BLOCK `global_paused`; exits pasan | — | S | Bajo ⚠ (L1, no dispatch) | ⬜ |
| NX-02 | Calidad UNKNOWN — score no parte en 100 (Anexo 25 §1-bis) | P1 | `filter_pipeline.py:69,180,212`, `quality_scorer.py:127`, `payload_builder.py:79`, `signal_detail.html:137` | señal sin filtros = "100 pasó" | `filters_active` en level_4; `ntexecg_quality` ∈ {UNKNOWN,LOW,MEDIUM,HIGH} en decisión + extras; UI muestra UNKNOWN sin ✅; avg_score excluye UNKNOWN | Señal sin filtros ⇒ quality=UNKNOWN visible en UI/payload/traza; con filtros ⇒ etiqueta por umbral; test por caso | — | M | Bajo (no cambia gates) | ⬜ |
| NX-03 | Staleness real: parsear `payload["time"]` → `signal_ts` | P1 | `signal_normalizer.py:130`, `webhooks_luxalgo.py:495` | signal_ts=now; time_raw sin uso | Parse ISO/epoch de `time` con fallback a now; `received_ts` aparte; staleness y retry-gate usan el ts real | Señal con time 10 min viejo y max_age=60 ⇒ BLOCK signal_stale; sin `time` ⇒ comportamiento actual | — | S/M | Bajo ⚠ (L2) | ⬜ |
| NX-04 | UI Scale Entry deja de pisar `mode=execute` + textos veraces | P1 | `routes_strategies.py:859-942`, `routes_api.py:284-323`, `strategy_detail.html:276-312` | hidden design_only; "motor no existe" (falso) | UI muestra mode real; guardar preserva mode; textos actualizados; bajar de execute exige confirmación | Con mode=execute, guardar niveles no cambia el mode; audit registra cambios de mode | — | S | Bajo | ⬜ |
| NX-05 | `score_minimum` editable en UI + limpiar param muerto del create | P1→ops | `routes_strategies.py:132`, form filtros, `strategy_detail.html:417-443` | campo inexistente; create lo descarta | Campo numérico en la sección Filtros → `pipeline_config_json.score_minimum`; validación 0-100 | Guardar 55 en UI ⇒ resolver lo aplica ⇒ señal score 50 BLOCK; test API/UI | NX-02 | S | Bajo | ⬜ |
| NX-06 | Forced exit / reversal despachan a TODOS los perfiles ⚠ | P1 | `forced_exit.py:72-75` | solo webhook base | Iterar `dprof.resolve_destinations(config)`; un WebhookDelivery por destino; tag por perfil | Posición con 2 perfiles ⇒ EOD genera 2 exits (uno por webhook); test | — | S/M | Medio ⚠ (dispatch) | ⬜ |
| NX-07 | Reversal respeta L1 (quarantined/retired no despacha ni el cierre) | P1 | `webhooks_luxalgo.py:221-276` | forced exit antes de validar status | Chequear `strategy.status` antes de `dispatch_forced_exit`; quarantined/retired ⇒ BLOCK normal | Señal reversal de estrategia quarantined ⇒ BLOCK, 0 deliveries; test | — | S | Bajo ⚠ | ⬜ |
| NX-08 | Regla `symbol_busy` en L3 (caso dos ES sobre MES) ⚠ | P1 | `filter_pipeline.py:386-409`, `position_service.py:160-170` | L3 no bloquea LONG/SHORT/PENDING existente | L3.4: entrada BLOCK `symbol_busy` si hay posición abierta (cualquier estrategia) en (account, symbol), salvo opt-out `allow_stacking`; incluye same-strategy re-entry | 2ª entrada al mismo símbolo ⇒ BLOCK symbol_busy con traza; exits no afectados; tests cruzados A/B | — | M | Medio ⚠ (L3) | ⬜ |
| NX-09 | Estado de posición por estrategia + agregado por símbolo | P1 | `position_service.py`, migración | fila única (account,symbol) se sobrescribe | Clave (strategy_id, account, symbol) + vista agregada para el gate de NX-08; migración de datos | Dos estrategias mismo símbolo mantienen estados separados; NX-08 gatea por el agregado | NX-08 | M/L | Medio ⚠ | ⬜ |
| NX-10 | Cablear `dedup_seconds` o quitar el campo | P2 | `deduplicator.py:23`, `webhooks_luxalgo.py:78`, ficha | guardado, nunca leído | Leer profile antes del dedupe y pasar window; default 60 | dedup_seconds=300 ⇒ señal repetida a los 2 min ⇒ IGNORE_DUPLICATE; test | — | S | Bajo | ⬜ |
| NX-11 | `atr_timeframe`: usarlo en L5/L4 o eliminar el knob | P2 | `filter_pipeline.py:206,230`, `routes_api.py:245` | resuelto, no consumido | `get_atr(data_symbol, config["atr_timeframe"] or signal.timeframe)`; documentar decisión | Estrategia con atr_timeframe=15m y señal 5m ⇒ ATR de 15m en traza; test | — | S | Medio ⚠ (cambia SL) — validar contra calibración | ⬜ |
| NX-12 | Cap de `max_quantity` en PayloadBuilder | P2 | `payload_builder.py:47`, `config_resolver.py:46` | qty del payload sin tope | `quantity = min(signal.quantity, config["max_quantity"])` en build y legs escalonados | Señal qty=99 con max=3 ⇒ payload qty=3 + warning en traza; test | — | S | Bajo ⚠ | ⬜ |
| NX-13 | Guardarraíles L3 reales: `max_trades_day`, `daily_loss_stop`, `max_open_positions` | P2 | `filter_pipeline.py:408`, settings UI | stubs configurables en UI | Contar decisiones APPROVE del día por estrategia (max_trades_day); P&L día desde execution_results (daily_loss_stop); posiciones abiertas (max_open) — o retirar campos de UI | Cada límite excedido ⇒ BLOCK con reason propio; tests por límite | NX-09 (parcial), NX-16 | M/L | Medio ⚠ | ⬜ |
| NX-14 | Suite de tests `dispatch_profiles` + e2e multi-perfil | P2 | `tests/` (nuevo) | 0 tests del módulo | Unit: cap_quantities, herencia, dedupe webhook, TP None explícito, dry_run por perfil; e2e: 2 destinos, deliveries y gate por destino | Suite verde cubriendo los 6 casos borde del prompt | — | M | Nulo (solo tests) | ⬜ |
| NX-15 | Reconciliación con broker (diseño P2-9) — job `PositionReconciler` | P2 | `results_import.py`, `scheduler.py`, `position_service.py` | estado estimado sin corrección | Import periódico de fills; cierre detectado ⇒ FLAT `state_source=reconciled`; discrepancia ⇒ UNKNOWN (bloquea entradas); badge UI | SL llenado en broker ⇒ posición pasa a FLAT ≤15 min; discrepancia genera UNKNOWN + alerta; tests con fixtures | NX-09 | L | Medio ⚠ (escribe estado) | ⬜ |
| NX-16 | Fills reales en Analytics (`execution_results` en UI) | P2 | `routes_analytics.py`, `results_import.compute_real_metrics` | datos importados invisibles | Sección "Resultados reales" (P&L por estrategia, con/sin filtro — cierra Prueba 9 en vivo) | Página muestra métricas de execution_results; test de ruta | — | M | Nulo | ⬜ |
| NX-17 | `cancel_after` por estrategia (pendiente conocido, Anexo 25 §4.7) | P2 | `pipeline_config_json` (nuevo), `pullback_timing.py` | interino 3600 s del lado TradersPost | Guardar `cancel_after_seconds` = p90 pullback + colchón por estrategia (metadata + procedimiento de alta en TradersPost); mostrar en Config | Cada estrategia tiene su valor documentado y visible; playbook actualizado | — | S/M | Nulo (metadata) | ⬜ |
| NX-18 | Migrar histórico en `rename_strategy` (ids legacy) | P2 | `scripts/rename_strategy.py` | decisiones con id viejo | Flag `--migrate-history`: UPDATE strategy_id en decisions/signals/deliveries/performance (transaccional, backup) | Tras rename, analytics por id nuevo incluye el histórico; dry-run muestra conteos | — | S | Bajo | ⬜ |
| NX-19 | Limpieza de columnas/modelos/settings muertos | P2 | modelos + migración + `config.py` + `security.py` | §5 de este reporte | Migración que dropea columnas muertas; borrar ConflictLog/EconomicEvent, security.py, settings sin uso; `Strategy.enabled` → property | Grep post-limpieza: 0 referencias muertas; suite verde; migración reversible | — | M | Bajo | ⬜ |
| NX-20 | Helpers compartidos de parse/merge de `pipeline_config_json` | P2 | `routes_strategies.py`, `routes_api.py`, scripts | parsers duplicados ×3 | `app/services/strategy_config.py` con `_ints/_floats/merge_section`; UI/API/scripts lo usan | Una sola definición; tests de parsing; sin cambio de comportamiento | — | M | Bajo | ⬜ |
| NX-21 | `account_id` derivado de routing o retirar la ilusión | P3 | `webhooks_luxalgo.py:238`, ficha routing | clave leída nunca escrita | Decisión: (a) resolver `account_id` desde `routing.target_account`, o (b) rotular routing como referencia | Comportamiento documentado y consistente con NX-09 | NX-09 | S | Bajo | ⬜ |
| NX-22 | Idempotencia + commit temprano de deliveries | P3 | `webhooks_luxalgo.py:434-452` | duplicado posible si crash post-send | `idempotency_key` por leg en extras; commit del delivery tras cada send | Reproceso del mismo raw_signal no re-envía legs ya SENT; test | — | M | Medio ⚠ | ⬜ |
| NX-23 | Re-sweep de exits FAILED | P3 | `scheduler.py`, `forced_exit.py` | exit fallido queda huérfano | Job que reintenta deliveries FAILED con rol exit_* (< N horas) | Exit FAILED se reintenta y cierra; test | NX-06 | S/M | Medio ⚠ | ⬜ |
| NX-24 | `config_snapshot_json` en StrategyDecision | P3 | `decision.py`, `process_signal` | contrato lo pide; no se persiste | Guardar snapshot del config resuelto (limado de secretos) por decisión | Toda decisión nueva lleva snapshot; replay exacto posible | — | S | Bajo | ⬜ |
| NX-25 | Consolidar carpetas legado (`A REVISAR/` duplicada, snapshot CCTV) | P3 | raíz del repo | §3 | Verificar diff, borrar `A REVISAR/`; mover `ClaudeCodeTV_290626/` a tag/archivo; commitear Anexo 25 | Una sola copia de cada doc; repo sin duplicados | — | S | Nulo | ⬜ |
| NX-26 | Nits UI: batch-resume restaura status previo; allow_exits explícito False; quitar news/retry de settings | P3 | `routes_strategies.py:1216`, `:743`, `routes_settings.py` | P3-1/P3-6/P3-7 | Tres arreglos menores independientes | Comportamientos corregidos con tests de ruta | — | S | Bajo | ⬜ |

**Sugerencia de arranque (uno por uno, como acordado):**
`NX-01` (P0, quirúrgico) → `NX-02` (el pendiente de calidad del Anexo 25) → `NX-03`
(staleness) → `NX-04`/`NX-05` (UI veraz, riesgo nulo) → `NX-06`/`NX-07` (cierres
correctos) → `NX-08`/`NX-09` (symbol_busy + estado por estrategia) → resto.

---

*Fin de la revisión. Ningún archivo de código fue modificado; único entregable:
este documento.*
