**Modelo:** `claude-fable-5` (Claude Fable 5) · **Fecha:** 2026-07-02 · **Alcance:** revisión de solo lectura — no se modificó código ni se hizo commit/push.

# REVISIÓN DE ARQUITECTURA — NTEXECG

> Evidencia verificada por lectura directa y grep sobre `C:\NTEXECG` (rama `main`, HEAD `d739383`).
> Suite de tests ejecutada como verificación de estado: **396 passed** en 105 s (`pytest -q`).

---

## 1. RESUMEN EJECUTIVO

**¿El sistema es coherente con su objetivo? Sí, con reservas serias en los bordes.** El camino central webhook → RawSignal → normalización/dedupe → FilterPipeline (5 niveles) → decisión → dispatch multi-perfil → PositionState → auditoría/UI existe, está bien trazado y el invariante nuclear (entrada sin `sl_price` → BLOCK, `PayloadBuilder` lanza `ValueError` si se viola) se cumple en el flujo normal. Sin embargo: (1) el fix del freno global `paused/flatten_only` (NX-01) está **sin commitear** — el servidor desplegado corre la versión donde ese kill-switch es inerte; (2) los **perfiles de riesgo pueden escalar por encima del kill-switch** (`dry_run=false` a nivel perfil escapa del `dry_run` global); (3) hay una familia de controles de la pestaña Config que se guardan pero **nunca se consumen** (dedup_seconds, retry_attempts, atr_timeframe, score_minimum del alta), y (4) el botón Flatten de la UI **no envía nada** al broker aunque dice "Flatten enviado". El pendiente conocido del Anexo 25 (score=100 = UNKNOWN, no HIGH) sigue sin implementar y se confirma en código y UI.

---

## 2. HALLAZGOS POR SEVERIDAD

### P0 — rompe fail-closed / kill-switch / paper-only

**P0-1. El freno global `paused`/`flatten_only` está roto en el código desplegado (fix NX-01 sin commitear).**
- Evidencia: `git status` muestra `app/services/filter_pipeline.py`, `app/services/config_resolver.py` y `tests/test_filter_pipeline.py` **modificados sin commit**; el diff introduce la clave `global_mode` (`config_resolver.py:42,95`) y cambia L1.1 a leerla (`filter_pipeline.py:289`). En la versión commiteada (HEAD `d739383`), L1.1 lee `config["mode"]`, que el merge de `StrategyProfile` sobrescribe con el modo de madurez (`paper`/`micro`/…) en `config_resolver.py:210` — el freno global **nunca bloquea** porque `"paper"` no está en `("paused","flatten_only")`.
- Impacto: en el servidor (que consume por `git pull`), poner el sistema global en `paused` desde Settings NO detiene entradas. Es una capa entera del kill-switch inoperante en producción.
- Recomendación: **commitear y pushear el fix NX-01 hoy** (ya tiene test verde local) y verificar en el servidor con una señal simulada bajo `mode=paused`.

**P0-2. Los perfiles de riesgo escalan por encima del kill-switch por capas.**
- Evidencia: `app/services/dispatch_profiles.py:110-114` — `"dry_run": p["dry_run"] if isinstance(p.get("dry_run"), bool) else base_dry` y lo mismo para `traderspost_enabled`. Un perfil con `{"dry_run": false}` o `{"traderspost_enabled": true}` **reemplaza** el valor base ya fusionado (global OR estrategia), y `resolve_effective_dry_run` (`webhooks_luxalgo.py:279-292`) se evalúa por destino con esa config proyectada (`webhooks_luxalgo.py:358`).
- Impacto: viola el invariante "cualquier nivel que pida dry_run gana / traderspost_enabled = global AND estrategia". Con el global en dry_run, un perfil configurado vía `scripts/manage_profiles.py --set-json` puede enviar HTTP real (solo lo detiene el env `TRADERSPOST_ENABLED`). La UI actual solo puede escribir `dry_run: true` en perfiles (`routes_strategies.py:1008,1027-1028`), así que hoy se explota por script, pero el contrato semántico está roto en código.
- Recomendación: en `resolve_destinations`, aplicar la misma semántica de capas: `dry_run = base_dry OR p.dry_run`, `traderspost_enabled = base_tpen AND p.traderspost_enabled`. Un perfil solo puede restringir, nunca abrir. Test dedicado.

**P0-3. El env `DRY_RUN` se muestra como candado en la UI pero el gate de dispatch lo ignora.**
- Evidencia: `app/web/common.py:44` — el badge "DRY RUN" del navbar se enciende con `settings.DRY_RUN or not settings.TRADERSPOST_ENABLED`. Pero `resolve_effective_dry_run` (`webhooks_luxalgo.py:288-292`) solo consulta `TRADERSPOST_ENABLED`; `settings.DRY_RUN` no participa en ningún gate.
- Impacto: con `.env` `DRY_RUN=True` + `TRADERSPOST_ENABLED=True`, el operador ve el badge DRY RUN mientras el sistema envía real. Indicador de seguridad engañoso = P0 operativo aunque el envío real requiera además global+estrategia armadas.
- Recomendación: o `resolve_effective_dry_run` incluye `settings.DRY_RUN` como cuarta capa, o se elimina `DRY_RUN` del env y del badge. No pueden divergir.

### P1 — correctitud / riesgo

**P1-1. Calidad de señal parte en 100 sin marca UNKNOWN (pendiente conocido, Anexo 25 §1-bis).**
- Evidencia: `filter_pipeline.py:69` (`score: int = 100` en `PipelineResult`), `filter_pipeline.py:180` (`score = 100`), `quality_scorer.py:126-127` (`if not active: return 100`). `execution["level_4"]` registra solo `{score, passed}` (`filter_pipeline.py:212`) — no existe `filters_active` ni `quality`. La cinta de la UI muestra "Score 100 ≥ 70" en verde para estrategias sin filtros (`routes_signals.py:69-74`), y el payload lleva `ntexecg_score` sin etiqueta (`payload_builder.py:79`).
- Impacto: una señal sin ninguna medición aparece como "perfecta" (el caso MNQ sin contexto). Además `avg_score` de `StrategyPerformance` queda inflado a ~100 para estrategias sin filtros.
- Recomendación (plan concreto): (a) en `QualityScorer.score` devolver también `filters_active` (o exponer `active` como método); (b) en L4 calcular `filters_active = bool(filters activos) or regime.enabled` y escribir `execution["level_4"] = {score, passed, filters_active, quality}` con `quality ∈ {UNKNOWN, LOW, MEDIUM, HIGH}` (UNKNOWN si `not filters_active`; LOW se bloquea; umbral_alto configurable, p. ej. 80); (c) propagar `ntexecg_quality` y `filters_active` a `payload.extras` (`payload_builder.py:76-86` y `build_scaled:169-179`); (d) en la cinta (`routes_signals.py:66-76`) y en dashboard/signals renderizar UNKNOWN gris/ámbar sin ✅; (e) excluir señales UNKNOWN del `avg_score` o promediar solo scores medidos. El gate numérico (`score ≥ score_minimum`) no cambia. Tests: sin filtros → quality UNKNOWN y ribbon sin verde; con filtros → HIGH/MEDIUM/LOW correctos.

