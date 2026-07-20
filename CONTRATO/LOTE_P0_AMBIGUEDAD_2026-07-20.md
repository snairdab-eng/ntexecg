# LOTE P0-AMBIGUEDAD — A-1/A-4 + B-1 · 2026-07-20

> Base: AUDITORIA_FINAL_2026-07-19 (§A-1, §A-4, §B-1). Los dos P0 que impiden
> la demo desatendida: la doctrina "perder un fill < duplicar tamaño" estaba
> impecable en el RearmJob pero no retro-aplicada al despacho principal (A-1/
> A-4) ni al propio envío del job (B-1). Protocolo §0: SIN commit hasta
> revisión.

---

## PARTE 1 — A-1 + A-4: E3 retro-aplicado al despacho principal

### A-1 · el intento ambiguo CORTA los reintentos de ENTRADA

`app/services/traderspost_client.py` — en el bucle de `send()`:

- Cualquier excepción ≠ `httpx.ConnectError` (timeout, protocolo roto,
  genéricas: la petición PUDO llegar y la orden PUDO quedar viva) marca el
  intento como ambiguo **y rompe el bucle si el rol es de entrada** — jamás se
  re-POSTea a ciegas sobre una orden que pudo existir. Log explícito
  `traderspost_entry_ambiguous_cut`.
- **EXITS conservan sus 10 intentos** aun con ambigüedad — declarado en el
  código y en el contrato del módulo: no cerrar es peor que cerrar dos veces
  (el flatten es idempotente; una entrada duplicada no lo es).
- Lo inequívoco sigue reintentando como siempre: respuestas HTTP no-2xx y
  `ConnectError` (el canal jamás se estableció → la orden seguro no existe).
- El `FAILED` final reporta `attempts` REALES (los ejecutados, no
  `max_attempts`): la forense distingue "cortó en el 1º" de "agotó los 3".

### A-4 · FAILED totalmente ambiguo ⇒ UNKNOWN, no FLAT

- `failed_ambiguo(result)` se movió del RearmJob a `traderspost_client.py`
  como **fuente única** de la clasificación (el job la re-importa con su
  nombre histórico `_failed_ambiguo` — cero cambio para sus tests).
- `app/api/webhooks_luxalgo.py::_dispatch_approved`: rastrea `entry_ambigua`
  (algún FAILED de entrada clasificado ambiguo). Con nada SENT:
  - exit → UNKNOWN (NX-08, sin cambio);
  - entrada ambigua → **`on_entry_ambiguous` → UNKNOWN** (nuevo);
  - entrada toda-inequívoca → FLAT (NX-08, sin cambio — pineado por test).
- `app/services/position_service.py::on_entry_ambiguous` (nuevo): solo desde
  `PENDING_*` (no pisa estados confirmados de otro flujo), conserva
  quantity/direction/entry_price como evidencia de lo que pudo salir, y
  audita `DELIVERY_FAILED{state: UNKNOWN, cause: entry_delivery_ambiguous}`.
  UNKNOWN bloquea nuevas entradas en L3 hasta revisión manual — FLAT habría
  liberado `symbol_busy` sobre una posible posición viva.

## PARTE 2 — B-1: intent-first en el RearmJob (diseño §2)

`app/services/rearm_job.py::procesar_posicion`, rama REENVIAR:

1. **Antes del HTTP** se escribe en la pierna el marcador
   `enviando = {cycle_n, client_id, sent_at}`, se persiste vía
   `set_rearm_state` y se **COMITEA** (excepción deliberada y documentada a
   la transacción única por posición: es exactamente la frontera que debe
   ser durable pre-envío; `expire_on_commit=False` en prod y tests).
2. Con desenlace conocido, el marcador se retira y la transición se persiste
   al final del barrido de la posición — **incluida la rama `fallido`**
   (antes no persistía nada; ahora debe persistir el marcador limpio o el
   siguiente barrido mataría un reintento legítimo como huérfano).
3. **Al releer**, una pierna `working` con `enviando` presente = intent sin
   desenlace (crash/error de DB entre el envío y su resolución) ⇒ la pierna
   **MUERE** (`intent_sin_desenlace`), jamás se re-envía, con
   `REARM_KILL{regla, intent}` — el intent queda dentro de la pierna muerta
   como evidencia forense. Fail-closed: la orden pudo salir y vivir 3600 s.

