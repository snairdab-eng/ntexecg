# AUDITORÍA TOTAL DE NTEXECG — Fable 5 · 2026-07-12

> Auditoría de solo lectura (cero cambios de código, cero commits). Compañeros:
> `HANDOFF_Contexto_2026-07-12.md`, `AUDITORIA_Fable_Cierre_2026-07-12.md`,
> `SPEC_Luxy_Integracion_Lotes_2026-07-10.md`. Método: lectura del código real
> (motor de decisión, seguridad, capa de datos, jobs) + verificación empírica de
> los invariantes y de un hermano de LX-6. Donde un hallazgo contradice o amplía
> la auditoría de cierre, se dice explícito.

---

## Resumen ejecutivo

El sistema está **sano en sus invariantes de misión**: los seis invariantes rojos
(SL obligatorio, `passed⇒sl_price`, exits exentos de L3/L4/L5, participación 100%,
kill-switch intocable por los puentes, R-T10, guardarraíles siempre-on,
PortfolioGuard regla 1 fail-closed) se verificaron **en código y con lógica
adversarial** y se sostienen. No encontré ninguna vía que envíe una entrada a
TradersPost sin stop, ni que aplique la fila OOS, ni que un puente toque el
kill-switch. La deuda real está en los **bordes de la capa de datos y de la
operación**, no en el motor de decisión.

Hallazgos nuevos de peso: (1) un **hermano de LX-6 en la ruta de ESCRITURA** de
`ohlcv_bars` — `parse_bar_time` descarta silenciosamente el offset TZ (verificado
empíricamente); hoy latente porque el bridge entrega ET-naive, pero es exactamente
la clase de bug que envenenó el estudio. (2) La arquitectura de **jobs + estado en
memoria asume un solo worker de uvicorn** sin candado ni documentación en el código;
con `--workers>1` habría cierres forzados duplicados. Lo demás es deuda P2/P3
conocida o menor.

**Conteo por severidad:** P0 = 0 · P1 = 3 · P2 = 7 · P3 = 5.

Estado de la suite: el handoff la reporta 1004 passed / 2 skipped (gated por datos
reales de ES). Se relanzó como verificación read-only; ver §"Nota de la suite" al
final.

---

## Hallazgos por severidad

### P1 — Riesgo real

#### P1-1 · Hermano de LX-6 en la ESCRITURA: `parse_bar_time` descarta el offset TZ en silencio
- **Archivo:** `app/services/bar_store.py:43-59` (y el uso en `_rows_from_bars:76`).
- **Evidencia (verificada empíricamente):**
  ```python
  def parse_bar_time(raw):
      if isinstance(raw, datetime):
          return raw.replace(tzinfo=None)          # aware → naive a ciegas
      s = str(raw).strip().replace("T", " ")
      ...
      return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")   # s[:19] corta el offset
  ```
  Corrida real:
  `parse_bar_time("2026-07-02T14:30:00+00:00") → 2026-07-02 14:30:00` (naive),
  `parse_bar_time("…Z") → 14:30:00`, `datetime(…, tzinfo=utc) → 14:30:00`.
  Un timestamp UTC 14:30 se guarda como pared "14:30" **rotulado ET** — el mismo
  envenenamiento que causó el PF 184 (LX-6), pero en el escritor, no en el lector.
- **Impacto en la misión:** la lectura (`stitch_from_db`) ya se blindó con `_et_naive`
  y aborta fail-closed si el solape es pobre; pero si un futuro bridge/símbolo emite
  ISO con offset o un `datetime` aware, esta función lo persiste mal-TZ **sin avisar**,
  y la costura podría no detectarlo si el volumen mal-etiquetado es homogéneo. La
  detección (`audit_ohlcv_tz`) es manual y posterior. La lección del handoff
  ("jamás `.replace(tzinfo=None)` a ciegas") se cumple en el lector pero **se viola
  en el escritor**.
- **Contradice/ amplía cierre:** la auditoría de cierre da la capa de datos por
  "blindada esta etapa" y marca solo la *secuencia del server* como P0. Este es un
  hueco NUEVO en el escritor, del linaje que se pidió cazar ("busca los hermanos").
- **Severidad:** P1 (latente hoy — el bridge .NET entrega ET-naive — pero es fail-open
  silencioso en el punto exacto de la clase de bug ya sufrida).
