# AUDITORÍA FINAL NTEXECG — Cierre de la etapa Fable 5 · 2026-07-19

> Auditor adversarial de solo lectura (Fable 5). CERO cambios de código, cero
> commits. Método: seis barridos paralelos (camino del payload · cadena de
> re-armado · código muerto · doble-fuente · arsenal de pruebas · seguridad)
> con verificación cruzada de los P0 por el auditor principal, aritmética
> reproducida ejecutando las funciones reales del repo, y suite completa como
> verificación de no-regresión. Compañeros de lectura usados para NO
> re-reportar: AUDITORIA_TOTAL_Fable5_2026-07-12 · AUDITORIA_Despacho_E2E_
> 2026-07-15 · AUDITORIA_Total_Luxy_FixtureOro_2026-07-18 · LOTE_*.md del
> 16-19 · RA2b_RearmJob_Diseno_2026-07-17 · SPEC_Rearmado_Piernas_2026-07-15.

---

## §0 — VERIFICACIÓN DE NO-REGRESIÓN

**Suite completa VERDE en UNA sola corrida** (no hizo falta el split de RAM):

```
1442 passed, 7 skipped, 628 warnings in 865.60s (0:14:25)
```

Corrida sobre el working tree (incluye el diff sin commitear de
`routes_dashboard.py` y los dos tests sin commitear). OJO: un checkout limpio
de HEAD estaría ROJO — ver E-5 (test commiteado que depende del diff local).
Los 7 skips son los conocidos (paridad intrabar dormida por LX-12 + node/datos
condicionales).

---

## RESUMEN EJECUTIVO (honesto)

**¿Se puede confiar el sistema a una demo desatendida? Todavía no — pero está
a dos lotes quirúrgicos de poder.**

Lo estructural resiste el ataque: strategy_id siempre del path, ticker
intacto, SL obligatorio con guarda P0, kill-switch de 4 capas, herencia
solo-endurece de los gates, dedupe con strategy_id, escalonada con suma exacta
por destino, cuarentena con cota, E3/E3b ejemplar EN EL REARMJOB, y el guard
server-side de RA-3 sin vía de bypass encontrada. Los cinco hallazgos de la
auditoría de despacho del 07-15 están CERRADOS con evidencia. La aritmética
de los ejemplos manuales (ES y 6J) coincide con el código paso a paso… salvo
en un camino (A-3).

Lo que impide la demo desatendida son **dos P0 de la misma familia**: la
doctrina "perder un fill < duplicar tamaño" se implementó impecable en el
RearmJob **pero no se retro-aplicó al despacho principal ni al propio envío
del job**:

1. **A-1**: `TradersPostClient.send` reintenta una ENTRADA tras un timeout
   ambiguo (la orden pudo llegar) → dos órdenes a mercado. La clasificación
   E3 existe en el cliente pero solo el RearmJob la consume; el flujo
   principal la ignora.
2. **B-1**: el RearmJob envía la pierna ANTES de persistir el ciclo; un crash
   o error de DB post-envío re-envía la misma pierna al minuto siguiente con
   la anterior viva 60 minutos.

Ninguno de los dos es alcanzable HOY en producción-demo con la config actual
(re-armado APAGADO; A-1 exige un timeout parcial real), pero ambos son
exactamente la clase de error que la misión declaró inaceptable, y B-1 es
**prerequisito duro** antes de encender rearm en RTY.

El resto es deuda acotada: sin techo de cantidad (A-5), perfiles inertes en
entrada simple (A-6), tick-snap ausente en el bracket por perfil (A-3, la
única discrepancia numérica encontrada), dedupe ciego tras re-key (A-2),
estado rearm que no se limpia al cerrar la posición (B-2), y la frontera de
ambigüedad del cliente sin test real (E-3). Todo con fix quirúrgico, nada
arquitectónico.

---

## TABLA CONSOLIDADA DE HALLAZGOS (nuevos de esta auditoría)