**P1-2. Entrada sin precio produce SL absurdo con `passed=True`.**
- Evidencia: `signal_normalizer.py:102-106` — precio ausente/no parseable → `price = 0.0` (no None). `filter_pipeline.py:234-235` llama `calculate(signal, atr, signal.price or 0.0, config)`; `sl_tp_calculator.py:59-62` calcula `sl = 0 − atr×k` (negativo para long) y devuelve `passed=True`. El payload sale con `signalPrice: 0.0` y `stopLoss` sin sentido.
- Impacto: el fail-closed se cumple de forma (hay SL) pero no de fondo (el SL no protege nada). TradersPost probablemente rechace, pero NTEXECG lo registra como APPROVE+SENT-intent.
- Recomendación: en el normalizador, precio inválido → `price=None`; en L5, `entry_price` faltante o `<= 0` → BLOCK `entry_price_missing`. Test con payload sin `price`.

**P1-3. El botón "Flatten" de la UI no despacha ningún cierre.**
- Evidencia: `routes_positions.py:42-53` llama `PositionService.on_flatten_manual` (`position_service.py:104-118`), que solo cambia el estado estimado a EXITING + AuditLog, y muestra flash **"Flatten enviado"**. No hay llamada a `dispatch_forced_exit` ni a `TradersPostClient`. Además `ExitManager` solo barre `{LONG, SHORT}` (`exit_manager.py:21`), así que la posición queda EXITING para siempre y la posición real del broker sigue abierta.
- Impacto: el operador cree haber aplanado una posición y no lo hizo — en un gateway de trading es un riesgo directo.
- Recomendación: `flatten` debe invocar `dispatch_forced_exit(db, pos, strategy, config, "manual_flatten", settings)` (resolviendo config) y reportar el status real del envío. Test que verifique el WebhookDelivery.

**P1-4. Salidas forzadas y cierres por reversal solo van al webhook base — los perfiles de riesgo quedan abiertos.**
- Evidencia: `forced_exit.py:72-75` envía únicamente a `config.get("traderspost_webhook_url")` con `destination="traderspost"`. En cambio, las entradas aprobadas se replican a todos los perfiles (`webhooks_luxalgo.py:325-397` vía `resolve_destinations`). El cierre por reversal usa el mismo `dispatch_forced_exit` (`webhooks_luxalgo.py:253-254`).
- Impacto: con perfiles habilitados, la cuenta del perfil recibe la entrada pero nunca el cierre forzado (EOD, max_holding, overnight, reversal) → posiciones huérfanas en cuentas secundarias.
- Recomendación: iterar `dprof.resolve_destinations(config)` también en `dispatch_forced_exit` (los exits ya lo hacen en el flujo LuxAlgo). Test multi-perfil de salida forzada.

**P1-5. Un delivery FAILED deja el estado de posición estancado sin reintento ni alarma.**
- Evidencia: `_dispatch_approved` siempre ejecuta `on_entry_approved`/`on_exit_approved` (`webhooks_luxalgo.py:399-416`) y solo confirma con `any_sent` (`webhooks_luxalgo.py:419-422`). Si los 3/10 intentos fallan: entrada → PENDING_* eterno; salida → EXITING eterno. Nada barre PENDING/EXITING (`exit_manager.py:21`), y L3 no bloquea por PENDING (solo UNKNOWN/LOCKED, `filter_pipeline.py:404-409`).
- Impacto: divergencia silenciosa estado-estimado vs broker; la única señal es el banner de FAILED en dashboard (`routes_dashboard.py:188-217`).
- Recomendación: al agotar reintentos de una SALIDA, pasar la posición a UNKNOWN (que sí bloquea entradas) + alerta; para entradas FAILED, revertir a FLAT. Job que reporte posiciones PENDING/EXITING con antigüedad > N min.

**P1-6. `force_flat_time` global (default 15:55) se hereda a estrategias 24h y no hay forma de decir "sin EOD" por estrategia.**
- Evidencia: `global_profile.py:31` (`default=time(15,55)`), `config_resolver.py:104` lo fusiona, `config_resolver.py:252-253` solo overridea si el profile tiene valor **no-None** (None = heredar, no "apagar"), y `exit_manager.py:57-60` fuerza cierre cuando `now_local >= fct`. La ruta `/ficha` incluso re-pone `force_flat_time = None` si el campo va vacío (`routes_strategies.py:744-753`), reactivando la herencia.
- Impacto: si la fila global tiene 15:55 (el default del modelo), toda posición confirmada de NQ/YM/CL/6J (24h) se aplana a las 15:55 ET. Contradice la calibración de sesión 24h del Anexo 20/Memoria §12.
- Recomendación: valor centinela explícito por estrategia ("sin EOD" ≠ "heredar"), o mover el default global a NULL y documentarlo. Verificar qué tiene la fila real en producción.

**P1-7. No existe la regla de portafolio "una posición por símbolo" (caso dos ES sobre MES) y L3 permite re-entradas ilimitadas.**
- Evidencia: L3 solo evalúa `position_state` UNKNOWN/LOCKED (`filter_pipeline.py:389-412`); `daily_loss_stop`/`max_positions` son stubs declarados (`filter_pipeline.py:411`). `PositionState` se clave por `(account_id, symbol)` (`position_service.py:160-170`), así que dos estrategias sobre MES comparten fila y se pisan el estimado (la segunda entrada del mismo lado es `entry_long`, no reversal, y pasa). `grep symbol_busy` → 0 resultados.
- Impacto: dos ES sobre MES pueden duplicar exposición o netearse; una misma estrategia puede piramidar por señales repetidas fuera de la ventana de dedupe.
- Recomendación (diseño `symbol_busy`): nuevo check L3.4 — para entradas, si `PositionState(account_id, symbol).state ∈ {PENDING_*, LONG, SHORT, EXITING}` y `entry_signal` no es de la estrategia dueña (o aunque lo sea, si ya hay posición del mismo lado), BLOCK `symbol_busy`, opt-out por config (`allow_stacking`). Registrar la estrategia dueña en la fila (ya existe `strategy_id`). Tests: misma estrategia re-entrada, estrategia B con A abierta, salida siempre exenta.

**P1-8. Estado de posición estimado sin reconciliación con el broker (riesgo conocido — propuesta pedida).**
- Evidencia: `position_service.py:1-14` (docstring "estimated"); si el SL se llena broker-side, NTEXECG mantiene LONG y (a) bloquea/permite mal por L3, (b) puede emitir un exit sobre posición plana, (c) `forced_close_eod` puede cerrar algo que ya no existe.
- Propuesta de reconciliación (incremental):
  1. **Fase A (ya en marcha):** import semanal de resultados (`results_import.py`) marca trades cerrados → job que ponga FLAT las posiciones cuyo trade cerrado esté conciliado (match exacto por `extras.signal_id`).
  2. **Fase B (diaria/horaria):** heurística de expiración — posición LONG/SHORT cuya edad supere `max_holding` teórico o cuya sesión cerró hace > X horas pasa a UNKNOWN (bloquea entradas hasta revisión) en vez de fingir certeza.
  3. **Fase C (objetivo):** poll de posiciones reales vía API del broker (Tradovate REST ya está previsto como provider stub, `market_data_service.py:287-297`) o el CSV de TradersPost; comparar contra PositionState y emitir `conflict_log` (el modelo `ConflictLog` existe y está sin uso — `app/models/conflict_log.py`).
  4. En UI, mostrar SIEMPRE la edad del estimado y el último punto de conciliación.

