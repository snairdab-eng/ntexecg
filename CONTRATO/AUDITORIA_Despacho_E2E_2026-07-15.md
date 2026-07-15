# AUDITORÍA E2E DEL FLUJO DE DESPACHO — 2026-07-15

Recorrido `webhook → decisión (L1–L5) → reconstrucción del payload → envío a
TradersPost → cierre → registro`, cada eslabón con evidencia `archivo:línea`,
el catálogo de bloqueos, y el test que lo fija. Insumo para la fase de
observación en demo.

**Veredicto:** el flujo es sólido y fail-closed en lo esencial (SL obligatorio,
bracket validado, exits exentos y priorizados, kill-switch por capas, URL
enmascarada, doble registro). **Sin P0 nuevos.** Hallazgos abiertos: **1×P1
seguridad (escritura no autenticada), 1×P1 FX (redondeo al tick ausente),
2×P2 (huérfanas C2/C3 en el cierre; `Numeric(18,6)` trunca el tick de 6J),
1×P3 (notación científica en `extras`)**. Tests nuevos: `test_despacho_e2e_lx.py`.

---

## Tabla de hallazgos

| ID | Sev | Eslabón | Hallazgo | Evidencia | Acción |
|----|-----|---------|----------|-----------|--------|
| D-1 | **P1** | Recepción | `RawSignal` se escribe **y commitea** incondicionalmente ANTES del 401 → escritura DB no autenticada y sin cota | `webhooks_luxalgo.py:576-591` | **Lote** (invierte comportamiento probado; ver §1) |
| D-2 | **P1** | Payload FX | Sin **redondeo al tick** del instrumento en SL/TP absolutos; `round(limit,6)` mal para 6J (tick 5e-7) | `sl_tp_calculator.py:121-153`, `payload_builder.py:99-109,217` | **Lote** (hilar `tick_size` del symbol_map; ver §3) |
| D-3 | **P2** | Cierre | C2/C3 límite sin llenar quedan **huérfanas**: `cancel_after` nunca viaja en el payload; el cierre no las cancela explícito | `payload_builder.py` (sin `cancel_after`), `forced_exit.py:173-247` | **Lote** + doc de ciclo de vida (§5) |
| D-4 | **P2** | Registro | `Numeric(18,6)` trunca el 7º decimal del tick de 6J en decisión/posición/execution | `decision.py:43-45`, `position_state.py:40`, `execution_result.py:30-32` | **Lote** (subir a `Numeric(18,9)` FX) |
| D-5 | **P3** | Payload | Notación científica en `extras.atr_value` (`1.3e-06`) en el JSON | `payload_builder.py:122-125` | Cosmético; los campos de PRECIO de 6J (~6e-3) NO son científicos |

Positivos verificados (no regresionar): token en tiempo constante + hash+salt;
dedupe NX-10; bracket P0 fail-closed; exits exentos L3/L4/L5 y priorizados;
kill-switch por capas; propagación del cierre a todos los destinos; retry
entradas/exits; URL enmascarada; doble registro `WebhookDelivery` + `ExecutionResult`.

---

## 1) RECEPCIÓN — token → dedupe → RawSignal

**Camino:** `receive_luxalgo_webhook` (`webhooks_luxalgo.py:543`) valida el token
(hash+salt por estrategia → token legacy → secret global, SIEMPRE
`hmac.compare_digest`/`verify_token`, tiempo constante — `:563-573`), guarda
`RawSignal` (`:576-592`), audita+401 si inválido (`:594-608`), 200 con
`signal_id` y encola `_background_process_signal` (`:621-625`). El fondo:
`process_signal` → normaliza → **dedupe NX-10** (ventana del perfil,
`pipeline_config_json["dedup_seconds"]`, default 60s — `:79-108`) → re-key
fail-honest de colisiones legítimas fuera de ventana (`:120-134`) → pipeline.