- **Fix propuesto (1-3 líneas):** en `parse_bar_time`, si el valor es aware
  (o el string trae offset/`Z`), convertir a `America/New_York` y luego soltar el tz
  (reusar `lab_analyze._et_naive`), nunca truncar; si trae offset y no se puede
  normalizar, devolver `None` (fail-closed) en vez de un naive mal-rotulado.

#### P1-2 · Jobs de fondo + estado en memoria asumen un solo worker, sin candado ni nota en el código
- **Archivos:** `app/main.py:27-58` (lifespan arranca 4 schedulers), `app/core/scheduler.py`
  (Heartbeat/ExitManager/MarketBars/HMM), `app/core/login_guard.py:21`,
  `app/core/token_once.py:18`, `app/core/auth.py:25` (`_sessions_valid_from`),
  `app/web/routes_riesgo.py:62` (`JOBS`), `routes_strategies.py:598,674` (`LUXY_JOBS`,
  `LUXY_EVAL_JOBS`), `manifest_store._INTEGRAR_LOCKS`.
- **Evidencia:** cada worker de uvicorn ejecuta `lifespan` completo → **N schedulers**.
  `ExitManagerJob._run` (scheduler.py:171) hace `exit_manager_sweep`, que lee posiciones
  `LONG/SHORT` y despacha cierres; con dos schedulers concurrentes ambos leen `LONG`
  y despachan el cierre **antes** de que cualquiera confirme `EXITING` (sesiones
  independientes) → **doble cierre forzado**. `MarketBarsUpdater` duplica escrituras
  (idempotente, inofensivo) y `HMMTrainerJob` reentrena por duplicado. Los locks de
  integración, `JOBS`, el lockout de login, `token_once` y el watermark de revocación
  son **por proceso**: con >1 worker se vuelven inconsistentes (un token revocado en un
  worker sigue válido en otro; el lock de integración no serializa entre workers).
- **Impacto en la misión:** el despliegue documentado (`MEMORIA_TECNICA`: uvicorn
  127.0.0.1:8000, README sin `--workers`) implica 1 worker → hoy correcto. Pero **no hay
  candado, elección de líder ni comentario** que impida escalar workers; el día que
  alguien ponga `--workers 2` para "ir más rápido", se rompen cierres forzados y
  seguridad en memoria sin ninguna señal.
- **Contradice/ amplía cierre:** el cierre solo anota que revocación/lockout son en
  memoria y se pierden al reiniciar (aceptable single-admin). **No menciona** la
  duplicación de schedulers ni el doble cierre bajo múltiples workers — es NUEVO.
- **Severidad:** P1 (condicionado a workers>1, pero sin ninguna barrera en el código).
- **Fix propuesto:** documentar y forzar 1 worker (comentario en `lifespan` + nota de
  despliegue), o gatear los schedulers con un advisory-lock de Postgres
  (`pg_try_advisory_lock`) para que solo un proceso corra cada job.

#### P1-3 · Escritura de `RawSignal` sin autenticar en cada POST del webhook (amplificación de almacenamiento)
- **Archivo:** `app/api/webhooks_luxalgo.py:576-592`.
- **Evidencia:** el endpoint guarda **y hace `commit`** de un `RawSignal` con
  `payload_json` completo **antes** de validar el token; token inválido → 401 *después*
  de haber escrito la fila. Cualquier POST a `/webhooks/luxalgo/{cualquier_id}?token=x`
  crea una fila persistida.
- **Impacto en la misión:** es deliberado (rastro de auditoría de tokens inválidos), pero
  hace la tabla `raw_signals` escribible por un no autenticado sin cota → vector de
  crecimiento/DoS de almacenamiento y de ruido en auditoría. `client_ip` sale de
  `CF-Connecting-IP` sin validar (el cierre ya pide firewall a IPs de Cloudflare, tarea
  del operador), así que ni el rate-limit por IP es confiable a nivel de app.
- **Severidad:** P1 (mitigable por firewall CF, pero a nivel de código es una escritura
  DB no autenticada y no acotada).
- **Fix propuesto:** cota de tamaño del body + límite/purga de `raw_signals` con
  `token_valid=false` (retención corta), y/o rate-limit por IP antes de persistir; no
  confiar `CF-Connecting-IP` salvo detrás del firewall documentado.

---

### P2 — Deuda / riesgo acotado