| ID | Sev | Hallazgo (una línea) | Evidencia |
|----|-----|----------------------|-----------|
| **A-1** | **P0** | El despacho principal reintenta ENTRADAS tras intento ambiguo → posible doble orden a mercado | `traderspost_client.py:147-192`, `webhooks_luxalgo.py:431-460` |
| **B-1** | **P0** | RearmJob envía antes de persistir/comitear el ciclo → crash post-envío re-envía la pierna | `rearm_job.py:160-175, 352-395` |
| A-2 | P1 | Dedupe ciego tras re-key legítimo: el duplicado real posterior se despacha | `webhooks_luxalgo.py:113-137`, `deduplicator.py:26-35` |
| A-3 | P1 | `recompute_bracket` por perfil NO redondea al tick (regresión parcial FIX-D2, verificada numéricamente en 6J y ES) | `dispatch_profiles.py:69-96`, `payload_builder.py:134-137` |
| A-4 | P1 | Entrada FAILED-toda-ambigua ⇒ estado FLAT (fail-open: la orden pudo quedar viva) | `webhooks_luxalgo.py:518-530`, `position_service.py:148-172` |
| A-5 | P1 | `quantity` sin NINGUNA cota; `max_quantity` es config muerta | `signal_normalizer.py:125-129`, `config_resolver.py:51,151,320-321` |
| B-2 | P1 | `risk_plan_json["rearm"]` no se limpia al cerrar la posición → legs de un trade muerto renacen bajo el siguiente | `position_service.py:73-77,110-114` |
| B-4 | P1 | Carrera RearmJob vs exit/cancel concurrente → pierna viva sin posición madre hasta su cancelAfter | `rearm_job.py:205-248` vs exits con `cancel:true` |
| E-3 | P1 | La clasificación de ambigüedad del cliente real no tiene test — todos los tests de rearm ponen la bandera a mano | `traderspost_client.py:174-183`, `test_rearm_adversarial_ra2b6.py:100-119` |
| A-6 | P2 | Re-escalado por destino INERTE en entrada simple: el perfil "cuenta chica" recibe el tamaño íntegro | `payload_builder.py:196-197`, `dispatch_profiles.py:143-156` |
| A-7 | P2 | Error posterior al envío real ⇒ rollback del registro: órdenes reales sin traza | `webhooks_luxalgo.py:565-576` |
| B-3 | P2 | `set_rearm_state` read-modify-write sin lock: clobber last-writer-wins (puede retroceder `cycle_n`) | `position_service.py:90-94` |
| B-5 | P2 | Reintento de `envio_fallido` sin cota ni backoff (~1440 cadenas/día por pierna) | `rearm_job.py:340-350` |
| C-1 | P2 | Enlaces "Estudio de riesgo →" apuntan a `/ui/riesgo` que redirige DE VUELTA a la misma página (loop) | `strategy_detail.html:35-36,471`, `routes_riesgo.py:814-816` |
| C-2 | P2 | "Score mínimo" global editable pero INERTE tras FILTROS-OFF (score fijo 100) | `settings.html:47-50`, `quality_scorer.py:166` |
| C-3 | P2 | Filtro de noticias fantasma: editable y resuelto, pero NINGÚN nivel del pipeline lo lee | `settings.html:53-55`, `config_resolver.py:64-65,124-125` |
| D-1 | P2 | "Aplicada/difiere" con DOS estudios de referencia: dashboard contra v1 retirado, lista/ficha contra Luxy | `routes_dashboard.py:58-99` vs `routes_strategies.py:248` |
| E-1 | P2 | No existe E2E perfiles × escalonada — exactamente la combinación que se va a encender en vivo | `test_despacho_e2e_lx.py:83` (quantities [1,0,0]) |
| E-2 | P2 | El universo "gated" de FIX-FLAKE-2 es mayor que el patrón `_HAY_DATOS` (8 tests pesados quedarían fuera del corral) | `test_lab_consistency.py:22`, `test_ra0_study.py:209,224-232` |
| E-4 | P2 | `_ES_INTRABAR` se evalúa EN IMPORT: 22.5s + ~200 MB por colección, para 5 tests dormidos | `test_estrategias_l1.py:57` |
| E-5 | P2 | HEAD rojo: test commiteado (`test_ra3_ui.py:294`) depende del diff sin commitear de `routes_dashboard.py` | commit ab90e25 vs working tree |
| F-1 | P2 | Body del webhook parseado sin cota ANTES de autenticar (DoS de memoria en el único endpoint público) | `webhooks_luxalgo.py:596` |
| F-2 | P2 | `CF-Connecting-IP` confiado sin verificar origen Cloudflare → evade lockout de login | `login_guard.py:26` |
| A-8..A-11, B-6..B-10, C-4..C-5, D-2..D-8, E-6..E-8, F-3..F-5 | P3 | ~25 hallazgos menores — detalle en cada sección | secciones A-F |

**Dictamen de hallazgos previos** (lo ya conocido — sigue/empeoró/cerró):

| Hallazgo previo | Dictamen |
|---|---|
| D-1 cuarentena (07-15) | **CERRADO** (`security.py:14-16`, `quarantine_guard.py:20-47`) — residual F-1/F-2 |
| D-2 tick FX (07-15) | **CERRADO en camino base** — brecha nueva en perfiles (A-3) |
| D-3 huérfanas C2/C3 (07-15) | **CERRADO** (`payload_builder.py:112-123`, cancelAfter `:275-276`) |
| D-4/D-4bis Numeric (07-15) | **CERRADO** (Numeric(20,10); los 18,6 restantes no tocan despacho) |
| D-5 notación científica (07-15) | **CERRADO en el envío** (`tp_format.py:48-84`) — residuo forense A-8 |
| P1-2 jobs 1-worker sin lock (07-12) | **SIGUE** y el RearmJob amplía la superficie (`scheduler.py:205-214`, `rearm_job.py:5-8`) |
| P2-3 AuditService traga excepciones (07-12) | **SIGUE** tal cual, intencional (`audit_service.py:32-53`) |
| Replay TOTP (07-12) | **SIGUE** (`totp.py:32-45` — sin store de un-solo-uso) |
| Timing username (07-12) | **SIGUE** (`auth.py:91-92`), atenuado por lockout |
| CSP (07-12) | **MEJORÓ, no cerró**: CSP completa (`main.py:82-91`) con `unsafe-inline/eval` asumidos |
| Revocación en memoria (07-12) | **SIGUE por diseño** (`auth.py:25`) |

---

## A. EL CAMINO DEL PAYLOAD, DE PUNTA A PUNTA

### A.1 Lo que resiste (re-verificado, no inflar)

- **strategy_id SIEMPRE del path**: `webhooks_luxalgo.py:583-587` →
  `signal_normalizer.py:104`; el payload jamás lo aporta.
- **ticker_received intacto byte a byte**: `signal_normalizer.py:105`,
  `symbol_mapper.py:44-59` (lookup directo, sin strip/upper).
- **Jamás `sl_price=None` con `passed=True`**: guarda P0 en
  `sl_tp_calculator.py:165-189` + ValueError en `payload_builder.py:127-131`
  + `build_rearm_leg:335-336`.
- **Kill-switch de 4 capas por destino** (`webhooks_luxalgo.py:326-342,
  417-419`) y NX-01 leído en L1.1 (`filter_pipeline.py:338-341`).
- **Dedupe key incluye strategy_id** → no colisiona entre estrategias
  (`signal_normalizer.py:71-85`).