**P1-9. La UI de Scale Entry apaga la ejecución escalonada silenciosamente y su texto es falso.**
- Evidencia: el form del Config tab lleva `scale_entry_mode` **hidden con valor `design_only`** (`strategy_detail.html:281`), y la ruta reescribe `{"mode": "design_only", ...}` (`routes_strategies.py:913-930`). Si el operador guarda niveles/cantidades sobre una estrategia con `mode=execute` (aplicado por `scripts/set_scale_execution.py`, estado actual según Memoria §14), la ejecución escalonada queda **desactivada sin aviso**. Además el docstring/flash ("el motor escalonado no existe", `routes_strategies.py:871-877`) y `routes_api.py:12,40` contradicen la realidad: el motor existe (`payload_builder.py:93-184`) y despacha multi-leg (`webhooks_luxalgo.py:354-397`).
- Impacto: pérdida silenciosa de config operativa + documentación embebida engañosa. Tres escritores (UI, API JSON, script) con semánticas distintas del mismo campo (`design_only/off` vs `execute/live` vs `enabled` rechazado).
- Recomendación: la UI debe **preservar el `mode` existente** (mostrarlo read-only con advertencia "EJECUTA ⚠") y unificar el vocabulario de modos en un solo lugar; corregir docstrings/flashes.

**P1-10. `dedup_seconds` se guarda en la ficha pero el Deduplicator hardcodea 60 s.**
- Evidencia: la UI guarda `pipeline_config_json["dedup_seconds"]` (`routes_strategies.py:246-248, 721-725`; campo en `strategy_detail.html:585`), pero `Deduplicator.is_duplicate` usa `window_seconds: int = 60` y nadie le pasa otro valor (`deduplicator.py:23`; llamada en `webhooks_luxalgo.py:78`). ConfigResolver ni lo lee.
- Impacto: control de Config visible y editable **sin ningún efecto** — exactamente el patrón 🔴 que esta revisión debía cazar.
- Recomendación: pasar `config["dedup_seconds"]` al deduplicador (resolver la config antes del dedupe o leer el profile) o quitar el campo de la ficha.

### P2 — deuda / mantenibilidad

**P2-1. Reintentos y timeout de TradersPost configurables que no configuran nada.**
- `GlobalProfile.retry_attempts/retry_backoff_seconds/entry_signal_timeout_secs` (`global_profile.py:56-58`) se editan en Settings (`routes_settings.py:72`) pero `TradersPostClient` hardcodea 3/10 intentos y backoff 1-2-4 (`traderspost_client.py:93,164-167`) y lee el timeout de `settings` env — atributo `ENTRY_SIGNAL_TIMEOUT_SECS` que **no existe** en `Settings` (`config.py`), por lo que siempre aplica el default 30 (`traderspost_client.py:94-95`). Los env `MAX_RETRY_ATTEMPTS`/`RETRY_BACKOFF_SECONDS` (`config.py:42-43`) tampoco se usan. 🔴 en matriz.
**P2-2. `atr_timeframe` se resuelve pero el pipeline no lo consume.**
- `config["atr_timeframe"]` se llena desde asset/strategy (`config_resolver.py:54,121,232-233`) pero L5 calcula el ATR con `signal.timeframe` (`filter_pipeline.py:230-231`) y L4 lee barras con `signal.timeframe` (`filter_pipeline.py:206-208`). Hoy coincide de facto (la calibración usa el TF de la señal), pero el knob es decorativo: cambiarlo no cambia nada. Consumirlo (`get_atr(data_symbol, config["atr_timeframe"] or signal.timeframe, ...)`) o eliminarlo del modelo/scripts.
**P2-3. `score_minimum` del formulario de alta se descarta y no hay campo en el Config tab.**
- `create_strategy_ui` declara `score_minimum: str = Form("")` (`routes_strategies.py:132`) y **nunca lo usa**; el Config tab no tiene input para `pipeline_config_json["score_minimum"]` (solo scripts `apply_quality_filter.py`/`apply_anexo21_demo.py` lo escriben; resolver lo lee en `config_resolver.py:192-195`). El threshold efectivo (p. ej. GC=55) es invisible/ineditable en la UI.
**P2-4. Staleness mide latencia interna, no la edad real de la señal.**
- `signal_ts = datetime.now(...)` al normalizar (`signal_normalizer.py:130`); el campo `time` del payload de TradingView se guarda en `RawSignal.time_raw` y se ignora. `signal_max_age_entry_seconds` y el chequeo del cliente (`traderspost_client.py:98-105`) miden desde la recepción — un webhook retenido 10 min por TV/red pasa como fresco. Parsear `payload["time"]` (con fallback a recepción).
**P2-5. Sin clave de idempotencia en el dispatch.**
- Un timeout tras entrega efectiva se reintenta y puede duplicar la orden (`traderspost_client.py:113-146`). `extras.signal_id` ya viaja; documentar/usar el mecanismo de deduplicación de TradersPost si existe, o al menos registrar el riesgo por leg.
**P2-6. Clonar una estrategia pierde toda la config de pipeline.**
- El clon copia SL/TP/ATR pero no `pipeline_config_json` (windows/filters/regime/guardrails/scale/profiles) ni guardrails (`routes_strategies.py:1179-1187`). Silencioso; copiar y resetear solo lo peligroso (dispatch, profiles).
**P2-7. Auto-creación de estrategias desde señales desconocidas con el secret global.**
- `process_signal` crea la Strategy si no existe (`webhooks_luxalgo.py:127-138`); el token para ids desconocidos es el global `LUXALGO_WEBHOOK_SECRET` (`webhooks_luxalgo.py:478-484`). Queda en `candidate` (no ejecuta, `filter_pipeline.py:295-298`) pero permite ensuciar la DB/dashboard remotamente. Flag de entorno para desactivarlo en producción.
**P2-8. Tokens de webhook en texto plano y módulo de hashing muerto.**
- `Strategy.webhook_token` se guarda plano (`strategy.py:37`; `routes_strategies.py:159,407`) y se compara plano (`webhooks_luxalgo.py:479-484`). `app/core/security.py` (hash_token/verify_token) tiene **cero usos** y `WEBHOOK_TOKEN_SALT` no se usa. O se hashean los tokens (mostrando el valor solo al generarlo) o se elimina security.py/salt para no aparentar una protección inexistente.
**P2-9. `strategy_id` legacy en decisiones históricas.**
- `scripts/rename_strategy.py` recrea con id nuevo y retira/borra el viejo (`rename_strategy.py:1-11`); las `strategy_decisions`/deliveries históricas conservan el id viejo. Analytics agrupa por `strategy_id` crudo (`routes_analytics.py:78-107`) → aparecen series partidas/ids retirados sin marca. Añadir tabla/JSON de alias (`old_id → new_id`) y usarla en analytics, o anotar "(retirada)" con el status.
**P2-10. `Strategy.enabled` y `Strategy.traderspost_webhook_url` son redundantes.**
- `enabled` se escribe al cambiar status (`routes_strategies.py:1097,1232`) pero el pipeline decide solo por `status` (`filter_pipeline.py:294-307`) — nadie lo lee para gate. `Strategy.traderspost_webhook_url` se duplica con el del profile y ConfigResolver solo lee el profile (`config_resolver.py:220`). Consolidar.
**P2-11. Divergencia doc 03 vs código.**
- `03_ARQUITECTURA` describe módulos inexistentes: `app/schemas/*` (la carpeta está vacía), `api/internal/*` (vacío), `news_filter.py`, `strategy_registry.py`, `timezones.py`, tests bajo `app/tests/`. También el flujo (§2) pinta el dedupe como check 1.3 del pipeline cuando vive en `process_signal` antes del pipeline (`webhooks_luxalgo.py:77-95`), y el check "1.5 allowed_symbols" no existe (la columna `allowed_symbols_json` está muerta). La Memoria §12 lista la generación anterior de estrategias (`ES5m`, `NQ5m_ConfirmationAny`…) mientras Anexo 23/25 y los scripts operan la nueva (`ES5m_ConfNormal_TC_TSR`…). Actualizar/anotar como histórico.
**P2-12. Gate de régimen habilitado sin regímenes permitidos = no-op silencioso.**
- `filter_pipeline.py:196` — `if allowed and regime != "unknown" ...`: con `allowed_regimes=[]` el gate corre y nunca bloquea. La UI lo previene (`routes_strategies.py:624-629` elimina la clave), pero scripts/JSON pueden dejarlo así. Registrar warning en la traza o tratar `enabled+[]` como inválido.
**P2-13. Cobertura de tests — huecos en los caminos nuevos más delicados.**
- 396 tests verdes, buena base (fail-closed, gate Fase 2, escalonado, guardarraíles, ventanas, alias market-data). Faltan: **dispatch_profiles** (0 tests: herencia base↔perfil, `cap_quantities`, dedupe por webhook_url, kill-switch por perfil — el bug P0-2 habría saltado), rekey del deduplicador (`rk:`/`dup:` en `webhooks_luxalgo.py:97-121`), flatten UI (P1-3), salida forzada multi-perfil (P1-4), entrada sin precio (P1-2), FAILED→estado (P1-5), y el no-op de régimen vacío.