#### P2-1 · Regla 1 del Portafolio se puede APAGAR desde la UI sin confirmación
- **Archivo:** `app/web/routes_portfolio.py:59-87`.
- **Evidencia:** `toggle_rule` apaga cualquier regla (incluida la 1, el único guardarraíl
  de portafolio fail-closed ON) con `enabled != "on"`; queda auditado
  (`PORTFOLIO_RULE_CHANGE`) pero **sin la fricción de CONFIRMAR** que sí exige armar el
  dispatch (`routes_strategies.py:1621`).
- **Impacto:** desactivar en un clic la protección "una posición por activo" es un cambio
  de riesgo de mayor peso que difícil de deshacer por error. No rompe un invariante (la
  regla es un toggle por diseño), pero la asimetría de fricción es incoherente.
- **Severidad:** P2.
- **Fix propuesto:** exigir confirmación explícita para APAGAR la regla 1 (o cualquier
  regla ON), espejando el patrón CONFIRMAR del dispatch.

#### P2-2 · `_rule_max_daily_loss` usa medianoche UTC como frontera del "día"
- **Archivo:** `app/services/portfolio_guard.py:439`.
- **Evidencia:** `start = datetime.now(timezone.utc).replace(hour=0,…)` — el corte de la
  pérdida diaria es medianoche UTC (≈19:00/20:00 ET), no la frontera de sesión ET.
- **Impacto:** la pérdida diaria del portafolio se "reinicia" a media tarde ET, partiendo
  la sesión regular en dos días contables. Regla P-B **inerte** hoy, pero cuando se
  encienda el tope no medirá el día de trading real.
- **Severidad:** P2.
- **Fix propuesto:** anclar el inicio del día a la TZ de trading (America/New_York) o a la
  frontera de sesión configurada, no a UTC.

#### P2-3 · `AuditService.log` hace `flush()` dentro del try que traga excepciones
- **Archivo:** `app/services/audit_service.py:43-53`.
- **Evidencia:** `db.add(entry); await db.flush()` en el try; si el flush falla, se traga,
  pero la sesión async queda potencialmente en estado inválido y el `commit` posterior del
  llamador puede fallar.
- **Impacto:** un fallo de auditoría (raro) podría envenenar la transacción del flujo que
  lo invocó, contradiciendo el contrato "audit nunca rompe el flujo".
- **Severidad:** P2.
- **Fix propuesto:** aislar el intento de auditoría en un savepoint (`db.begin_nested()`)
  o construir el `AuditLog` sin `flush` inmediato, dejando que el `commit` del flujo lo
  persista.

#### P2-4 · Llaves huérfanas en `pipeline_config_json` (confirmado del cierre)
- **Archivos:** `app/services/config_resolver.py` (nunca lee `filters.*` como gate real de
  producción salvo score; `regime` opt-in; `guardrails.enforce_*` **ignorado** por diseño
  siempre-on, `config_resolver.py:193-194`).
- **Evidencia/impacto:** verificado — `enforce_symbol_match`/`enforce_timeframe_match`
  persistidos en la ficha se ignoran (el resolver los fuerza a True). Las llaves siguen
  round-trip pero huérfanas de UI. Coincide con lo que el cierre lista; lo **confirmo**,
  no es nuevo.
- **Severidad:** P2 (deuda de limpieza).
- **Fix propuesto:** lote de limpieza — retirar/renombrar llaves muertas y documentar cuáles
  se conservan a propósito (ver inventario abajo).

#### P2-5 · `_luxy_latest` / lectura de `runs/luxy_*.json` por orden lexicográfico de fecha
- **Archivos:** `routes_strategies.py:601-611` (`_luxy_latest`), `routes_riesgo.py:173-187`
  (`_latest_run_file`/`_latest_estudio`).
- **Evidencia:** "última corrida" = `sorted(glob(...))[-1]`. Con nombres `luxy_YYYY-MM-DD.json`
  el orden lexicográfico coincide con el cronológico **mientras** la fecha sea ISO. Un sufijo
  distinto (p. ej. `_{hash}`) o formato no-ISO rompería la selección del "más reciente".
- **Impacto:** el puente Aplicar y el de ventanas leen "el último estudio"; si por cualquier
  motivo entra un nombre no ISO, se aplicaría un estudio que no es el más reciente. Hoy los
  nombres son ISO puros → correcto, pero es un supuesto frágil sin guardia.