- **N4 post-FILTROS-OFF es passthrough coherente**: score fijo 100
  (`quality_scorer.py:194-196`), régimen opt-in
  (`filter_pipeline.py:203-221`); nada más lee score/régimen para decidir.
  Único residuo: A-11.
- **E3/E3b del RearmJob ejemplar**: FAILED con intento ambiguo MATA la
  pierna, jamás re-envía (`rearm_job.py:104-117, 183-191, 310-339`).

### A.2 Hallazgos

**A-1 · P0 — Reintento de entrada tras intento AMBIGUO.**
`TradersPostClient.send` clasifica correctamente (solo `httpx.ConnectError`
es inequívoco, `traderspost_client.py:177-183`) pero el bucle **continúa y
re-POSTea** tras cualquier excepción (`:191-192 → :147-149`). Un
`ReadTimeout` (10s, `:30`) donde la petición SÍ llegó + intento 2 exitoso =
**dos órdenes**; en escalonada la C1 es market SIN cancelAfter — posición
doble no acotada por TTL. `_dispatch_approved` (`webhooks_luxalgo.py:
431-460`) ignora `any_ambiguous_attempt` por completo. *Fix coherente con lo
ya construido: en entradas, no reintentar tras intento ambiguo (solo tras
ConnectError/respuesta HTTP no-2xx) + estado honesto del FAILED-ambiguo
(A-4). Exits pueden seguir reintentando (flatten idempotente).*

**A-2 · P1 — Dedupe ciego tras re-key.** Señal con clave K fuera de ventana
→ colisión UNIQUE → re-etiqueta `rk:<uuid>` y procesa
(`webhooks_luxalgo.py:113-137`). Un duplicado VERDADERO segundos después
consulta `is_duplicate(K)` (`deduplicator.py:26-35`) — la fila reciente
tiene clave `rk:` → no matchea → **segunda orden despachada**. La ventana
NX-10 se anula justo en el escenario que debía cubrir.

**A-3 · P1 — Bracket por perfil fuera de la rejilla del tick.** Cuando un
perfil overridea bracket, `recompute_bracket` (`dispatch_profiles.py:69-96`)
hace la aritmética SIN `round_to_tick` (compárese `sl_tp_calculator.py:
161-163`) y `payload_builder.py:134-137` emite el float crudo. Verificado
ejecutando el código: 6J con `sl_atr_multiplier=2.0` → SL=0.0063519 =
12.703,8 ticks (no múltiplo de 5e-7); ES → SL=5993.97 = 23.975,88 ticks.
TradersPost puede rechazar o redondear impredecible.

**A-4 · P1 — FAILED-todo-ambiguo ⇒ FLAT.** `on_entry_failed`
(`webhooks_luxalgo.py:518-530`, `position_service.py:148-172`) asume "la
entrada nunca llegó" — solo válido para ConnectError. FLAT libera
`symbol_busy` → la siguiente señal entra sobre una posible posición viva.
Coherente sería FAILED+ambiguo → UNKNOWN (L3 bloquea), como distingue el
RearmJob (`rearm_job.py:104-117`).

**A-5 · P1 — Cantidad sin techo.** `quantity` viaja del body al payload tal
cual (`signal_normalizer.py:125-129` → `payload_builder.py:96-106`).
`max_quantity` existe en resolver y perfiles (`config_resolver.py:51,151,
320-321`) pero **cero consumidores**. Una alerta mal configurada con
`"quantity":"50"` despacha 50 contratos.

**A-6 · P2 — Perfiles inertes en entrada simple.** Si `scale_entry.mode` no
es execute/live, `build_scaled` cae a `build()` (`payload_builder.py:
196-197`) y quantities/max_contracts del perfil no tienen efecto: cada
destino recibe el `signal.quantity` completo. En escalonada el reparto SÍ es
correcto (verificado: `cap_quantities([2,1],1)→[1,0]`, quita la pierna
lejana, suma == total; `dispatch_profiles.py:15-31,143-156`). La herencia
NX-02 de gates se cumple sin excepción (`dry_run` OR, `traderspost_enabled`
AND, `dispatch_profiles.py:182-191`) — lo que un perfil puede "aflojar" es
el TAMAÑO (sin techo global: mismo lote que A-5).

**A-7 · P2 — Rollback del registro tras envío real.** `process_signal` corre
en UNA transacción; excepción tras `client.send` → `db.rollback()`
(`webhooks_luxalgo.py:565-576`) borra WebhookDelivery de envíos que YA
salieron. El RearmJob lo resolvió (transacción por posición + audit en
sesión fresca, `rearm_job.py:359-396`); el flujo principal no.

**A-8 · P3** — Payload registrado no byte-idéntico al enviado (DB guarda vía
JSON estándar, el wire vía `tp_dumps`; `webhook_delivery.py:29`).
**A-9 · P3** — `recompute_sl_tp` sin caller (`dispatch_profiles.py:34-46`).
**A-10 · P3** — Matices cuarentena: IP sin X-Forwarded-For
(`webhooks_luxalgo.py:597`), estado en memoria, token en query string
(un access-log lo capturaría).
**A-11 · P3** — `score_minimum` acepta >100 (`config_resolver.py:228-231`);
con score fijo 100 un 101 bloquearía el 100% en silencio.

### A.3 Ejemplos numéricos manuales (aritmética verificada ejecutando las funciones del repo)