### P3 — nits

- `_ints`/`_floats` duplicados dentro del mismo archivo (`routes_strategies.py:887-911` vs `964-988`) y parsers equivalentes en `routes_api.py`/scripts — extraer a un helper.
- `avg_score` promedia 0 para decisiones sin score (salidas, blocks tempranos) (`performance_tracker.py:55-58`) — sesga la métrica a la baja o al alza según el mix.
- `SessionValidator` usa `datetime.now(tz)` no inyectable (`session_validator.py:42`) — L2 no es testeable con hora fija (staleness sí acepta `config["now"]`, `filter_pipeline.py:375`).
- Al recomputar SL/TP por perfil, si falta `entry_price`/ATR el TP del perfil queda None en silencio aunque la base tuviera TP (`webhooks_luxalgo.py:341-352`).
- `/ficha` no permite poner `allow_exits_outside_window=False` (unchecked → None = heredar) (`routes_strategies.py:743`).
- `exit_manager_sweep` resuelve config por posición en bucle (`forced_exit.py:116-125`) — aceptable con pocas posiciones; cachear por estrategia si crece.
- Claves JWT de sesión cortas en dev (warnings `InsecureKeyLengthWarning` en la suite) — exigir `SESSION_SECRET` ≥ 32 bytes en prod.
- Archivos sin trackear en `CONTRATO/` (`Anexo_25…`, `PROMPT_Ejecucion…`) — commitear la documentación viva.

---

## 3. MAPA DE REDUNDANCIAS / DUPLICACIONES

| Qué se duplica | Dónde | Cómo unificar |
|---|---|---|
| Escritura de `scale_entry` con 3 semánticas | UI `routes_strategies.py:859` (fuerza `design_only`), API `routes_api.py` (rechaza `enabled`), script `set_scale_execution.py` (pone `execute`) | Un solo módulo de dominio con vocabulario único de modos; UI/API preservan `mode` |
| Edición de calibración SL/TP/windows | UI `/sltp` + `/windows`, API `PATCH /api/strategies/{id}/calibration`, scripts `apply_strategy_calibration_v1.py` | Mantener API+scripts como capa; que la UI llame la misma función de servicio |
| Parsers de listas de cantidades/niveles | `routes_strategies.py:887/901/964/976`, `routes_api.py`, `manage_profiles.py`, `set_scale_execution.py` | Helper compartido `app/services/parsing.py` |
| Webhook TradersPost | `Strategy.traderspost_webhook_url` (`strategy.py:38`) y `StrategyProfile.traderspost_webhook_url` (solo este se lee: `config_resolver.py:220`) | Dejar solo el del profile; migración de limpieza |
| Estado ejecutable | `Strategy.enabled` vs `Strategy.status` (pipeline solo usa status) | Derivar `enabled` o eliminarlo |
| Flag dry-run | env `DRY_RUN` (solo badge), `GlobalProfile.dry_run`, `StrategyProfile.dry_run`, perfil `dry_run` | Una semántica de capas única (P0-2/P0-3) |
| Retries | env `MAX_RETRY_ATTEMPTS`/`RETRY_BACKOFF_SECONDS`, `GlobalProfile.retry_*`, hardcode del cliente | Elegir UNA fuente (sugerido: GlobalProfile) y cablearla |
| Docs de estrategias | Memoria §12 (generación vieja) vs Anexos 23/24/25 (nueva) vs DB | Marcar §12 como histórico; generar la tabla viva con `show_strategy_configs.py` |
| Carpetas de docs | `DOCS/` vs `A REVISAR/DOCS/`, `PROMPTS/` vs `A REVISAR/PROMPTS/`, `FASES_ANTERIORES/` | `A REVISAR/` ya es el archivo histórico: mover ahí todo lo superseded y dejar un LEEME |

**Veredicto carpetas legado:** `A REVISAR/` (archivo, OK mantener), `FASES_ANTERIORES/` y `PROMPTS/` (históricos — mover a `A REVISAR/`), `DOCS/` (manuales de operador vigentes — mantener), `ClaudeCodeTV_290626/` (estudios fuente de calibración — mantener como evidencia, read-only), `NINJATRADER/` (fuente del bridge .cs + HOLC — **vigente**, es parte del sistema), `REPORTES/` (backups/auditoría — vigente).

---

## 4. MATRIZ DE TRAZABILIDAD — PESTAÑA CONFIG (por campo)

Leyenda: ✅ conectado end-to-end · 🟨 conectado con defecto · 🔴 roto (se guarda y no se lee / se lee y no se consume / clave desalineada / sin efecto).