- **Severidad:** P2.
- **Fix propuesto:** ordenar por `mtime` real (o parsear la fecha del nombre) en vez de por
  orden lexicográfico del glob.

#### P2-6 · `detect_tz_offset` se re-ejecuta en cada corrida de Luxy (snapshot HOLC no fija el offset)
- **Archivos:** `scripts/mr_luxy.py:960` (`detect_tz_offset` dentro de `_load_master`),
  `scripts/lab_analyze.py:370`.
- **Evidencia:** el estudio re-detecta el offset TZ del CSV vs el HOLC en cada corrida en vez
  de leerlo del manifest (`manifest["tz"]` ya lo guarda al integrar). Dos rutas de detección
  del mismo offset (integrar vs estudiar) pueden divergir si el HOLC snapshot y el master se
  desincronizan.
- **Impacto:** riesgo bajo (misma función determinista), pero es una segunda fuente de verdad
  para el offset — justo el eje del bug LX-6.
- **Severidad:** P2.
- **Fix propuesto:** que el estudio consuma el offset persistido en el manifest cuando exista,
  y solo recalcule como fallback (con aviso si difiere).

#### P2-7 · `LUXY_EVAL_JOBS` / `LUXY_JOBS` sin lock ni expiración de tarea
- **Archivo:** `routes_strategies.py:695-724`.
- **Evidencia:** `luxy_evaluar` sobreescribe `LUXY_EVAL_JOBS[clave]` sin chequear si hay uno
  corriendo (a diferencia de `luxy_calcular`, que sí devuelve 409). Dos "Recalcular" rápidos
  lanzan dos subprocesos; el segundo pisa el estado del primero.
- **Impacto:** menor (diagnóstico, no toca producción), pero desperdicia CPU y puede mostrar
  el resultado del subproceso equivocado.
- **Severidad:** P2.
- **Fix propuesto:** espejar la guardia de `luxy_calcular` (409 si `status=="running"`), o
  cancelar la tarea previa antes de lanzar otra.

---

### P3 — Estilo / notas

- **P3-1 · `due_exit` forced_close_eod sin cota superior de hora** (`app/services/exit_manager.py:59`):
  `now_local.time() >= fct` cierra en cada barrido tras el EOD; una posición abierta *después*
  de `force_flat_time` con `allow_overnight=True` se cerraría de inmediato. Config contradictoria,
  borde. *Fix:* acotar el disparo a una ventana [fct, fct+Δ) o exigir cruce del umbral.
- **P3-2 · Timing de username no constante** (`app/core/auth.py:91-93`): `username != UI_USERNAME`
  retorna antes de bcrypt → oráculo de enumeración. **Ya listado por el cierre** (bcrypt dummy);
  lo confirmo tal cual, no peor. *Fix:* correr un bcrypt dummy en el camino de usuario incorrecto.
- **P3-3 · TOTP sin guard de replay** (`app/core/totp.py`): código válido reusable ~90s. **Ya
  listado por el cierre** (SEC-2). Confirmado, no peor.
- **P3-4 · CSP con `unsafe-inline`/`unsafe-eval`** (`app/main.py:77-86`): documentado y asumido
  (Alpine estándar). **Ya listado por el cierre.** Confirmado.
- **P3-5 · `resolve_effective_dry_run` usa default `False` para `DRY_RUN`** (`webhooks_luxalgo.py:335`):
  el comentario lo justifica y el `env_enabled` (default False) sigue gateando, así que un atributo
  ausente no abre envíos. No es bug; se anota por ser un default contra-intuitivo en una función de
  kill-switch.

---

## Verificación de invariantes de misión (alcance A)