**ES — escalonada, base + perfil `cons {max_contracts:1}`** (camino sano):
entrada buy 6001.37, qty 2, ATR(5m)=3.7, backstop 90 pts, tp_nominal 11.5,
tick 0.25, `scale_entry={execute, quantities:[2,1], levels:[1.4]}`.
1. SL = 6001.37−90 = 5911.37 → 23.645,48 ticks → **5911.25**
   (`sl_tp_calculator.py:122-123,161-162`). TP = 6001.37+11.5×3.7 = 6043.92
   → 24.175,68 ticks → **6044.00** (`:145-147,163`). Guarda P0:
   5911.25 < 6001.37 < 6044.00 ✔.
2. Base `build_scaled`: C1 market qty 2; C2 limit qty 1 a
   6001.37−1.4×3.7 = 5996.19 → **5996.25** (`payload_builder.py:263-271`),
   `cancelAfter=3600` (`:275-276`); stop 5911.25 / TP 6044.00 común
   (`:221-225,277-279`). Suma base 3 = total ✔.
3. Perfil cons: `cap_quantities([2,1],1) = [1,0]` → solo C1 market qty 1
   (`dispatch_profiles.py:15-31`, `payload_builder.py:230-231`). Suma 1 ✔.
4. Cálculo manual == código en todos los pasos. ✔

**6J — simple, base + perfil con override** (aquí aparece A-3): entrada buy
0.0063545, tick 0.0000005, backstop 0.0000225 (45 ticks, ya snapped por
FIX-FX-BACKSTOP `scripts/fx_levers.py:30-52`), ATR 0.0000013.
1. Base: SL = 0.006332 = 12.664 ticks exactos ✔; TP = 0.00636945 →
   12.738,9 → **0.0063695** ✔. Bytes del wire: `"atr_value": 0.0000013`
   decimal fijo, sin `e-` (`tp_format.py:48-84`) ✔.
2. Perfil `sl_atr_multiplier=2.0` (override apaga backstop heredado,
   `dispatch_profiles.py:168-181`) → `recompute_bracket`:
   SL = 0.0063545−2×0.0000013 = **0.0063519 = 12.703,8 ticks** — el manual
   esperaba 0.0063520 (12.704 ticks). **El código envía el valor SIN
   rejilla** (`payload_builder.py:134-143`) → discrepancia = **A-3**.

---

## B. LA CADENA DEL RE-ARMADO (primera auditoría externa)

### B.1 Invariantes del diseño §0-§7 contra el código

| Invariante | Veredicto | Evidencia |
|---|---|---|
| §0 supuesto 1-worker | CUMPLE (riesgo declarado) | `scheduler.py:205-244`, wiring único `main.py:49-50` |
| §1 `rearm` solo nace de Aplicar; resolver superficia; gate LX-11 | CUMPLE con matices (B-6 clone, B-7 editores) | escritura única `mr_luxy.py:1451-1453`; guard `routes_strategies.py:1010-1062` |
| §2 estado persistente; restart = releer; ilegible ⇒ fail-closed | **NO CUMPLE en el punto duro** (B-1: persistencia POST-envío) | relee `rearm_job.py:213`; ilegible⇒SKIP `:236-238`; escritor acotado `position_service.py:81-95` |
| §3 ciclo sin solape 3600+120; jamás dos órdenes al mismo precio | CUMPLE en operación normal; ROTO bajo B-1/B-2/B-4 | `rearm.py:484-505` |
| E1 TTL≠3600 defendido en 3 capas | CUMPLE | `mr_luxy.py:1183-1185`, `rearm_job.py:232-234`, `rearm.py:146-147,543-546` |
| E2 assumed_filled solo bloquea la pierna | CUMPLE (la "aritmética de exposición" del diseño no existe — doc-drift) | `rearm.py:244-251`, `rearm_job.py:278-284` |
| §4 jerarquía R-RA9 estricta | CUMPLE (errata del diseño en R-RA4: código usa `≥`, correcto) | orden en `rearm.py:529-635` |
| §5 misma orden límite, qty recalculada, client_id, mismo gate | CUMPLE | `payload_builder.py:301-360`, `rearm_job.py:160,167-175` |
| §6 AuditLog con `object_id=account:symbol` | CUMPLE (llaves `ciclo/precio` vs `cycle_n/limit_price` del diseño — no dañino) | `rearm_job.py:64-68,295-298` |
| §7 matriz fail-closed | CUMPLE salvo la fila "restart a mitad de envío" (B-1) | `test_rearm_adversarial_ra2b6.py` (a)-(g) |

### B.2 Máquina de estados real y zombies

`working` es el único estado con acciones; `dead` y `assumed_filled` son
terminales reales (`rearm.py:548-550,258-260`). Zombies encontrados:
1. **"sent-uncommitted"** (fantasma no modelado): envío ejecutado +
   transacción perdida → re-envío al siguiente barrido (**B-1**).
2. **Legs sobreviven al cierre**: nada limpia `rearm` en FLAT
   (`position_service.py:110-114`) ni al reentrar (`:73-77`) (**B-2**).
3. **SKIP eterno ruidoso**: E1/ilegible/disabled → una fila de audit por
   minuto sin dedupe temporal (B-9).
4. **Reintento sin cota** de FAILED inequívoco (B-5).

### B.3 Hallazgos

**B-1 · P0 — Duplicado por crash/error entre envío y commit.**
`_reenviar_pierna` ejecuta el HTTP (`rearm_job.py:160-164`) en la MISMA
transacción que después persiste el ciclo (`:352-355`) y comitea
(`:377-381`). Cualquier excepción post-envío (flush de cadena sintética
`:165-166`, delivery `:167-175`, `set_rearm_state`, el commit) o un kill del
proceso → el handler (`:383-395`) audita `REARM_SKIP{error}` y CONTINÚA: la
DB conserva `last_sent_at` viejo → `toca_reenviar` sigue True → el barrido
siguiente (60s) re-envía **con la orden anterior viva 3600s**. La ambigüedad
de RED mata la pierna (E3); la ambigüedad LOCAL post-envío se trata como
"nada salió". *Mitigación coherente: intent persistido+comiteado ANTES del
HTTP; intent sin desenlace al releer ⇒ `envio_ambiguo` (matar).*