### D-1 (P1) — escritura DB NO autenticada, sin cota
`token_valid` se computa primero (`:563-573`) **pero el `RawSignal` se
`db.add`+`commit` incondicionalmente** (`:590-591`) y solo DESPUÉS se evalúa
`if not token_valid → 401` (`:594`). Un atacante sin token válido persiste una
fila por request, sin límite → amplificación de escritura / DoS de disco.

**Blast radius del fix ingenuo** (no persistir en inválido): **1 test**
(`test_webhook.py:132 test_raw_signal_saved_on_invalid_token`) + docstring
(`:6` "Save RawSignal — ALWAYS"). PERO ese comportamiento es **intencional y
probado** (traza forense de intentos rechazados). Invertirlo en una auditoría
borra evidencia de ataque → **decisión del operador, va como lote**.

**Fix propuesto (lote, recomendado):** mantener la traza pero **acotada** —
`RawSignal` de token inválido va a cuarentena con **cota por IP/ventana**
(rate-cap: N filas por IP cada T; excedente → solo contador + `AuditLog`, sin
fila cruda). El `AuditLog WEBHOOK_BLOCKED` (`:595-603`) ya deja el evento de
seguridad, así que la cota no pierde el "quién/cuándo", solo tapa el flood.
Alternativa mínima: guardar `RawSignal` SOLO si `token_valid` (la cuarentena
se pierde, pero cero escritura no autenticada). *Test:* `test_webhook.py:132`
se invierte al comportamiento elegido.

**Test que lo fija (hoy):** `test_despacho_e2e_lx.py::test_recepcion_token_invalido_401_y_no_procesa`
(401 + no llega a payload).

## 2) DECISIÓN L1–L5 — catálogo de BLOCK e invariantes

`FilterPipeline.evaluate` (`filter_pipeline.py:98`), fail-fast, registra cada
nivel en `pipeline_execution_json`.

| Nivel | Chequeo | Motivo de BLOCK | Evidencia |
|-------|---------|-----------------|-----------|
| 1.1 | `global_mode` paused/flatten (entradas) | `global_{mode}` | `:338-341` |
| 1.2 | status estrategia | `strategy_quarantined/retired`; `strategy_paused`; candidate→QUEUE | `:344-356` |
| 1.4 | symbol map | `symbol_not_mapped` | `:359-361` |
| 1.7 | guardarraíl símbolo | `symbol_mismatch` | `:364-369` |
| 1.8 | guardarraíl timeframe | `interval_mismatch` (tf presente≠); **tf ausente → `tf_not_verified`, NO bloquea** | `:377-391,408-409` |
| 1.6 | heartbeat bridge (entradas) | `market_data_not_active`; **exits exentos** | `:396-405` |
| 2.0 | frescura | `signal_stale` | `:428-448` |
| 2.2 | sesión/día | `outside_session_hours` (exit hereda `allow_exits_outside_window`) | `:150-169,415-426` |
| 3.3 | estado posición | `unknown_position_state`, `position_locked` | `:468-473` |
| 3.4 | symbol_busy NX-09 | `symbol_busy` (opt-out `allow_stacking`; reversals exentos) | `:481-492` |
| 3.5 | **PortfolioGuard** | riesgo agregado por activo (fail-closed) | `:501-505` |
| 4 | quality/régimen | `regime_not_allowed`, `score_below_minimum` | `:203-251` |
| 5 | SL/TP | `atr_calculation_failed`, `entry_price_missing`, `bracket_price_invalid` | `sl_tp_calculator.py:87-180` |

**Invariantes reverificados en ESTE camino:**
- **Sin SL válido → BLOCK.** `calculate` nunca devuelve `passed=True` con
  `sl_price=None`; guarda P0 final (precios >0 y lado correcto) →
  `bracket_price_invalid` (`sl_tp_calculator.py:162-180`). Entrada sin SL en el
  builder → `ValueError` (`payload_builder.py:92-96`). ✔
- **`tf_not_verified` anota y NO bloquea** (`filter_pipeline.py:380-386,408`). ✔
- **Exits exentos L3/L4/L5** (`:174-175,196-197,257-258`). ✔
- **PortfolioGuard 3.5** corre tras symbol_busy, solo otros símbolos del activo,
  fail-closed (`:494-505`). ✔
