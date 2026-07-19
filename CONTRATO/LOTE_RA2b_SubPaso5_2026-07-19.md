# RA-2b SUB-PASO 5 — RearmJob (REVISIÓN PROFUNDA: despacho real)

> 2026-07-19 · Plomería sobre el cerebro blindado (sub-pasos 2/3/4). Cero
> UI (RA-3 aparte). **Default OFF absoluto** (sin `rearm.enabled` el barrido
> es no-op). **NO COMMITEAR hasta revisión profunda del arquitecto** aunque
> la suite esté verde — mandato del lote.

## Piezas

| Pieza | Dónde |
|---|---|
| `RearmJob` (scheduler 60s, patrón ExitManagerJob, supuesto 1-worker P1-2 documentado) | `app/core/scheduler.py:205` + wiring en `app/main.py` lifespan |
| `rearm_sweep` (barrido; sesión/transacción POR posición) | `app/services/rearm_job.py:294` |
| `procesar_posicion` (leer → inferir → decidir → ejecutar) | `rearm_job.py:162` |
| `_reenviar_pierna` (destinos + gate + deliveries) | `rearm_job.py:104` |
| `PayloadBuilder.build_rearm_leg` (UNA pierna límite re-armada) | `payload_builder.py:301` |

## Diseño §5 punto por punto

- **Misma orden límite**: `limit_price`/lado idénticos, **re-snapped al tick
  del catálogo vigente** (FIX-D2, `round_to_tick`); NO se re-emiten C1/C2/C3
  (helper de pierna única, como pedía el diseño).
- **qty RECALCULADA del perfil vigente**: `quantities[leg_index-1]` de la
  config efectiva POR DESTINO, con `short_size_factor` (mismo `_apportion`
  de una entrada) y `max_micro_contracts` respetado. Perfil sin qty para la
  pierna (destino BASE) ⇒ la pierna **muere honesta**
  (`REARM_KILL{perfil_sin_qty}`) — dejarla working reintentaría un imposible
  cada 60 s.
- **Client id correlacionado**: `extras.client_id = "<entry_signal_id>-r{n}"`
  + `extras.rearm_cycle = n` (diseño §5).
- **Mismo gate**: `resolve_effective_dry_run(settings, dest_config)` POR
  destino + kill-switch por capas — un dry_run/pausa corta re-armados EXACTO
  como entradas. `cancelAfter = TTL vigente` en el re-envío.
- **WebhookDelivery por re-envío** (decision_id NOT NULL ⇒ cadena sintética
  RawSignal→NormalizedSignal→StrategyDecision `outcome="REARM"`, el MISMO
  patrón de forced_exit — la otra fuente autónoma de despacho).
- **stopLoss/takeProfit del estado CONGELADO** (sl/tp de la siembra — un
  re-armado no re-deriva bracket).

## Decisión pedida: semántica en DRY_RUN (justificada)

**El ciclo AVANZA con delivery DRY_RUN.** Coherencia del mundo paper: el
estimado de posición también avanza en dry-run, y el modelo sin-solape /
horizonte del estudio debe observarse EN PAPER exactamente como en vivo (la
demo valida el timing; si no avanzara, el job "re-enviaría" cada 60 s y el
ciclo perdería sentido). El FAILED total se rige por E3 (abajo).

## ENMIENDA E3 — FAILED ambiguo ⇒ MATAR, jamás reintentar