**B-2 · P1 — Resurrección de estado de un trade anterior.** Ventanas
reales: reversal dentro del gap ≤60s antes de que el barrido aplique R-RA5;
entrada degradada a C1-mercado que no siembra; rearm OFF→ON entre trades.
Legs `working` del trade ANTERIOR (precio/SL/TP congelados, hasta side
contrario) razonadas contra la posición nueva; `REENVIAR` de orden obsoleta
es alcanzable. *Fix: `pop("rearm")` en `on_entry_approved` y al pasar a
FLAT.*

**B-3 · P2 — Clobber last-writer-wins** sobre `risk_plan_json["rearm"]`
(`position_service.py:90-94`): el job mantiene la transacción abierta
durante el HTTP; una siembra concurrente queda pisada (incluye restaurar
`cycle_n` menor → ciclos extra contra R-RA4).

**B-4 · P1 — Carrera vs exit/cancel.** Entre la lectura de `pos.state`
(`rearm_job.py:205-248`) y el send hay awaits largos; un exit con
`cancel:true` en ese intervalo cancela lo EXISTENTE y el re-armado nace
DESPUÉS → pierna viva sin posición madre hasta su cancelAfter (60 min),
invisible al estimador. Sin cancelación compensatoria. (El caso secuencial
post-exit SÍ está cubierto — adversarial (b); el concurrente no.)

**B-5 · P2** — `envio_fallido` reintenta cada 60s sin cota: ~1440 cadenas
RawSignal→Delivery→Audit al día por pierna (`rearm_job.py:340-350`).
**B-6 · P3** — `clone_strategy` copia `scale_entry.rearm` con
`enabled:true` (`routes_strategies.py:2932-2938`): estrategia nueva con
rearm ON sin pasar SU gate (mitigado: nace dry_run+disabled).
**B-7 · P3** — Los editores de scale-entry (PATCH `routes_api.py:331-338`,
POST `routes_strategies.py:2691-2698`, `_merge_activacion`
`routes_riesgo.py:541-558`) reconstruyen `scale_entry` sin `rearm`: apagan y
borran constantes en silencio (dirección segura, pero contradice el LOTE
RA-3 §2).
**B-8 · P3** — RA-3 con checkbox pero sin escalera aplicable: preview dice
`incluido:true`, Config queda sin sembrar — la UI miente (nada se enciende;
`mr_luxy.py:1437-1454`).
**B-9 · P3** — Ruido de audit (skips por minuto). **B-10 · P3** —
`atribuir_toque` asume cadencia exacta 3720s (solo afecta contabilidad,
jamás duplica); hueco R-RA1 permanente si la posición excede la ventana del
bridge (fail-closed honesto); N+1 en la vista de ciclos.

### B.4 Coherencia sembrado ↔ job ↔ UI (incluido el diff sin commitear)

**ALINEADO llave por llave**: legs `leg_index/state/cycle_n` idénticos en
siembra (`rearm.py:125-136`), validación (`:93-96`) y vista
(`routes_dashboard.py:344-346`); `object_id` del job (`rearm_job.py:68`) ==
vista (`routes_dashboard.py:334`); actions REARM_* matchean el LIKE;
`REARM_DISABLED` del operador usa `object_id=strategy_id` y no contamina —
pero por eso mismo la vista no lo ve (D-7). El guard RA-3 es sólido: `rearm`
top-level del cliente DESCARTADO (`routes_strategies.py:1028`), veredicto
no-🟢 ⇒ 409 en preview Y aplicar (`:1053-1058,1090-1092`), gate ámbar
server-side (`:874-891`), Config solo expone `/rearm/off` (`:2610-2641`).
`max_ciclos` se enforcea sobre el `cycle_n` persistido (`rearm.py:621-624`);
ninguna reconstrucción productiva puede resetearlo (solo vía clobber B-3).

---

## C. CÓDIGO MUERTO E INCOHERENCIAS

### RETIRAR (muerto sin duda — 20 ítems, P3 salvo indicado)

- `filters_active_now` calculada y jamás leída (`routes_strategies.py:1787,1920`).
- Imports muertos del retiro L7b en `routes_riesgo.py:30,43,52` (`Request`,
  `flash_messages`, `render`, `MICRO_TO_LAB`) + 8 imports muertos menores
  (`global_profile.py:7`, `luxy_exploracion.py:1`, `repositories.py:6`,
  `market_data_service.py:22`, `symbol_mapper.py:14`,
  `traderspost_client.py:17`, `routes_positions.py:6`,
  `scripts/test_buy_all.py:18`).
- Dependencias sin import: `requests`, `python-dateutil`, `pytz`
  (`pyproject.toml:23-24,29`); `beautifulsoup4` es solo-tests → mover a dev
  (`pyproject.toml:28`).
- Llaves de Settings sin lector: `SECRET_KEY` (P2 por falsa sensación de que
  rota algo), `POSTGRES_USER/PASSWORD/DB`, `MARKET_BARS_UPDATE_MINUTES`
  (`config.py:23-27,56`).
- Endpoint + partial `recent-signals` sin consumidor
  (`routes_dashboard.py:540-561`).
- Basura de raíz: `pytest_out.txt`, `pytest_audit_out.txt`,
  `pytest_full.log`, `_luxy_smoke_*.png`, `_scratch_fixd/` (**154 MB**).

### CONSERVAR DECLARADO (10 grupos — correctos)