| Invariante | Estado | Evidencia |
|---|---|---|
| Entrada sin SL válido → BLOCK | ✅ | `sl_tp_calculator.py:87-110` (sin ATR/sin backstop y sin precio → `passed=False`); `payload_builder.py:92` lanza `ValueError` si entrada sin `sl_price`. |
| `passed=True ⇒ sl_price ≠ None` | ✅ | `sl_tp_calculator.py:162-190`: guarda P0 final (lado correcto + >0), y cualquier violación → `passed=False, sl_price=None`. |
| TP: opcional pero si presente, válido | ✅ (matiz) | El TP no es obligatorio (LuxAlgo cierra); `bracket_ok` bloquea solo si el TP presente cae del lado equivocado. La frase "SL+TP" del prompt es imprecisa: el sistema exige **SL**, TP opcional. |
| Exits exentos de L3/L4/L5 | ✅ | `filter_pipeline.py:174-258` (skips explícitos para `is_exit`). |
| Participación 100% (sin descarte silencioso) | ✅ | Único punto tolerado: gate 1.8 `tf_not_verified` anota y NO bloquea (`filter_pipeline.py:377-410`). QUEUE_FOR_REVIEW/BLOCK/reversal-closed-only quedan **auditados** con motivo, no son descartes silenciosos. |
| Kill-switch intocable por los puentes | ✅ | `_merge_activacion` (`routes_riesgo.py:538-555`) y los aplicar de Luxy/ventanas solo tocan llaves de `pipeline_config_json` (backstop/tp_nominal/scale_entry/windows/reserve); nunca mode/dry_run/traderspost_enabled/status. |
| R-T10 (fila OOS jamás aplicable) | ✅ | `activacion_from_study` lee **solo** `levers_in_sample` (`mr_luxy.py:866-900`); test adversarial en `test_luxy_aplicar_l5.py:22-124`. |
| Guardarraíles siempre-on no desactivables | ✅ | `config_resolver.py:193-194` fuerza `enforce_symbol_match/enforce_timeframe_match=True` ignorando lo persistido; test en `test_parte_c.py`. |
| PortfolioGuard regla 1 fail-closed | ✅ | `portfolio_guard.py:490-559`: toda lectura fallida (catálogo, posiciones, activo indeterminado) → BLOCK; test `test_portfolio_pa.py`. Matiz: la regla se puede APAGAR desde UI (P2-1). |

No hallé, buscando adversarialmente (parámetros raros, perfiles que overridean bracket,
reversals, órdenes de llamada, degradado), ninguna vía que rompa estos invariantes. El
camino de perfiles (`dispatch_profiles.recompute_bracket`) es fail-closed: bracket no
computable → cae al bracket base del L5 (`webhooks_luxalgo.py:399-409`), nunca envía sin stop.

---

## Seguridad (alcance B)

Sano y confirmado: `require_auth` a nivel de router cubre **todos** los endpoints nuevos
(luxy/exploracion, ventanas/aplicar, evaluar, aplicar) — están bajo `strategies_router` con
`Depends(require_auth)` (`main.py:132-141`); solo health/webhook/login son públicos. Token del
webhook: hash SHA-256+salt con `hmac.compare_digest` (tiempo constante), dedupe, one-time display
(`token_once`, TTL 60s, un solo read). Validación de entrada de LX-10 (`_luxy_expl_parse`) acota
tamaño (8KB) y llaves. Upload de HOLC con doble candado anti-traversal (`routes_strategies.py:581-586`).
Sin `eval`/formato Jinja peligroso en los payloads; `| tojson` es la convención.

Nuevo/peor que el cierre: **P1-3** (escritura no autenticada de `RawSignal`). Lo demás
(replay TOTP, timing username, `unsafe-eval`) es **igual a lo descrito** por la auditoría de
cierre — se lista en P3-2/3/4 solo como confirmación, sin agravante.

---

## Capa de datos (alcance C)

El lector está blindado (LX-6): `_et_naive` (`lab_analyze.py:251-262`) convierte aware→NY→naive,
`stitch_from_db` es triple fail-closed (solape ≥12, inconsistencia ≤0.01%, continuidad de rejilla),
tripwire de plausibilidad. **El hermano vive en el ESCRITOR** (P1-1): `parse_bar_time` trunca el
offset. Segundo eje frágil: el offset TZ se **re-detecta** en el estudio en vez de leerse del
manifest (P2-6). Snapshot HOLC por-clave correcto (`nt_riesgo.py:521`), con aviso de inconsistencia
manifest↔snapshot ya implementado (`nt_riesgo.py:663-666`). DST/sesiones distintas: la conversión
usa `America/New_York` (ZoneInfo, DST-aware) en lector y validador de sesión — correcto.

---

## Lógica de programación (alcance D)