Un FAILED total solo permite REINTENTO si TODOS los intentos fallidos son
**INEQUÍVOCOS** (respuesta HTTP con status code, `ConnectError` — el canal
nunca se estableció, la petición jamás se escribió — o URL ausente): la
orden seguro NO existe → `REARM_SKIP{envio_fallido}` y reintento al
siguiente barrido. Si **CUALQUIER intento terminó AMBIGUO** (timeout /
excepción sin respuesta): la orden PUDO quedar viva en el broker → la
pierna se **MATA** (`REARM_KILL{envio_ambiguo}`, "perder un fill < duplicar
tamaño") y el siguiente barrido no re-envía nada.

Implementación: el "CUALQUIER intento" no es decidible con el ÚLTIMO intento
que conserva el resultado (un timeout en el intento 1 seguido de un 500 en
el 3 deja una posible orden viva que el último no delata) → campo **ADITIVO**
`WebhookDeliveryResult.any_ambiguous_attempt` (default False; entradas/exits
no lo consumen — cero cambio) acumulado POR INTENTO en el cliente
(`traderspost_client.py`: solo `httpx.ConnectError` es inequívoco; Read/
Write/Pool/ConnectTimeout y genéricos son ambiguos). El SENT también lo
porta (informativo: un SENT tras intento ambiguo puede coexistir con una
orden fantasma — observación para los adversariales del sub-paso 6). El job
clasifica en `_failed_ambiguo` (`rearm_job.py`: flag manda + defensa por
status/error para resultados sin el campo).

Tests E3: (a) FAILED 500 en todos ⇒ ciclo NO avanza y el siguiente barrido
reintenta · (b) FAILED con timeout ⇒ pierna muerta + `envio_ambiguo` + el
siguiente barrido no re-envía · el caso del último-intento-que-no-delata
(flag por-intento manda) · unidad del cliente (ReadTimeout/ConnectTimeout ⇒
ambiguo, ConnectError ⇒ inequívoco, httpx fakeado).

Observación heredada para sub-paso 6: el caso MIXTO (un destino ok + otro
FAILED-ambiguo) hoy avanza el ciclo — el re-envío futuro iría también al
destino ambiguo. E3 gobierna el FAILED TOTAL; el mixto queda anotado para
los adversariales.

## Fail-closed del job

- Excepción en una posición ⇒ **rollback de SU transacción** +
  `REARM_SKIP{motivo:error}` en sesión FRESCA + el barrido **continúa** (ni
  el audit del error puede tumbar el job). Test: A revienta, B re-envía.
- El job **solo escribe `risk_plan_json["rearm"]`** (`set_rearm_state` del
  sub-paso 2) — jamás state/direction/quantity. Test: foto de la posición
  idéntica tras re-envío y tras assumed_filled (E2).
- `rearm.enabled` **RE-VERIFICADO cada barrido** de la config efectiva:
  apagado a media vida ⇒ `REARM_SKIP{disabled}`, la pierna no se re-arma más.
- E1 defensa en profundidad: TTL efectivo ≠3600 ⇒ `REARM_SKIP{ttl_incoherente}`
  (además del flag sembrado que el motor ya corta).
- Candidatas del barrido = TODAS las posiciones (el ESTADO lo juzga el motor
  — R-RA5; el filtro barato es `plan["rearm"]` ausente ⇒ fuera sin costo).

## AuditLog

`REARM_LEG{leg_index, ciclo, precio, qty, client_id}` ·
`REARM_KILL{leg_index, regla, detalle}` · `REARM_SKIP{motivo[, detalle]}`
(dedupe por regla por posición/barrido — R-RA1 no spamea una línea por
pierna) · **`REARM_ASSUMED`** (desviación declarada del §6: la doble lectura
de R-RA2 merece verbo propio — la pierna NO está muerta, está asumida llena
y cuenta exposición; llamarla KILL mentiría en la reconstrucción de la
demo). ESPERAR no audita (el timing esperaría cada 60 s = ruido).

## AUTO-REVISIÓN R-RA1..9, una por una (evidencia archivo:línea)

| Regla | Implementación | Test |
|---|---|---|
| **R-RA5** | `rearm.py:551-558` — `_POS_MATAR` (los 5 estados del diseño, `rearm.py:487-491`) ⇒ MATAR; no-razonable ⇒ SKIP fail-closed. El job NO pre-filtra por estado (`rearm_job.py:175-178`) para que el motor juzgue | motor: `test_rra5_*`; E2E job: `test_posicion_no_abierta_el_motor_mata` (EXITING/UNKNOWN/FLAT) |
| **R-RA6** | `rearm.py:559-568` — `backstop_tocado`/`tp_tocado` (`rearm.py:424-444`, lados invertidos correctos) ⇒ MATAR huérfana, ANTES del reloj | motor: `test_rra6_*` (por lado); job E2E: `test_rra6_stop_tocado_mata_huerfana_e2e` |
| **R-RA1** | `rearm.py:570-578` — inferencia None (huecos/frescura del sub-paso 3) O ATR ilegible ⇒ SKIP, jamás matar a ciegas | motor: `test_rra1_*` + conflicto RA1>RA4; job: `test_feed_ciego_skip_rra1` |
| **R-RA2** | `rearm.py:579-594` — primer toque del tramo → `atribuir_toque` (`rearm.py:508`, mod ciclo 3720) → viva ⇒ ASSUMED_FILLED / ciega ⇒ MATAR; una sola lectura por toque | motor: viva/ciega + bordes exactos [3600,3720); job: `test_rra2_toque_con_orden_viva_assumed_y_posicion_intacta` (E2) |
| **R-RA7** | `rearm.py:596-601` — `atr_expandido` (> estricto) vs `signal_atr` congelado en la siembra (`rearm.py:118`) | motor: `test_rra7_*` + conflicto RA7>RA4 |
| **R-RA3** | `rearm.py:610-621` — último close del tramo vs entry_price por lado ≥ k×ATR señal ⇒ ESPERAR; datos ausentes ⇒ SKIP | motor: `test_rra3_*` (ambos lados, borde bajo umbral) |
| **R-RA4** | `rearm.py:622-626` — `cycle_n ≥ max_ciclos` ⇒ MATAR agotado (tras el timing: la orden viva del ciclo actual conserva su rastreo) | motor: `test_rra4_*` (incl. max_ciclos=1=OFF) |
| **R-RA8** | `rearm.py:627-634` — `< min_antes_cierre_min` de las 17:00 ET ⇒ ESPERAR (decisión justificada: R-RA4 ya garantizó horizonte; matar por reloj lo quemaría) | motor: `test_rra8_borde_de_las_17_et_con_zoneinfo` (16:31 sí / 16:30 no) |
| **R-RA9** | la JERARQUÍA misma: orden fijo en `decidir_pierna` (`rearm.py:529-641`), primera que dispara corta | 6 tests de conflicto (E1>todas, RA5>RA6, RA6>RA2, RA1>RA4, RA2>RA7, RA7>RA4) |
| **§3 sin solape** | `toca_reenviar` (`rearm.py:499-506`), ciclo 3720 s = 62 min ≡ RA-1 | `test_timing_antes_de_ttl_mas_guarda_espera` + `test_idempotencia_dos_barridos_sin_avanzar_reloj` (el candado anti-doble-orden) |

## Tests (`tests/test_rearm_job_ra2b5.py`, 16 — fakes, cero HTTP)

no-op sin posiciones · caso feliz (client_id `-r2`, cancelAfter 3600,
limitPrice 5492.1→**5492.0** re-snapped, qty del perfil, REARM_LEG, estado
avanzado) · **idempotencia** (2 barridos mismo reloj ⇒ 1 sola delivery) ·
EXITING/UNKNOWN/FLAT ⇒ R-RA5 mata · disabled a media vida ⇒ skip · E1 TTL
vigente ⇒ skip · estado ilegible ⇒ skip · perfil cambiado ⇒ qty 5 · perfil
sin qty ⇒ muere · feed ciego ⇒ R-RA1 · **excepción en A no bloquea B** (+
audit del error en sesión fresca) · kill-switch ⇒ todo DRY_RUN, nada SENT,
posición intacta, ciclo avanza (decisión DRY_RUN) · R-RA6 y R-RA2 E2E.

## Suite

181 verdes en la familia RA-2b + despacho E2E + forced_exit + payload_builder
+ position_service (local). Suite completa: ver cierre.

## Pendiente

- **REVISIÓN PROFUNDA del arquitecto ANTES de commit** (mandato del lote).
- Sub-paso 6: AuditLog E2E + adversariales (restart a mitad de ciclo,
  post-exit no revive, kill-switch corta, cotas, correlación de ids).
- Nota para la revisión: el job arranca SIEMPRE en lifespan (no-op sin
  estrategias con rearm ON) — si el arquitecto prefiere un flag de entorno
  para ni arrancarlo, es un cambio de 3 líneas en main.py.