Retiro L7b de Riesgo (template + endpoints reusados como funciones, pineado
por `test_l7b_retiro.py`) · N4 passthrough FILTROS-OFF (declarado en
`scripts/inventario_l4.py` + `test_filtros_off.py`) · `MarketBarsUpdater`
jubilado con docstring (`scheduler.py:247-256`) · `scripts/archivo/` ·
compats `/ui/analytics`, LX-14b, pestaña Perfiles (`strategy_detail.html:11,
366-368`) · untracked de trabajo pendiente (2 tests + handoff + diff RA-3).
Mover a RESPALDOS: `es_server_tmp.json`, `_ntbridge_0714/` (67 MB),
`alias.bundle`, `ttfix.bundle`, `AnalisisClaudeTV/`.

### DOCUMENTAR (vivo pero engañoso — 3 P2 + 7 P3)

- **C-1 · P2** — Enlace circular "Estudio de riesgo →" / "ver estudio →"
  (`strategy_detail.html:35-36,471` → `/ui/riesgo` → 302 de vuelta,
  `routes_riesgo.py:814-816`).
- **C-2 · P2** — "Score mínimo" global editable e inerte
  (`settings.html:47-50`, `routes_settings.py:56,70`).
- **C-3 · P2** — Filtro de noticias fantasma: `news_filter_enabled`/
  `news_window_minutes` editables y resueltos, cero consumidores en el
  pipeline (`settings.html:53-55`, `config_resolver.py:64-65,124-125`).
- P3: comentario de pyproject que justifica matplotlib con la pestaña
  retirada (`pyproject.toml:32-34`); docstrings de `/filters`, `/regime`,
  `/probar-filtros` pre-FILTROS-OFF (`routes_strategies.py:2201-2209,
  2277-2281,1943-1947`); API JSON sin consumidor declarado
  (`routes_api.py` — solo `PATCH /status` tiene consumidor UI); doble puerta
  `luxy/aplicar` (sin UI) vs `aplicar_palancas` (UI) sin declarar
  (`routes_strategies.py:901,943` vs `strategy_detail.html:826`);
  `GET /ui/lab/data` huérfano no declarado (`routes_lab.py:307`); scripts
  `apply_quality_filter.py`/`apply_regime_gate.py` que activan lo que
  producción apagó (candidatos a archivo).

Barridos sin hallazgos: TODO/FIXME reales = 0; comentarios pre-FIX-D ya
actualizados; NQ retirada sin hardcodes rotos.

---

## D. DOBLE-FUENTE Y UNIVERSOS (barrido final)

**Categorías LIMPIAS (residuo cero, verificado):** DISPLAY-FX en superficies
vivas (los `toFixed` restantes son adimensionales; ficha/partial/diff usan
`fmt_pts`/`luxyFmtPts`; eje pts gated por `show_pts` con FX excluido) ·
semáforo con fuente única `robustez_semaforo` (`mr_luxy.py:561-588`) leída
por gate, lista, ficha y guard · despacho unificado (partial incluido
exactamente 2×, nadie re-renderiza destinos) · PF/net Python↔JS del detalle
(deliberado y rotulado "estimación" vs "validado · motor").

**Hallazgos:**
- **D-1 · P2** — "Aplicada/difiere" con DOS referencias: el dashboard
  (`routes_dashboard.py:58-99`) compara contra el estudio **v1 retirado**;
  la lista (`routes_strategies.py:248`) y la ficha Luxy contra el digest
  **Luxy**. Mismo comparador, dos verdades que pueden contradecirse en
  pantalla. *Fix: una sola referencia (Luxy) para todos.*
- **D-2 · P3** — Doble badge de deriva en la misma ficha (v1 pestaña Config
  `strategy_detail.html:467-471` + Luxy `:829-830`) con el link fantasma de
  C-1.
- **D-3 · P3** — Formateador FX duplicado con regla propia: `_fmt_unidad`
  heurístico (`routes_riesgo.py:233-251`) vs `fmt_pts` por catálogo
  (`fx_levers.py:68-79`) — hoy solo alimenta la muerta `riesgo.html`
  (latente).
- **D-4 · P3** — `$/micro` con `/10` hardcodeado en template
  (`_perfiles_panel_ro.html:14`) vs `MICROS_PER_MINI`
  (`position_sizing.py:10`).
- **D-5 · P3** — Tooltip del ⚪ siempre dice "muestra chica" aunque la causa
  sea `pocos_perdedores` (`strategies.html:65`; el digest no exporta
  `reason`, `mr_luxy.py:1662-1663`).
- **D-6 · P3** — Sin helper común de TZ: `strftime` crudo UTC sin rótulo en
  dashboard/positions/signals/audit vs "ET" declarado en Lab
  (`lab.html:13`) y "UTC" en `signal_detail.html:32`.
- **D-7 · P3** — La vista de ciclos rearm vive SOLO en el dashboard; la
  página canónica Posiciones no la muestra (la vista completa enseña menos
  que el resumen); el `REARM_DISABLED` del operador (object_id=strategy_id)
  no aparece como "última acción".
- **D-8 · P3** — "Recibidas" (vida entera, `StrategyPerformance`) vs
  conteo del rango del dashboard: fuentes Y ventanas distintas sin etiqueta.

---

## E. SALUD DEL ARSENAL DE PRUEBAS