- **Concurrencia de jobs:** integrar está serializado por estrategia con lock compartido
  (`_lock_integrar`, `manifest_store`) que cubre subproceso + manifest (`routes_riesgo.py:904`);
  calcular/renombrar/eliminar chequean `JOBS[...]=="running"` → 409. **Falla el multi-worker**
  (P1-2): los locks son por proceso y los schedulers se duplican.
- **Estado en memoria multi-worker:** P1-2. Worker count no fijado en el repo (README/MEMORIA:
  uvicorn sin `--workers` → 1 hoy).
- **Excepciones que degraden a fail-open:** no encontradas en la ruta de decisión. `is_active`
  con excepción → `active=False` → BLOCK (`filter_pipeline.py:396-405`). Envío real fallido →
  UNKNOWN/FLAT honesto (NX-08). Los jobs capturan y hacen rollback sin propagar (correcto para
  jobs; ver P2-3 para el matiz de auditoría).
- **Aritmética de dinero:** todo en `float` (position_sizing, worst_case_loss, portfolio). Para
  paper/demo es aceptable; los redondeos son a 2 decimales en los puntos de reporte. No es un
  riesgo de misión (el dinero real no se mueve). Nota, no hallazgo.
- **Off-by-one splits/ventanas:** `split_in_out` (`lab_analyze.py:474-479`) determinista y correcto;
  conversiones weekday()↔%w (`(dow+1)%7`) consistentes entre `_fuera_por_lado`,
  `_compilar_desde_toggles` y `evaluate_overrides`. Sin off-by-one.

---

## Inventario de código muerto e higiene (alcance E)

**Muerto de verdad (candidato a retiro):**
- `app/templates/riesgo.html` — 0 referencias desde `.py` (grep). Retenido a propósito para
  rollback de L7b (el cierre lo confirma); **marcar con fecha de caducidad**, no dejar indefinido.
- `scripts/archivo/*` (`apply_anexo21_demo.py`, `apply_profile_policy_v1.py`,
  `compare_filter_decisions.py`, `diag_profiles.py`, `eval_quality_filters.py`,
  `revert_asset_profiles_v1.py`) — carpeta de archivo; muertos por diseño (histórico).
- Llaves huérfanas en `pipeline_config_json`: `filters`/`regime`/`guardrails.enforce_*` (P2-4).
- `tabla_a` legacy — persiste en el payload del estudio; el dashboard usa `table3`
  (`mr_luxy.py:786-790`). Aún lo consumen tests (`test_mr_luxy_l2.py`, `test_estrategias_l1.py`)
  y el CLI (`mr_luxy.py:1167`) → **retención semi-deliberada**; renombrar/documentar.
- `routes_strategies.py:1309-1520` computa `regime_now`/`ultima_eval` pero **ninguna plantilla
  los consume** (grep en `app/templates` → 0). El cierre ya lo lista; **confirmado muerto de UI**.

**Retenido a propósito (NO tocar):**
- `quality_scorer.py` / `hmm_service.py` / `hmm_trainer.py` — el Lab y el gate opt-in los importan
  (`filter_pipeline.py`, `routes_lab.py`, `test_parte_c.py`). Vivos.
- Providers stub `TradovateAPIProvider`/`DatabentoProvider` (`market_data_service.py:295-318`) —
  Fase 5, `NotImplementedError` explícito; retención declarada.
- `StrategyTemplate` + `strategies.template_id` — deprecados no-destructivos (main.py:142).

**Dependencias:** todas las declaradas en `pyproject.toml` tienen consumidor
(`yfinance`, `pandas-ta`, `hmmlearn`, `matplotlib`, `apscheduler`, `bcrypt`, `pyjwt`, etc.).
`beautifulsoup4`/`requests`/`pytz`/`python-dateutil` **sin import directo** en `app`/`scripts`
(posible arrastre transitivo de yfinance/pandas-ta) → candidatos a verificar/retirar (P3).

---

## Huecos de tests (alcance F)

- **Multi-worker / duplicación de schedulers (P1-2): sin test.** Ningún test cubre "dos schedulers
  concurrentes no doble-cierran". Difícil de simular, pero al menos un test de que
  `exit_manager_sweep` es idempotente si se corre dos veces sobre el mismo estado (el segundo pase
  no re-despacha porque la posición ya no está en OPEN_STATES) daría cobertura barata.
- **P1-1 (`parse_bar_time` con TZ):** no hay test que verifique el comportamiento con entrada
  aware/ISO-con-offset. Un test adversarial que fije la convención (aware → ET) blindaría el hermano
  de LX-6 igual que `test_stitch_lx6.py` blindó el lector.
