# LOTE ESCALERA-ADD — P0-2 · 2026-07-20

> Base: CONTRATO/TRADERSPOST_Semantica_Verificada_2026-07-20.md (sondas
> directas del operador con posición abierta, mercado vivo). Implementado
> CONTRA esa evidencia, sin re-investigar. Causa raíz cerrada: un buy/sell
> con posición abierta se IGNORA en silencio — por eso C2/C3 jamás llegaron
> al broker en toda la vida del sistema. Protocolo §0: SIN commit.

## 1. Cambios de producción

**`app/services/payload_builder.py` (los dos puntos del despacho):**

- **`build_scaled`** — piernas i>0: `action: "add"` y **sin** `sentiment`
  (sondas: add con sentiment ⇒ rechazo `invalid-sentiment-action`; add sin
  sentiment ⇒ orden de trabajo real que llena y promedia). **C1 conserva
  buy/sell + sentiment: es la que ABRE.** Todo lo demás intacto y pineado:
  `orderType/limitPrice` al tick (FIX-D2), `cancelAfter` (RA-2a), stop/TP
  comunes, extras con leg_index. El orden de llaves de C1 no cambió (cero
  movimiento de bytes en payloads no-add).
- **`build_rearm_leg`** — toda pierna re-armada es un add (R-RA5 garantiza
  posición abierta cuando el job envía): `action: "add"`, sin `sentiment`.
  `side` se conserva como parámetro (gobierna el reparto MR-5c de cortos).
- Contrato del módulo actualizado con la regla y su evidencia.

**`app/web/routes_strategies.py::_export_payloads`** — solo docstring: el
export R-T8 delega en el builder real, así que ahora muestra los add
legítimos; lo que distingue al andamio muerto son los offsets relativos
(`amount`), no el verbo.

## 2. CORTOS — declaración explícita

Las sondas fueron sobre un LONG. El código aplica la MISMA regla para short
(C1 `sell`+sentiment abre; adds sin lado): `add` no lleva dirección — el
broker suma a la posición abierta, sea cual sea su lado — y no encontramos
nada en el payload que deba diferir por lado (el precio límite ya viaja
absoluto y por ARRIBA del precio en cortos, pineado en tests).
**VERIFICACIÓN EN VIVO DE SHORT PENDIENTE del operador** antes de dar el
caso corto por cerrado.

## 3. Consumidores revisados (¿alguien asume action==signal.action?)

| Consumidor | Veredicto |
|---|---|
| `rearm.py::sembrar_estado` | NO lee `action` (selecciona por `orderType=="limit"`) — intacto |
| `webhooks_luxalgo._dispatch_approved` | NO lee `action` del payload (`is_exit` viene del norm; `primary_all_limit` por orderType) — intacto |
| `routes_dashboard` (últimos envíos) | muestra el action tal cual → "add" visible y honesto — intacto |
| `results_import` (reconciliación) | mapea buy→long/sell→short y salta el resto: los add quedan fuera como candidatos, PERO comparten signal_id con su C1 (que sí es candidato) — mismo matching neto que antes (by_signal dedupeaba). Declarado, sin cambio |
| `scripts/forense_exit_parcial.py` | **ajustado**: acumula entradas con `("buy","sell","add")` |
| `scripts/show_recent_deliveries.py` | **ajustado**: `is_entry` incluye "add" (etiqueta y resumen) |
| `scripts/check_leg_touch.py` | **ajustado**: `side=="add"` → toma el lado de `sig.action` (el toque de un add largo se juzgaba como corto) |
| `scripts/pullback_timing.py` | **ajustado**: ídem |
| `audit_signal_flow` / `audit_bracket_post_apply` | solo imprimen el action — intactos |
| Cliente/dedupe/normalizer | operan por signal_role/señal, no por payload — intactos |

## 4. Tests

**`tests/test_escalera_add.py` (4 nuevos):**
- **Regresión con los números del INCIDENTE** ([5,3,2]): C1 buy mercado
  qty 5 con sentiment; C2/C3 `add` límite 5492/5484 al tick, sin sentiment,
  cancelAfter 3600, bracket común, extras intactos.
- **Short, misma regla**: C1 sell+sentiment; add por ARRIBA (5006.0), sin
  sentiment, con bracket.
- **Re-armado ambos lados**: `build_rearm_leg` long y short ⇒ `add` sin
  sentiment, re-snap al tick, rearm_cycle/client_id intactos.
- **String EXACTO del wire** (tp_dumps byte a byte) con los números de las
  sondas B2/C: add límite 7500 sobre long, bracket 7419.50/7569.50 — el
  mismo payload que el broker demostró aceptar; y `"sentiment"` ausente.

**Ajustados** (pineaban el verbo viejo):
- `test_scaled_entry_engine.py:66` — C2 short: `sell` → `add` sin sentiment.
- `test_perfiles_l4.py::test_export_r_t8_builder_real` — el guard
  anti-andamio se re-ancla en lo que distingue al andamio (offsets
  `amount`); las piernas i>0 del export ahora DEBEN ser `add`.

## 5. Verificación

- Dirigidos (nuevo + scaled engine + perfiles + rearm job + exit-parcial +
  payload builder + escalera mr5c + ra2a + despacho e2e + fix-d3):
  **106 passed**.
- Suite completa: **`1463 passed, 7 skipped` en 785 s (0:13:05)** — verde en
  UNA corrida (esta vez sin el flaky de RAM de test_ra0_study; los 7 skips
  son los conocidos). Evidencia: pytest_escalera_add.txt.

## 6. Post-lote (operador)

1. Revisión → commit (working tree acumula P0-AMBIGUEDAD + P0-EXIT-PARCIAL
   + este lote).
2. **Verificar EN VIVO que C2/C3 aparecen como órdenes de trabajo** en
   TradersPost tras una entrada escalonada real.
3. **Verificar add sobre SHORT en vivo** (§2) — único hueco declarado.
4. Edge conocido (pre-existente, no introducido): una config patológica con
   C1 en 0 (`quantities: [0,x,y]`) emitiría solo adds sin pierna que abra;
   antes esos buy se ignoraban en silencio — mismo resultado neto (nada
   abre). Si se quiere guard fail-honest, es lote aparte.