**Cobertura por capa:** fuerte en webhook/pipeline/SL-TP/payload/registro/
rearm/UI-render/Luxy/seguridad/bridge; **media** en destinos-perfiles y
cliente TradersPost; **ausente** en respaldos (viven como ritual del
operador, cero tests). La fixture de oro NO es tautológica (valores
literales con derivación a lápiz, `test_luxy_golden.py:243,312-326,
353-370»); conftest limpio (teardown sin try/except tragón); los 2 tests sin
commitear son de calidad alta y sin estado compartido.

**Hallazgos:**
- **E-1 · P2** — No existe E2E perfiles×escalonada: `test_despacho_e2e_lx.
  py:83` usa `quantities:[1,0,0]`; la combinación multi-pierna+cap por
  destino solo está cubierta por unitarios que no se ensamblan. Es
  exactamente lo que T2/T3 va a encender en vivo.
- **E-3 · P1** — La clasificación de ambigüedad (`traderspost_client.py:
  174-183`, EL insumo de E3/E3b) no tiene test con excepciones httpx
  reales: todos los adversariales fabrican `WebhookDeliveryResult` con la
  bandera a mano (`test_rearm_adversarial_ra2b6.py:100-119`). Invertir el
  `isinstance` pasaría la suite en verde.
- **E-4 · P2** — `_ES_INTRABAR` evaluado en import: 22.5s + ~200 MB por
  colección (`test_estrategias_l1.py:57`) para 5 tests dormidos desde LX-12.
- **E-5 · P2** — HEAD rojo: `test_ra3_ui.py:294` (commit ab90e25) asserta
  contra el diff SIN commitear de `routes_dashboard.py`. El commit conjunto
  debe salir ya o el próximo bisect miente.
- **E-2 · P2** — El universo "gated" real son **29 tests en 9 archivos**, no
  los 7 archivos con `_HAY_DATOS`: `test_lab_consistency.py:22` (pytestmark
  inline, master+HOLC ES) y `test_ra0_study.py:209,224-232` (skip EN
  RUNTIME, invisible a deselección) también cargan datos reales.
- **E-6 · P3** — `test_robs2.py:358-361` se auto-skipea si `render_md`
  revienta. **E-7 · P3** — `except Exception: return False` en el gate de
  skip (`test_estrategias_l1.py:51`) disfraza errores reales de "sin
  datos". **E-8 · P3** — La ruta real de subprocesos asyncio jamás se
  prueba en Windows (shim global, `conftest.py:55-91`) — aceptado y
  documentado.

**Veredicto FIX-FLAKE-2 (opción a, dos invocaciones):** **suficiente CON
tres ajustes**: (1) marker explícito `datos_reales` registrado en pyproject
y aplicado a los 9 archivos (convirtiendo los skips runtime de ra0 en
marker) — la selección por grep de `_HAY_DATOS` dejaría 8 tests pesados en
la invocación ligera; (2) hacer lazy `_ES_INTRABAR` (fixture module-scope) o
la invocación ligera pagará igual los 22.5s+200 MB de colección; (3)
`--forked` no existe en Windows — correcto no contemplarlo. Nota: la corrida
de esta auditoría pasó completa en una invocación (865s), así que el split
es preventivo contra el techo de RAM, no correctivo.

---

## F. SEGURIDAD (delta desde 07-12)

**Previos:** ver tabla del dictamen arriba (replay TOTP, timing username,
revocación en memoria: SIGUEN; CSP mejoró a completa-con-excepciones; P1-3
parcialmente cerrado por FIX-D1 — el residual es F-1).

**Superficie nueva — dictamen afirmativo:** los endpoints rearm van todos
bajo `require_auth` (`main.py:138`); el guard 🟢 y el gate LX-11 se
recomputan server-side sin vía de bypass encontrada; validación de tipos
estricta (`rearm.py` `_int()` rechaza bool, ttl==3600 forzado); la frase
roja comparada con `!=` NO es vulnerabilidad (no es secreta, es fricción);
la cuota de cuarentena NO permite DoS de la señal legítima (tokens válidos
la saltan, `webhooks_luxalgo.py:663`); token webhook en tiempo constante
(`security.py:14-16`); XSS: autoescape Jinja intacto, sin `|safe`/`x-html`
sobre datos del atacante; AuditLog append-only de facto (cero
update/delete; matiz: sin trigger a nivel DB); sin secretos hardcodeados;
`nginx/` está vacío (nada que auditar).

**Nuevos:**
- **F-1 · P2** — `await request.json()` ANTES de token y cuota
  (`webhooks_luxalgo.py:596`), sin límite de tamaño de Starlette: DoS de
  memoria en el único endpoint público. *Fix: 413 si Content-Length > N KB.*
- **F-2 · P2** — `CF-Connecting-IP` confiado sin verificar que el peer sea
  Cloudflare (`login_guard.py:26`): llegando directo al origen se rota la
  clave por-IP y se evade el lockout. Inconsistente con el webhook, que usa
  `request.client.host` (`webhooks_luxalgo.py:597`). *Fix: confiar el
  header solo si el peer ∈ rangos CF.*
- **F-3 · P3** — Actor del AuditLog hardcodeado ("admin"/"operador") en vez
  del principal de sesión (`routes_strategies.py:981,1200,2035,2635`).
- **F-4 · P3** — Sin token CSRF; defensa única SameSite=Lax
  (`auth_routes.py:117`). Adecuado hoy, sin margen.
- **F-5 · P3** — `es_server_tmp.json` (y `_scratch_fixd/`,
  `_ntbridge_0714/`) fuera de `.gitignore`: riesgo de commit accidental de
  datos (sin secretos, verificado).

Y el aviso transversal: **cualquier `--workers>1` hoy duplicaría el
RearmJob** (P1-2 del 07-12, superficie ampliada) — documentarlo como
prohibición operativa hasta que exista lock.

---

## G. RECOMENDACIONES — los primeros 5 lotes de la siguiente etapa

1. **LOTE E3-DESPACHO (P0)** — retro-aplicar la doctrina del RearmJob al
   flujo principal: (a) A-1: entradas NO reintentan tras intento ambiguo
   (solo tras ConnectError/HTTP no-2xx; exits siguen igual); (b) A-4:
   FAILED-con-ambiguo ⇒ UNKNOWN, no FLAT; (c) E-3: tests de la
   clasificación con excepciones httpx REALES (ReadTimeout, ConnectError,
   ConnectTimeout, WriteError). Es un lote chico y es EL bloqueante de la
   demo desatendida.
2. **LOTE REARM-INTENT (P0/P1)** — prerequisito para encender rearm en RTY:
   (a) B-1: intent persistido+comiteado antes del HTTP; intent huérfano al
   releer ⇒ `envio_ambiguo`; (b) B-2: `pop("rearm")` en `on_entry_approved`
   y al pasar a FLAT; (c) B-4: re-chequeo de `pos.state` inmediatamente
   antes del send (+ considerar cancelación compensatoria); (d) B-3: releer
   estado antes de escribir (o versión en el JSON). B-5/B-9 caben aquí si
   sobra presupuesto.
3. **LOTE TECHO-Y-TICK (P1)** — (a) A-5: aplicar `max_quantity` como techo
   global en PayloadBuilder (build Y build_scaled Y build_rearm_leg); (b)
   A-3: `round_to_tick` en `recompute_bracket`; (c) A-6: decidir y declarar
   la semántica del perfil en entrada simple (¿cap o inerte?); (d) E-1: el
   test E2E perfiles×escalonada que ensambla todo esto.
4. **LOTE DEDUPE-Y-REGISTRO (P1/P2)** — (a) A-2: `is_duplicate` por
   contenido/created_at (o conservar la clave de búsqueda en columna
   no-única); (b) A-7: registro de deliveries en sesión aparte del flujo
   principal (patrón ya probado en RearmJob); (c) A-8 si sobra: guardar el
   string exacto de `tp_dumps`.
5. **LOTE HIGIENE-DE-CIERRE (P2/P3)** — (a) commit conjunto YA (diff RA-3 +
   2 tests + handoff + este reporte) para sacar a HEAD del rojo (E-5); (b)
   FIX-FLAKE-2 con marker `datos_reales` + `_ES_INTRABAR` lazy (E-2/E-4);
   (c) F-1 (cota de body) + F-2 (validar origen CF); (d) los tres P2 de UI
   engañosa (C-1 link circular, C-2 score inerte, C-3 noticias fantasma) +
   D-1 (una sola referencia de deriva); (e) barrido RETIRAR del inventario C
   + `.gitignore` (F-5).

### Tareas de OPERADOR pendientes (consolidadas)

1. **Commit conjunto** de la etapa (diff `routes_dashboard.py` + tests
   `test_rearm_inferencia_ra2b3.py`/`test_ui_despacho_unificado.py` +
   HANDOFF + esta auditoría) — urgente por E-5.
2. **Smokes pendientes**: RA-3 (7 pasos) y UI-DESPACHO (4 pasos) — listas en
   sus LOTE_*.md.
3. **T2/T3 en mercado vivo** (reapertura): escalonada ES con cancelAfter
   visible + exit que cancela piernas. Prerequisito de encender rearm.
4. **Encender rearm en RTY** SOLO tras T2/T3 **y tras el LOTE REARM-INTENT**
   (B-1/B-2 lo convierten en prerequisito de código, no solo de mercado).
   Después T10 (alcance del cancel con ticker compartido).
5. **Firewall del origin** a IPs de Cloudflare (pesa más con F-2).
6. **Rotar tokens de webhook** (viajaron por chats; además F-1: el token va
   en query string y un access-log lo captura).
7. **{{interval}}** en las alertas de LuxAlgo.
8. **Verificación periódica de respaldos** descargados fuera del server
   (además: la capa de respaldos no tiene ni un test — decisión consciente).
9. **Merge policy del HOLC en NinjaTrader** (roll) para reintegrar los 7
   masters (LX-12) — sin esto los 5 tests de paridad intrabar siguen
   dormidos.
10. **Prohibición operativa explícita**: jamás `uvicorn --workers>1` (dupli-
    caría schedulers y RearmJob) hasta que exista lock.

---

## VEREDICTO

**El sistema es apto para la demo SUPERVISADA en su configuración actual**
(re-armado apagado, cuentas paper, operador mirando): la aritmética está
certificada al centavo, los caminos base del despacho honran sus
invariantes, y las guardas de honestidad en pantalla funcionan.

**No es apto todavía para demo DESATENDIDA ni para encender el re-armado**:
A-1 y A-4 dejan al flujo principal violando la asimetría de la misión
exactamente en el caso de red que una demo desatendida terminará
encontrando, y B-1/B-2 hacen lo mismo con el RearmJob ante un crash o un
reversal rápido. Los cuatro comparten diagnóstico y ya tienen el patrón de
solución probado dentro del propio repo (E3/E3b + transacción-por-posición
del RearmJob; intent-first del diseño §2). Dos lotes quirúrgicos
(E3-DESPACHO y REARM-INTENT) cierran la brecha.

La mejor noticia de la auditoría: **las clases de error ya sufridas no
reaparecieron**. Los cinco hallazgos del 07-15 siguen cerrados, el barrido
de doble-fuente salió casi limpio tras los lotes de la semana (un P2
residual: la deriva con dos estudios de referencia), y el código muerto está
mayormente declarado y pineado por tests. Los P0 nuevos no son regresiones:
son la frontera a la que la doctrina todavía no había llegado.

— Auditor (Fable 5), 2026-07-19/20 · suite 1442 ✅ / 7 skip en una corrida