- **Escritura no autenticada de `RawSignal` (P1-3):** hay tests de token inválido → 401, pero
  ninguno sobre la cota/volumen de filas persistidas por peticiones no autenticadas.
- **Asserts débiles / pasa-por-accidente:** `test_parte_c.py:63-70` acepta *cualquiera* de tres
  símbolos (`score_signal` OR `compute_score` OR `QualityScorer`) — pasa aunque la API cambie de
  nombre; es laxo por diseño (tolerancia a refactor) pero no verifica comportamiento.
- **2 skipped (gated):** `test_lab_consistency.py:22` (pytestmark skipif) y los `skipif(not _HAY_DATOS)`
  de ES real — son **gates por datos reales de ES**, no bugs; correcto que se salten en NTDEV sin el
  dataset. No son los "2 skipped" preocupantes; son intencionales.
- **Buena cobertura adversarial confirmada:** R-T10 (`test_luxy_aplicar_l5`), guarda P0 del bracket
  (`test_bracket_guard_p0`), stitch fail-closed (`test_stitch_lx6`), PortfolioGuard fail-closed
  (`test_portfolio_pa`), siempre-on (`test_parte_c`), lockout/2FA (`test_security_sec1`),
  concurrencia de integración por lock (`test_lab_datos.py:170-187`, `test_riesgo_ui.py:378`).

---

## Primeros 5 lotes de fix sugeridos (en orden)

1. **FIX-1 (P1-1) — Blindar el escritor de barras (hermano de LX-6).** Corregir `parse_bar_time`
   para convertir aware→ET (no truncar) y fail-closed ante offset no normalizable; añadir test
   adversarial de convención TZ. *Toca capa de datos → revisión profunda obligatoria.*
2. **FIX-2 (P1-2) — Pinnear/gatear los jobs a un solo ejecutor.** Comentario + nota de despliegue
   forzando 1 worker, o advisory-lock de Postgres por scheduler; test de idempotencia de
   `exit_manager_sweep`. *Toca participación/despacho → revisión profunda.*
3. **FIX-3 (P1-3) — Acotar la escritura no autenticada del webhook.** Límite de tamaño de body +
   retención/purga de `raw_signals` con token inválido; no confiar `CF-Connecting-IP` fuera del
   firewall. *Toca seguridad → revisión profunda.*
4. **FIX-4 (P2-1, P2-7) — Fricción y guardias de UI.** CONFIRMAR para apagar la regla 1 del
   Portafolio; guardia 409 en `luxy_evaluar` (espejo de `luxy_calcular`). Lote JS → smoke de navegador.
5. **FIX-5 (P2-2, P2-3, P2-5, P2-6) + limpieza (E).** Frontera de día ET en regla 5; auditoría en
   savepoint; ordenar runs por mtime; que el estudio reuse el offset del manifest; retirar llaves
   huérfanas / `tabla_a` legacy / `regime_now` muerto de UI con fecha de caducidad para `riesgo.html`.

---

## Nota de la suite

Se corrió la suite completa como verificación read-only. Resultado confirmado:
**1004 passed, 2 skipped, 554 warnings en 839.15s (~14 min)** — coincide exacto con la baseline
del handoff. Esta auditoría no cambió una línea de código.

Aviso de método: el primer intento con `-o faulthandler_timeout=300 --timeout=600` se abortó por
el `faulthandler_timeout` GLOBAL de 300s (dispara por el tiempo de PARED total, ~14 min, no por
un assert). Correcto: es el "colgado flaky" que advierte el handoff — ese guard global es más
corto que la duración legítima de la suite completa. Con solo timeout por-test (`--timeout=120`)
la suite pasa limpia.

Los 2 skipped son intencionales (skipif por dataset ES real ausente en NTDEV / `test_lab_consistency`
gated), no fallos. Nota menor (test-only): la suite emite `InsecureKeyLengthWarning` porque el
`SESSION_SECRET` de `.env.test` mide 23 bytes (<32); el fail-fast de `main.py` lo permite solo en
`APP_ENV=test` (gate por entorno) — no afecta producción, donde el mismo gate responde 503.

---

**AUDITORÍA COMPLETA — LISTA PARA TRIAGE**