| Campo (UI) | Ruta que guarda | Clave persistida | ConfigResolver la lee | Consumidor real | Efecto | ON/OFF | Estado | Test |
|---|---|---|---|---|---|---|---|---|
| Nombre / activo / timeframe / modo | `/edit` (`routes_strategies.py:764`) | `Strategy.name/asset_symbol/timeframe`, `profile.mode` | asset→`expected_symbol/timeframe` (`config_resolver.py:141-142`); `mode` sí | Guardarraíles L1.7/1.8; `mode` **sin consumidor de gate** (etiqueta) | símbolo/TF esperados | n/a | 🟨 (`mode` decorativo) | test_web |
| URL webhook TradersPost | `/edit:788-798` | `profile.traderspost_webhook_url` (+copia muerta en Strategy) | ✅ `:220` | `_dispatch_approved` / perfiles | destino del envío | n/a | ✅ (columna duplicada P2-10) | test_dispatch_gate_phase2 |
| Token webhook LuxAlgo (regenerar) | `/regenerate-token:393` | `Strategy.webhook_token` | n/a (endpoint webhook `webhooks_luxalgo.py:478-484`) | validación de entrada | 401 si no coincide | n/a | ✅ (plano, P2-8) | test_webhook |
| Kill-switch ARMAR/DRY_RUN | `/dispatch:346-390` | `profile.traderspost_enabled/dry_run` | ✅ semántica OR/AND (`:215-219`) | `resolve_effective_dry_run` | envío real vs DRY_RUN | ✅ | ✅ (pero perfiles lo escapan → P0-2) | test_dispatch_gate_phase2 |
| SL ATR multiplier | `/sltp:813-856` | `profile.sl_atr_multiplier` | ✅ `:222-225` | `SLTPCalculator` L5 + payload | precio del stop | vacío→default 1.5 | ✅ | test_sl_tp_calculator |
| TP ATR multiplier | `/sltp` | `profile.tp_atr_multiplier` | ✅ `:226-229` | L5 + `takeProfit` | bracket opcional | vacío→sin TP | ✅ | test_sl_tp_calculator, test_payload_builder |
| ATR timeframe / period | **sin campo en Config tab** (API/scripts) | `profile.atr_timeframe/atr_period` | ✅ `:230-233` | period ✅ (`filter_pipeline.py:232`); **timeframe: nadie** (L5 usa `signal.timeframe`, `:230-231`) | period sí; timeframe **ninguno** | n/a | 🔴 (timeframe leído nunca aplicado) | — (proponer) |
| Ventanas repetibles | `/windows:473-532` | `pipeline_config_json.windows` | ✅ merge en `session_config_json` (`:172-177`) | `SessionValidator` L2 ANY-match | bloquea entrada fuera de ventana | sin windows → sesión del activo | ✅ | test_windows_anexo08 |
| Frescura entrada/salida (seg) | `/guardrails:419-470` | `guardrails.signal_max_age_*` | ✅ `:162-169` | `_check_staleness` L2 | BLOCK `signal_stale` | vacío→off | 🟨 (mide desde recepción, P2-4) | test_guardrails_anexo08 |
| Enforce símbolo / timeframe | `/guardrails` | `guardrails.enforce_*` | ✅ | L1.7/L1.8 | BLOCK mismatch | OFF→no-op real | ✅ | test_guardrails_anexo08 |
| Filtros de calidad (4 subscores + peso) | `/filters:535-590` | `pipeline_config_json.filters` | ✅ `:179-182` | `QualityScorer` L4 | score 0-100 vs mínimo | OFF→**score 100 sin marca** | 🟨 (Anexo 25 → NX-04) | test_quality_scorer_phase5 |
| `score_minimum` per-estrategia | **sin campo** (alta lo recibe y descarta, `:132`; solo scripts) | `pipeline_config_json.score_minimum` | ✅ `:192-195` | gate L4 | umbral del score | n/a | 🔴 (ineditable/invisible en UI; form de alta engañoso) | test_strategy_config_api parcial |
| Régimen HMM (enabled/TF/permitidos) | `/regime:593-642` | `pipeline_config_json.regime` | ✅ `:184-187` | gate L4 (`filter_pipeline.py:188-202`) | BLOCK `regime_not_allowed` | OFF→clave eliminada, no-op ✅ | ✅ (edge `allowed=[]` vía scripts = no-op, P2-12) | test_hmm_service, test_filter_pipeline |
| Scale entry (niveles/cantidades/max) | `/scale-entry:859-942` | `pipeline_config_json.scale_entry` | ✅ `:199-202` | `PayloadBuilder.build_scaled` + dispatch multi-leg | legs límite + stop común | **UI siempre re-escribe `mode=design_only`** | 🔴 (apaga `execute` sin aviso; textos falsos) | test_scaled_entry_engine (motor sí) |
| Perfiles de riesgo (hasta 8) | `/profiles:945-1063` | `pipeline_config_json.profiles` | ✅ `:205-208` | `dispatch_profiles` → multi-destino | un juego de legs por perfil | enabled por perfil ✅ | 🔴 (perfil puede escalar dry_run/tp_enabled → P0-2; **0 tests**) | — |
| Ficha: stop_ticks / riesgo $/op / máx contratos | `/ficha:709-718` | `pipeline_config_json.risk_reference` | ❌ nadie | ninguno (comentario: "NOT enforced") | ninguno | n/a | 🔴 (guardado nunca leído; la UI no lo marca como referencia) | — |
| Ficha: Dedup (seg) | `/ficha:721-725` | `pipeline_config_json.dedup_seconds` | ❌ | `Deduplicator` hardcodea 60 s (`deduplicator.py:23`) | **ninguno** | n/a | 🔴 (P1-10) | — |
| Ficha: confirmaciones / cuenta objetivo / ruteo | `/ficha:726-739` | `confirmaciones`, `routing` | ❌ | ninguno | ninguno | n/a | 🔴 (metadata; marcar como tal en UI) | — |
| Salidas fuera de ventana | `/ficha:743` | `profile.allow_exits_outside_window` | ✅ `:246-249` | L2 rama de salidas (`filter_pipeline.py:151-158`) | permite/bloquea exits fuera | **no se puede poner False** (unchecked→None=hereda True) | 🟨 | test_filter_pipeline |
| Cierre EOD (HH:MM) | `/ficha:744-753` | `profile.force_flat_time` | ✅ `:252-253` | `ExitManager` | flatten forzado | vacío→**hereda global 15:55** (P1-6) | 🟨 | test_exit_manager_phase4 |
| Status / enabled | `/status:1066` | `Strategy.status` (+`enabled`) | vía Strategy | L1.2 | BLOCK/QUEUE por estado | ✅ | ✅ (`enabled` sin lector → P2-10) | test_filter_pipeline |
| Settings global: mode/score/max_pos/daily_loss/news/retry | `/ui/settings:49-92` | `GlobalProfile.*` | mode/score ✅; `max_open_positions`/`daily_loss_stop` leídos pero **stubs L3**; `news_*` leídos pero **stub L2.3**; `retry_attempts` **ni se lee** | L1.1 (tras NX-01) y L4 | parcial | n/a | 🟨/🔴 (retry 🔴; risk/news guardados-sin-efecto deben marcarse "Fase futura" en UI) | test_global_mode_nx01 |

---

## 5. CÓDIGO MUERTO / DESCONECTADO