- **Despacho SOLO en APPROVE** (`webhooks_luxalgo.py:226`): un BLOCK a cualquier
  nivel NO llega a payload. ✔

### Kill-switch por capas — qué capa corta dónde
`resolve_effective_dry_run` (`webhooks_luxalgo.py:323-339`): envío real ⇔
**las 4 abiertas**.

| Capa | Fuente | Corta | Nota |
|------|--------|-------|------|
| 1 env `TRADERSPOST_ENABLED` | settings | maestro del server | False → todo dry |
| 2 env `DRY_RUN` | settings/.env | badge UI (NX-03) | True → todo dry |
| 3 `traderspost_enabled` | ConfigResolver (global **AND** estrategia) | por estrategia | perfil solo endurece (AND) — `dispatch_profiles.py:187-190` |
| 4 `dry_run` | merge (cualquier nivel ON) | por estrategia/perfil | perfil hereda con OR — `dispatch_profiles.py:186` |

Un perfil de riesgo **solo restringe**, nunca abre por encima de la base (NX-02).

## 3) RECONSTRUCCIÓN DEL PAYLOAD — precios ABSOLUTOS

`PayloadBuilder.build` / `build_scaled` (`payload_builder.py:55,137`). **Precios
absolutos siempre:** `stopLoss.stopPrice = float(sl_price)` (`:99-102`),
`takeProfit.limitPrice = float(tp_price)` (`:105-109`), `signalPrice` guarda P0
(`:82`). SL = `P0 ∓ backstop_points` nativos o `P0 ∓ k×ATR`
(`sl_tp_calculator.py:118-128`); TP = `P0 ± n×ATR` del bridge (`:141-154`).
`ticker = mapped_symbol` (nunca `ticker_received`, `:80`). Escalonado: C1 mercado
+ C2..Cn límite en `P0 ∓ level×ATR`, stop común (`:194-236`).

### D-2 (P1 FX) — falta el redondeo AL TICK
Ni `sl_tp_calculator` ni `payload_builder` redondean al `tick_size` del
instrumento. Reproducción real (6J, tick 5e-7, backstop 45 ticks, ATR 2.6 ticks):

```
signalPrice 0.0063545 · stopPrice 0.006332 · limitPrice 0.006374
```
Cae on-tick **por suerte** (offsets múltiplos limpios de tick). Con un backstop
de 46 ticks o un ATR no-múltiplo, `P0 ∓ offset` produce basura flotante
(`0.006374150000000001`) **fuera de la rejilla** → TradersPost puede rechazar
(`invalid price`) o redondear impredecible. Peor: `build_scaled` usa
`round(limit_price, 6)` (`payload_builder.py:217`) — rejilla 1e-6, **el doble**
del tick de 6J (5e-7): **no puede representar ticks impares** (0.0063545 →
0.006354, −1 tick). 6E (tick 5e-5) sufre lo análogo en menor grado.

**Fix (lote):** el `ConfigResolver` ya conoce el `SymbolMap.tick_size`; hilarlo
al `config` y redondear SL/TP/limit al múltiplo de tick más cercano en
`sl_tp_calculator`/`payload_builder` (una función `round_to_tick`). El
`round(...,6)` se reemplaza por `round_to_tick(limit, tick)`.

### D-5 (P3) — notación científica
`extras.atr_value` serializa `1.3e-06` (`payload_builder.py:122-125`). Es
**metadata** (TradersPost ignora `extras`), y los campos de PRECIO de 6J (~6e-3)
NUNCA son científicos (repr `"0.006332"`). Riesgo bajo; si algún día un precio
cae bajo ~1e-4 sí saldría científico → el redondeo-a-tick de D-2 debe además
formatear sin `e` (p. ej. `Decimal`/`format`).