`leer_estado`/`_leg_valida` toleran la llave extra por construcción (solo
exigen las requeridas) — el marcador jamás vuelve ilegible el estado.

### Bug real encontrado por los tests del lote (y arreglado)

`set_rearm_state` guardaba la **referencia** del dict `estado` que el job
sigue mutando. Con UNA escritura por posición era invisible; con las DOS de
intent-first, el flush de la segunda comparaba el valor nuevo contra un
"commiteado" ya mutado por el alias, los veía iguales y **omitía el UPDATE**:
el intent quedaba huérfano siempre y el barrido siguiente mataba piernas
sanas. Fix: `set_rearm_state` persiste una FOTO (`copy.deepcopy`), documentado
en su docstring. (Es además el mismo patrón de riesgo que la auditoría rozó
en B-3.)

---

## Tests

`tests/test_p0_ambiguedad.py` (12 nuevos):

- **A-1 con excepciones httpx REALES** (cierra de paso la mitad de E-3):
  entrada `ReadTimeout` ⇒ 1 solo POST, FAILED ambiguo · exit `ReadTimeout` ⇒
  10 intentos · corta EN el intento ambiguo, no antes (`[500, ReadTimeout]` ⇒
  2) · `ConnectError` agota los 3 inequívoco · unidad de `failed_ambiguo`.
- **A-4**: `_dispatch_approved` con FAILED ambiguo ⇒ posición UNKNOWN +
  audit con causa · contraste FAILED inequívoco ⇒ FLAT (NX-08 conservado) ·
  `on_entry_ambiguous` no pisa estados no-PENDING.
- **B-1**: el intent está EN LA DB (sesión fresca, leído desde dentro del
  propio send) antes del HTTP · crash simulado post-envío (la 2ª escritura
  revienta) ⇒ el barrido siguiente NO re-envía, mata con audit y el 3º calla ·
  intent huérfano sembrado ⇒ cero HTTP, pierna muerta, evidencia intacta ·
  `fallido` inequívoco limpia el intent y el reintento legítimo sigue vivo.

`tests/test_traderspost_client.py`: el test que pineaba el reintento a ciegas
de entrada tras excepción (el bug de A-1) ahora pinea el corte; nuevo gemelo
`ConnectError` ⇒ sí reintenta y recupera.

`tests/test_exits_lote4.py::_patch_send_failed`: el fake devolvía
`error_message="http_500"` SIN `response_status_code` — una forma que el
cliente real jamás produce (un 500 real siempre trae el status). Sin status
ni flag, `failed_ambiguo` lo clasifica ambiguo fail-closed (por diseño) y
A-4 mandaba la entrada a UNKNOWN. Se realistizó el fake
(`response_status_code=500`), NO se aflojó el clasificador: la asimetría
manda que ante un resultado irrazonable se asuma que la orden pudo salir.

## Verificación

- Dirigidos (nuevo + traderspost + rearm job/adversariales + config lote6 +
  fix-d3 + exits lote4): **verdes** (81 + 22).
- Suite completa, corrida 1: `1 failed, 1454 passed, 7 skipped` en 858 s — la
  única falla fue el fake de exits lote4 de arriba (frontera A-4 nueva).
- Suite completa, corrida 2 (tras el fix del fake): **`1455 passed, 7
  skipped` en 872 s (0:14:32)** — verde en UNA sola corrida, sin split de RAM.
  Los 7 skips son los conocidos (paridad intrabar dormida por LX-12 +
  node/datos condicionales).

## Estado

Working tree SIN commit (protocolo §0): pendiente revisión profunda del
operador → commit. Con este lote quedan cerrados los DOS P0 de la auditoría
final; B-1 era el prerequisito de código para encender rearm en RTY (falta
aún B-2/B-4 del LOTE REARM-INTENT y los T2/T3 de mercado vivo).

## Qué NO entra en este lote (deuda declarada de la auditoría)

A-2 dedupe re-key · A-3 tick en `recompute_bracket` · A-5 techo de qty ·
A-7 rollback de deliveries del flujo principal (en B-1 el intent es la traza
que sobrevive; la delivery del ciclo perdido puede rollbackearse — mismo
residuo A-7) · B-2 `pop("rearm")` al cerrar · B-4 carrera vs exit concurrente
· E3b multi-destino en el flujo principal (con A-1 un mismo destino ya no
puede SENT-tras-ambiguo; el caso "destino A SENT + destino B ambiguo" con
posición confirmada queda como estaba).