- `app/core/security.py` — 0 usos (tokens en plano); `WEBHOOK_TOKEN_SALT` sin uso.
- Settings env sin lector: `MAX_RETRY_ATTEMPTS`, `RETRY_BACKOFF_SECONDS`, `DEFAULT_TIMEZONE`, `MARKET_DATA_FALLBACK_ENABLED`, `NEWS_CACHE_TTL_MINUTES` (`config.py:39-49`); `DRY_RUN` solo alimenta el badge (P0-3).
- Modelos sin ningún uso fuera de `models/__init__` y la migración inicial: `EconomicEvent`, `ConflictLog`.
- Columnas muertas `StrategyProfile`: `profile_name`, `routing_mode`, `allowed_accounts_json`, `allowed_symbols_json`, `timezone`, `days_enabled_json`, `entry_start_time`, `entry_end_time`, `cooldown_minutes`, `daily_profit_lock`, `active` (nadie filtra por ella).
- Columnas muertas `GlobalProfile`: `days_enabled_json`, `entry_start_time`, `entry_end_time`, `entry_cutoff_time`, `global_daily_profit_lock`, `default_quantity`, `news_impact_levels_json`, `retry_attempts`, `retry_backoff_seconds`, `entry_signal_timeout_secs`.
- Columnas muertas/duplicadas `Strategy`: `pine_script_ticker_note`, `traderspost_webhook_url` (duplicado), `enabled` (sin lector de gate).
- Claves de config resueltas pero sin consumidor (stubs declarados): `max_open_positions`, `max_open_positions_symbol`, `max_trades_day`, `max_quantity`, `daily_loss_stop`, `news_filter_enabled`, `news_window_minutes`, `timezone` (el `QualityScorer._time_of_day` y `ExitManager` sí usan `config["timezone"]` — este vive), `atr_timeframe` (P2-2). Nota: `max_quantity` tampoco capa `payload["quantity"]` (`payload_builder.py:47` pasa `signal.quantity` tal cual).
- Carpeta `app/schemas/` vacía y `app/api/internal/` vacía (esqueleto del doc 03 nunca poblado).
- Doc 03 §2: check "1.5 allowed_symbols" y "2.3 noticias" descritos pero no implementados; `news_filter.py`/`strategy_registry.py`/`timezones.py` no existen.
- `PayloadBuilder.build` recibe `strategy` y no lo usa (firma heredada).

---

## 6. IDEAS DE MEJORA ADICIONALES

1. **Config snapshot en la decisión:** `StrategyDecision.config_snapshot_json` está en el contrato (doc 04 / Memoria Ap. D) pero `process_signal` no lo puebla — guardarlo haría auditables los cambios de config entre señal y señal.
2. **Outbox/pending queue para deliveries:** persistir el intento antes del HTTP y reintentar desde un job (hoy el reintento vive en memoria del request; un restart en medio pierde el envío).
3. **Health "profundo":** `/health` (`api/health.py`) devuelve estático; incluir bridge activo, edad del último heartbeat, DB y cola de FAILED para monitoreo externo.
4. **Alertas push** (Telegram/email) para: FAILED de salida, bridge inactivo > N min, posición PENDING/EXITING vieja, contrato por vencer (`rollover_alert.py` ya calcula esto — falta el canal).
5. **Migraciones autogeneradas + check de drift** en CI (comparar metadata vs head de Alembic) para que las columnas muertas no reaparezcan.
6. **Índices**: `strategy_decisions(created_at)`, `(strategy_id, created_at)` y `webhook_deliveries(status, created_at)` para dashboard/analytics cuando crezca el histórico.
7. **Inyección de reloj** unificada (un `Clock` inyectable) para SessionValidator/ExitManager/Deduplicator — hoy cada uno resuelve `now` distinto.
8. **CI mínima** (GitHub Actions o hook pre-push): pytest + ruff; habría atrapado el NX-01 sin pushear.
9. **Rate-limit del endpoint de webhook** por IP/estrategia (hoy solo token) — barato en nginx.
10. **`show_strategy_configs.py` como fuente del doc:** generar la tabla de config efectiva en Markdown y versionarla en `CONTRATO/` tras cada cambio aplicado.

---

## 7. PLAN DE ACCIÓN CONSOLIDADO (backlog priorizado)

Formato: `ID | título | sev | área/archivo | evidencia | propuesta | criterio de aceptación | deps | esfuerzo | riesgo invariantes | estado`