**Test que lo fija:** `test_despacho_e2e_lx.py::test_6j_payload_formato_decimal`
(precios absolutos, string exacto, sin `e-0` en los campos de orden).

## 4) ENVÍO — destinos por perfil, retry, fallo, enmascarado

`_dispatch_approved` (`webhooks_luxalgo.py:342`): `resolve_destinations`
(`dispatch_profiles.py:99`) = base + hasta 4 perfiles; dedupe por webhook_url
(`:193-204`). `recompute_bracket` **fail-closed** (`:56-96`): si el bracket del
perfil no es computable/lado inválido → el destino usa el bracket BASE del L5
(`webhooks_luxalgo.py:396-409`) — nunca stop inválido. Un `WebhookDelivery` por
leg por destino (`:438-451`).

**Envío** `TradersPostClient.send` (`traderspost_client.py:54`): timeout 10s;
**entradas 3 intentos** (config NX-15), **exits 10** (crítico, no bajable),
entrada rancia → 1 intento (`:102-119`); backoff `1·2^(n−1)` cap 4× (`:179-185`).
**Nunca lanza** — siempre `WebhookDeliveryResult` (`:65`). Error/timeout →
`status=FAILED` con `response_status_code/body/error_message` guardados
(`:168-177`); **no hay cola de reintento diferido** — los reintentos son
síncronos dentro del `send`. Tras FAILED en TODOS los destinos:
`on_entry_failed` → FLAT (entrada nunca llegó) / `on_exit_failed` → UNKNOWN (L3
bloquea entradas hasta revisión) — NX-08 (`webhooks_luxalgo.py:490-506`). URL
**siempre enmascarada** `token=***` en logs y DB (`traderspost_client.py:43-47`,
`WebhookDelivery.url_masked`).

## 5) CIERRE — exit → exención → propagación → C2/C3

Exit de LuxAlgo: `action=exit` → `signal_role` exit_* → pipeline exento
L3/L4/L5 (§2) → APPROVE → `_dispatch_approved` con `is_exit`. El payload de
salida **omite `stopLoss`/`takeProfit` y `sentiment`** (`payload_builder.py:87-109`,
TradersPost rechaza sentiment en exit). **Se cierra en TODOS los destinos**
(base + perfiles, NX-07 — `forced_exit.py:35,86-108`; el mismo `_dispatch_approved`
itera `destinations`). `PositionService.on_exit_approved` → EXITING → FLAT al
confirmar (`webhooks_luxalgo.py:474-493`).

### D-3 (P2) — ciclo de vida C2/C3 (observación del operador)
**NTEXECG NO envía `cancel_after`** en el payload (`grep` en `payload_builder`/
`traderspost_client` = 0). El `entry_reserve_timeout_seconds` vive SOLO del lado
NTEXECG. Ciclo real de una entrada escalonada:

1. Despacho: C1 market + C2/C3 **limit** (un `WebhookDelivery` c/u). El operador
   DEBE fijar a mano el "Cancel entry after" de TradersPost (≤60 min) — NTEXECG
   no lo transmite.
2. Vida de las C2/C3 en TradersPost: viven hasta que **su** cancel_after dispare
   (config manual) o se llenen por pullback.
3. **Al cerrar** (exit LuxAlgo/forzado): NTEXECG envía `action=exit` → cierra la
   posición ABIERTA. **No emite cancelación explícita de las órdenes límite
   pendientes.** Si una C2/C3 sigue viva y el precio hace pullback DESPUÉS del
   cierre y ANTES del cancel_after de TradersPost → **fill huérfano** (entrada
   nueva sin exit pendiente) hasta el próximo exit.
4. Lado NTEXECG: `release_unfilled_reservations` (`forced_exit.py:173-247`) SOLO
   arregla el **estimado** `symbol_busy` fantasma (PENDING_*/LONG-SHORT
   `entry_style=="limit_only"` más viejos que `entry_reserve_timeout_seconds`
   → FLAT + `RESERVE_RELEASED`). **No toca las órdenes reales del broker.**

**Escenarios (qué queda vivo/huérfano):**