| ID | Título | Sev | Área / archivo | Evidencia | Propuesta concreta | Criterio de aceptación | Deps | Esf. | Riesgo | Estado |
|---|---|---|---|---|---|---|---|---|---|---|
| NX-01 | Commit+push del fix `global_mode` (freno global) | P0 | `filter_pipeline.py:289`, `config_resolver.py:42,95` | `git status`: diff sin commitear; HEAD lee `config["mode"]` | Commitear el diff existente, push, deploy, verificar en server con señal simulada bajo `mode=paused` | En server: entrada con global `paused` → BLOCK `global_paused`; suite verde | — | S | Bajo (fix ya testeado) | ✅ 2026-07-02 commiteado y desplegado; verificado en server: `mode=paused` bloquea entradas |
| NX-02 | Perfiles no pueden escalar el kill-switch | P0 | `dispatch_profiles.py:110-114` | perfil `dry_run:false` reemplaza base | `dry_run = base OR perfil`; `traderspost_enabled = base AND perfil` | Test: base dry_run=True + perfil dry_run=False → destino en DRY_RUN; base tp_enabled=False + perfil True → sin HTTP | — | S | Medio (toca dispatch — solo restringe) | ✅ 2026-07-02 `dispatch_profiles.py` OR/AND por perfil; tests adversariales rojos→verdes; desplegado 2026-07-02 |
| NX-03 | Honrar env `DRY_RUN` en el gate (o eliminarlo) | P0 | `webhooks_luxalgo.py:288-292` vs `common.py:44` | badge usa DRY_RUN; gate no | Añadir `and not settings.DRY_RUN` a `real_send` (y al badge dejarlo igual) | Test: env DRY_RUN=True → siempre DRY_RUN aunque todo esté armado | — | S | Medio (dispatch — solo restringe) | ✅ 2026-07-02 env DRY_RUN es la capa 2 del gate (`webhooks_luxalgo.py`); desplegado 2026-07-02 con `.env` server `DRY_RUN=false` (demo verificado) |
| NX-04 | Calidad UNKNOWN/LOW/MEDIUM/HIGH (Anexo 25 §1-bis) | P1 | `filter_pipeline.py:180,212`, `quality_scorer.py:126`, `payload_builder.py:76-86`, `routes_signals.py:66-76` | score=100 sin filtros se pinta verde | `filters_active` en L4; etiqueta `quality`; `ntexecg_quality`+`filters_active` en extras; ribbon/dashboard muestran UNKNOWN gris sin ✅; `avg_score` solo sobre medidos | Señal sin filtros → `level_4.quality="UNKNOWN"`, ribbon sin verde, extras con etiqueta; con filtros → LOW bloquea, HIGH/MEDIUM correctos; tests nuevos | — | M | Bajo (no cambia el gate numérico) | ✅ 2026-07-02 taxonomía en quality_scorer + traza L4 + extras + ribbon/detalle; score ya no parte en 100 (salidas/blocks tempranos → None). Nota: `avg_score` ahora promedia 0 por señales sin score (antes 100 fantasma) — promediar solo medidos requiere columna, va con NX-26 (pendiente push) |
| NX-05 | Entrada sin precio → BLOCK (no SL basura) | P1 | `signal_normalizer.py:102-106`, `filter_pipeline.py:235` | price=0.0 → SL negativo passed=True | Normalizador: precio inválido → None; L5: `entry_price` None/≤0 → BLOCK `entry_price_missing` | Test: payload sin `price` → BLOCK nivel 5; salida sin precio sigue pasando | — | S | Bajo (endurece fail-closed) | ✅ 2026-07-02 normalizador→None + N5 BLOCK `entry_price_missing`; tests rojos→verdes (pendiente push) |
| NX-06 | Flatten UI despacha cierre real | P1 | `routes_positions.py:42-53`, `position_service.py:104` | solo marca EXITING; flash "enviado" | Resolver config y llamar `dispatch_forced_exit(..., "manual_flatten")`; flash con status real | Test: flatten → WebhookDelivery creado (DRY_RUN en test); estado EXITING→FLAT si SENT | NX-07 recomendado | M | Medio (dispatch de salida — exits son seguros) | ✅ 2026-07-02 flatten despacha vía `dispatch_forced_exit` (actor=admin), FLAT es no-op, flash con status real (pendiente push) |
| NX-07 | Salidas forzadas/reversal a TODOS los perfiles | P1 | `forced_exit.py:72-75` vs `webhooks_luxalgo.py:325` | forced exit solo al webhook base | Iterar `resolve_destinations(config)` en `dispatch_forced_exit`, un delivery por destino | Test multi-perfil: forced exit genera N deliveries con tags `traderspost:<perfil>` | NX-02 | M | Medio (dispatch de salida) | ✅ 2026-07-02 `dispatch_forced_exit` itera `resolve_destinations`, gate por destino, un delivery por perfil (pendiente push) |
| NX-08 | FAILED → estado honesto + alarma | P1 | `webhooks_luxalgo.py:399-422`, `exit_manager.py:21` | PENDING/EXITING eternos | Salida FAILED → UNKNOWN + alerta; entrada FAILED → FLAT; job que reporte PENDING/EXITING > 15 min | Tests de transición; banner/log de posiciones estancadas | — | M | Bajo | ✅ 2026-07-02 FAILED sin SENT: salida→UNKNOWN, entrada→FLAT (`on_exit_failed`/`on_entry_failed` + audit DELIVERY_FAILED); DRY_RUN intacto; `find_stale_positions` + warning en ExitManagerJob (pendiente push) |
| NX-09 | Regla `symbol_busy` en L3 (caso ES/MES) | P1 | `filter_pipeline.py:389-412`, `position_service.py:160` | L3 solo bloquea UNKNOWN/LOCKED | Check L3.4: entrada con `PositionState(account,symbol)` en {PENDING_*,LONG,SHORT,EXITING} → BLOCK `symbol_busy` (opt-out `allow_stacking`); registrar estrategia dueña | Tests: re-entrada misma estrategia bloquea; estrategia B con A abierta bloquea; salidas exentas; reversal sigue funcionando | NX-08 (estados honestos) | M | Medio (puede bloquear señales legítimas — validar en demo) | ✅ 2026-07-02 check L3.4 `symbol_busy` (PENDING_*/LONG/SHORT/EXITING) con `holder_strategy` en la traza; opt-out `allow_stacking`; reversals exentos; **validar en demo** (pendiente push) |
| NX-10 | `dedup_seconds` conectado (o retirado de la ficha) | P1 | `deduplicator.py:23`, `routes_strategies.py:721-725` | guardado, hardcode 60 s | Leer el profile antes del dedupe y pasar `window_seconds=config` (default 60) | Test: dedup_seconds=300 → duplicado a los 2 min se ignora | — | S | Bajo | ✅ 2026-07-02 webhook lee el perfil antes del dedupe; default 60s intacto; visible en config efectiva (pendiente push) |
| NX-11 | UI Scale Entry preserva `mode` + textos veraces | P1 | `strategy_detail.html:281`, `routes_strategies.py:859-942`, `routes_api.py:12,40` | UI resetea execute→design_only; docstrings dicen "motor no existe" | Mostrar mode actual (badge EJECUTA ⚠), preservarlo al guardar niveles/cantidades; corregir docstrings/flash; unificar vocabulario de modos | Guardar niveles sobre estrategia en `execute` NO cambia el mode; test de la ruta | — | S | Bajo | ✅ 2026-07-02 UI+API preservan mode; badge "EJECUTA ⚠"; docstrings/flash corregidos (pendiente push) |
| NX-12 | Campo UI para `score_minimum` per-estrategia | P2 | `routes_strategies.py:132` (descartado), template sin input | umbral GC=55 invisible | Input en la sección Filtros que escriba/lea `pipeline_config_json.score_minimum`; arreglar o quitar el campo del alta | Guardar 55 → resolver lo devuelve → L4 lo aplica; visible en Config | — | S | Bajo | ✅ 2026-07-02 input en form de filtros (1..100, vacío=hereda) + form de alta lo persiste (pendiente push) |
| NX-13 | `force_flat_time`: "sin EOD" explícito por estrategia | P1 | `config_resolver.py:252`, `global_profile.py:31`, `exit_manager.py:57-60` | 24h heredan 15:55 global | Tri-estado (hereda / HH:MM / sin EOD) — p. ej. `force_flat_time="off"` en guardrails JSON; auditar valor real de la fila global en prod | Estrategia 24h con "sin EOD" no se aplana; test de herencia | — | S | Medio (toca cierres automáticos) | ✅ 2026-07-03 `force_flat_off` en pipeline_config (gana al heredado) + checkbox en la ficha (pendiente push) |
| NX-14 | Consumir `atr_timeframe` (o eliminarlo) | P2 | `filter_pipeline.py:230-231` vs `config_resolver.py:232` | knob decorativo | `get_atr(data_symbol, config["atr_timeframe"] or signal.timeframe, period)` (y barras L4 igual); confirmar que la calibración vigente coincide con el TF de señal antes de activar | Test: atr_timeframe=15m con señal 5m → lee barras 15m | — | S | Medio (cambia el ATR efectivo si difieren — validar) | ✅ 2026-07-03 L5 consume `atr_timeframe` (fallback: TF de la señal); default del resolver None (ya no fuerza 5m); registrado en la traza L5 (pendiente push) |
| NX-15 | Retries/timeout desde GlobalProfile | P2 | `traderspost_client.py:93-95`, `global_profile.py:56-58` | Settings UI escribe, cliente hardcodea | Pasar retry_attempts/backoff/timeout al cliente vía config; eliminar env muertos | Cambiar retry_attempts en Settings altera los intentos (test) | — | S | Medio (dispatch) | ✅ 2026-07-03 cliente parametrizado (entradas: retry_attempts/backoff/timeout desde GlobalProfile vía resolver; **exits fijos en 10**); defaults preservan 3/1-2-4/30 (pendiente push) |
| NX-16 | Staleness desde `payload["time"]` | P2 | `signal_normalizer.py:130` | signal_ts = recepción | Parsear `time` de TV a `signal_ts` (fallback: recepción; guardar ambos) | Señal con time 10 min viejo y max_age 60 s → BLOCK `signal_stale` | — | M | Medio (TZ del payload — validar formato real) | ✅ 2026-07-03 `signal_ts` desde `payload.time` (ISO Z/offset/naive→UTC/epoch; fallback recepción); `normalized_at` conserva la recepción — validar formato real de TV en las primeras señales (pendiente push) |
| NX-17 | `cancel_after` por estrategia (TradersPost) | P2 | Anexo 25 §4.7; `pullback_timing.py` | interino 3600 s global | Correr `pullback_timing` por estrategia, fijar p90+colchón en TradersPost, documentar en la ficha (`routing`/nota) | Tabla cancel_after por estrategia aplicada y documentada | datos demo | M | Bajo (config externa) | ✅ 2026-07-03 `pullback_timing --apply` escribe `entry_reserve_timeout_seconds` (=misma clave de NX-28: una sola caducidad); editable en la ficha; recordatorio de fijar el MISMO valor a mano en TradersPost (pendiente push + correr el estudio con datos demo) |
| NX-18 | Reconciliación con broker (diseño P1-8) | P1 | `position_service.py:1-14`, `results_import.py`, `ConflictLog` | estimado sin cierre de lazo | Fases A/B/C del hallazgo P1-8 (import→FLAT conciliado; expiración→UNKNOWN; poll broker→conflict_log) | Fase A: import de un trade cerrado deja la posición FLAT; Fase B: posición vieja pasa a UNKNOWN | NX-08 | L | Medio | 🟨 2026-07-02 **Fase A** hecha: `_reconcile_positions` en el import — FLAT solo con match exacto por signal_id + trade cerrado + `entry_signal_id` coincidente (heurístico/abiertos/reabiertas no se tocan), audit RECONCILE, `positions_reconciled` en el summary. Fases B (expiración→UNKNOWN) y C (poll broker) pendientes (pendiente push) |
| NX-19 | Tests de `dispatch_profiles` + deduplicador rekey | P2 | sin tests (`grep` vacío) | P0-2 no tenía red | Suite: herencia, cap_quantities, dedupe por webhook, kill-switch por perfil, `rk:`/`dup:` | Cobertura de los caminos listados; verde | NX-02 | M | Nulo | ✅ 2026-07-02 `tests/test_dispatch_profiles.py` (20 tests: herencia, cap, dedupe, tags, kill-switch por perfil, env DRY_RUN) |
| NX-20 | Clone copia `pipeline_config_json` (saneado) | P2 | `routes_strategies.py:1179-1187` | clon pierde windows/filters/etc. | Copiar el JSON, limpiando `profiles` (webhooks) y forzando dry_run/candidate | Clon conserva ventanas/filtros; queda desarmado | — | S | Bajo | ✅ 2026-07-03 copia pipeline_config_json saneado (sin `profiles`, scale→design_only), dry_run=True/tp_enabled=False, token propio (pendiente push) |
| NX-21 | Auto-creación de estrategias: flag de entorno | P2 | `webhooks_luxalgo.py:127-138` | señal desconocida crea Strategy | `ALLOW_STRATEGY_AUTOCREATE=false` en prod → decisión BLOCK `unknown_strategy` (RawSignal se guarda igual) | Con flag off, id desconocido no crea fila y queda auditado | — | S | Bajo | ⬜ |
| NX-22 | Tokens de webhook hasheados (o retirar security.py) | P2 | `strategy.py:37`, `core/security.py` (muerto) | tokens en plano en DB | Guardar hash+salt, mostrar token solo al generarlo; migración de los existentes | DB sin tokens en claro; login de webhook sigue funcionando | — | M | Medio (coordinar re-alta de alertas) | ⬜ |
| NX-23 | Limpieza de columnas/modelos/env muertos | P2 | §5 de este reporte | ~25 columnas sin lector | Migración de drop + borrar `EconomicEvent`/`ConflictLog` (si NX-18 no lo usa) + env muertos; marcar en UI los campos "referencia" | Modelos == esquema usado; suite verde; alembic check sin drift | NX-18 (decide ConflictLog) | M | Bajo (solo drops verificados) | ⬜ |
| NX-24 | Analytics con alias de `strategy_id` legacy | P3 | `routes_analytics.py:78-107`, `rename_strategy.py` | series partidas tras renames | Tabla/JSON `strategy_aliases`; analytics agrupa por id canónico y marca retiradas | Rename no parte la serie; ids retirados anotados | — | S | Nulo | ✅ 2026-07-03 mapa de alias desde AuditLog (`renamed_from` que ya deja rename_strategy; sin migración), cadenas resueltas; Analytics agrupa por id canónico + badges "retirada"/"huérfana" (pendiente push) |
| NX-25 | Sincronizar docs (03, Memoria §12) y archivar legado | P3 | §2 P2-11, §3 carpetas | doc describe módulos inexistentes | Actualizar doc 03 a la estructura real; marcar Memoria §12 como histórico y enlazar Anexo 23/24; mover FASES_ANTERIORES/PROMPTS a `A REVISAR/` | Doc 03 == árbol real; un solo lugar para históricos | — | S | Nulo | ⬜ |
| NX-26 | Nits: parsers compartidos, `enabled`/webhook duplicados, régimen `allowed=[]`, `avg_score`, reloj inyectable, ficha `allow_exits=False` | P3 | §2 P3 / §3 | varios | Lote de limpieza en un PR pequeño por ítem | Suite verde; sin cambios de comportamiento salvo los documentados | NX-23 | M | Bajo | ⬜ |
| NX-28 | Liberar la reserva de symbol_busy sin fill (derivado de la validación de NX-09; requisito del diseño multi-estrategia con entradas pullback) | P1 | `forced_exit.py`, `position_service.py`, `webhooks_luxalgo.py`, `scheduler.py` | reserva LONG fantasma tras cancel_after bloqueaba el símbolo para siempre | El dispatch registra `entry_style` (market/limit_only); el sweep resetea a FLAT: PENDING_* viejos y LONG/SHORT `limit_only` viejos (timeout `entry_reserve_timeout_seconds` ≈ cancel_after, default 3600, override por estrategia); mercado/legacy/EXITING no se tocan; audit RESERVE_RELEASED | Reserva límite de 2h → FLAT y símbolo libre; mercado nunca se libera; fresca se conserva | NX-09, NX-17 (cancel_after real por estrategia) | M | Medio (libera un símbolo que podría estar lleno — mitigado por exits de LuxAlgo) | ✅ 2026-07-03 implementado con tests adversariales (pendiente push) |
| NX-27 | Reversal respeta L1 (fusionado del reporte de Opus): quarantined/retired/candidate no despachan ni el cierre | P1 | `webhooks_luxalgo.py::_classify_and_handle_reversal` | el cierre se despachaba ANTES de validar `strategy.status` | Cortocircuitar el reversal para esos status → BLOCK/QUEUE normal con 0 deliveries; `paused` sigue cerrando (salidas prioritarias) | Señal opuesta con estrategia quarantined → BLOCK `strategy_quarantined`, 0 deliveries; paused → cierra y bloquea la entrada | — | S | Bajo (solo restringe) | ✅ 2026-07-02 implementado con tests adversariales (pendiente push) |

**Orden de ataque sugerido:** NX-01 (hoy, es un push) → NX-02/NX-03 (kill-switch, cambios que solo restringen) → NX-05, NX-10, NX-11, NX-12 (quick wins seguros de Config) → NX-04 (calidad UNKNOWN, el pendiente estrella) → NX-06/NX-07/NX-08 (salidas y estados, con tests) → NX-09 (symbol_busy, validar en demo) → NX-13..NX-17 → NX-18 (reconciliación, por fases) → NX-19..NX-26.

---

*Fin de la revisión. No se modificó código. Espero aprobación para comenzar a atacar el backlog ítem por ítem (cambio + tests + visto bueno antes del siguiente).*