| Escenario | Posición NTEXECG | Órdenes C2/C3 en TradersPost |
|-----------|------------------|------------------------------|
| Exit con C2/C3 sin llenar, dentro de cancel_after | EXITING→FLAT (exit enviado) | **VIVAS** → riesgo de fill huérfano hasta cancel_after |
| Exit tras cancel_after ya disparado | FLAT | canceladas por TradersPost (ok) |
| Sin exit, todo límite, timeout | `release_unfilled_reservations`→FLAT | TradersPost las canceló por cancel_after (si configurado) |
| C2/C3 llena tras cierre | fantasma hasta próximo barrido/exit | posición real abierta sin gestión NTEXECG |

**Fix (lote):** (a) transmitir el cancel_after en el payload de la leg límite si
la versión de TradersPost lo soporta, o (b) que el exit dispare una cancelación
explícita de las límite pendientes de ese símbolo antes/junto al cierre. **Es un
riesgo real de órfanas**, hoy mitigado solo por la config manual del operador.

`ExitManagerJob` (`scheduler.py:174-186`, 60s): `exit_manager_sweep`
(max_holding/EOD/overnight, `exit_manager.py:41-68`) + `find_stale_positions`
(warning PENDING_*/EXITING >15min) + `release_unfilled_reservations`.

## 6) REGISTRO — ¿reconstruible?

**Sí, doble pista:**
- **`WebhookDelivery`** (`webhook_delivery.py`) por leg×destino: `decision_id`,
  `destination` (tag `traderspost:<perfil>`), `url_masked`, `payload_json`
  (dict enviado), `response_status_code/body`, `status`, `attempts`,
  `latency_ms`, `error_message`, `sent_at`. → qué se envió, a qué perfil, con
  qué bracket, y qué respondió TradersPost.
- **`ExecutionResult`** (`execution_result.py`): fills IMPORTADOS
  (`row_hash`, `entry/exit_price`, `pnl`, `exit_reason`, `matched_decision_id`,
  `match_method`) → reconciliación fill↔decisión.
- **`StrategyDecision.pipeline_execution_json`**: la traza L1–L5 completa.
- **`AuditLog`**: `WEBHOOK_BLOCKED`, `FORCED_EXIT`, `RESERVE_RELEASED`, etc.

**D-4 (P2):** `WebhookDelivery.payload_json` (JSON) guarda el float fiel, pero
`StrategyDecision.sl_price/tp_price/atr_value`, `PositionState.entry_price` y
`ExecutionResult.entry/exit_price` son **`Numeric(18,6)`** → el 7º decimal del
tick de 6J (5e-7) se **trunca** en el registro/analítica (no en el envío). Subir
a `Numeric(18,9)` para FX (lote de migración).

---

## 7) Tests nuevos (`tests/test_despacho_e2e_lx.py`)

| Test | Fija |
|------|------|
| `test_es_entrada_e2e_payload_absoluto_y_destinos` | ES buy: webhook→APPROVE→WebhookDelivery con `stopPrice`/`limitPrice` ABSOLUTOS; base + perfil |
| `test_6j_payload_formato_decimal` | 6J: precios absolutos, string exacto del payload, sin notación científica en campos de orden |
| `test_exit_limpia_bracket_y_todos_los_destinos` | exit: sin SL/TP/sentiment, a TODOS los destinos, posición cerrada |
| `test_bloqueo_por_nivel_no_llega_a_payload[L1..L5]` | catálogo de BLOCK por nivel → 0 `WebhookDelivery` |
| `test_recepcion_token_invalido_401_y_no_procesa` | token malo → 401 + no procesa (D-1 pin) |
| `test_killswitch_por_capa` | tabla de `resolve_effective_dry_run` — real solo con las 4 abiertas |

Todos DRY_RUN (sin HTTP). Los hallazgos D-1..D-5 quedan como **lotes**
propuestos; los tests fijan el comportamiento E2E vigente (guardas de regresión)
y documentan el string real del payload FX.
