# P0-EXIT-PARCIAL — Reporte + implementación · 2026-07-20

> URGENTE. Evidencia del operador (prueba en vivo 2026-07-20): entrada
> escalonada ES [5,3,2] → posición real de 5 micros (C1); el exit viajó con
> `"quantity": 1` (la de la ALERTA de LuxAlgo) y TradersPost cerró SOLO 1 —
> 4 micros vivos en el broker con NTEXECG marcando FLAT.
>
> **ESTADO: fix IMPLEMENTADO con OK del operador (mismo día). Suite completa
> verde (ver §6). SIN commit — protocolo §0, pendiente revisión.** Las
> secciones 1-2 y 4-5 son el reporte de investigación original.

---

## 1. CAUSA RAÍZ (confirmada, con evidencia)

**Un solo punto de origen:** `PayloadBuilder.build()`
(`app/services/payload_builder.py:95-105`) pone `"quantity":
signal.quantity` en TODO payload, **exits incluidos**. `build_scaled`
(`:196`) cae a `build()` para exits ("Salidas nunca escalan"), así que la
escalonada no lo salva. La ironía está escrita en el propio código: el
comentario MR-5c (`:93-94`) dice "las salidas cierran completo" — pero la
quantity que viaja es la de la señal, no la de la posición.

**Los CUATRO caminos de salida convergen ahí, todos afectados:**

| # | Camino | Quantity que viaja | Efecto real |
|---|--------|--------------------|-------------|
| 1 | **Exit de LuxAlgo** (`webhooks_luxalgo._dispatch_approved` → `build_scaled`→`build`) | la de la **alerta** (`signal.quantity`, p.ej. 1) | **EL INCIDENTE**: cierre parcial de 1 sobre 5 |
| 2 | **forced_exit EOD/max_holding** (`forced_exit.py:65` — `quantity=position.quantity or 0`) | el **estimado** = TOTAL DESPACHADO | en escalonada [5,3,2] pide cerrar **10** sobre una posición de 5 (solo llenó C1) — comportamiento de TradersPost no documentado; y si el estimado es 0 → exit con `quantity: 0`, tampoco documentado |
| 3 | **Reversal** (`webhooks_luxalgo.py:301` → `dispatch_forced_exit`) | ídem 2 | el cierre previo al reverso puede ser parcial/impredecible → la entrada opuesta montaría sobre residuo |
| 4 | **UI Flatten** (`routes_positions.py:80` → `dispatch_forced_exit`) | ídem 2 | el botón de pánico del operador tampoco garantiza aplanar |

**Dato estructural clave:** `PositionState.quantity` NUNCA es "la cantidad
realmente abierta". En `_dispatch_approved` se guarda `primary_qty` = suma
de TODAS las piernas despachadas (C1+C2+C3 = 10 en [5,3,2]); NTEXECG no
observa fills (estado `estimated` por diseño, REQ-0701). **Ninguna cantidad
propia puede cerrar exacto.**

Lo que SÍ funcionó: `cancel:true` (FIX-D3) viaja en los 4 caminos y no
depende de quantity — las C2/C3 sin llenar se cancelaron.

En el incidente: estimado NTEXECG 10, broker 5, exit qty 1 → broker quedó
con 4; NTEXECG pasó EXITING→FLAT por `on_delivery_confirmed` (el SENT del
exit parcial "confirma" el cierre completo a ojos del estimador).

## 2. SEMÁNTICA DE TRADERSPOST (fuentes + evidencia)

- **docs.traderspost.io/docs/core-concepts/webhooks** (verbatim): *"The
  full quantity of the open position in the broker will be exited if you do
  not send a quantity in the exit signal."* → **exit SIN quantity = aplana
  la posición COMPLETA, con la cantidad REAL del broker.**
- **Referencia de partial exit** (verbatim): *"TradersPost is only able to
  partially exit open positions by sending the explicit quantity to exit in
  the webhook JSON."* Ejemplo de la doc: posición 5, exit con quantity 2 →
  cierra 2, quedan 3. → **exit CON quantity = cierre PARCIAL explícito.**
- **webhook-spec.json** (github.com/TradersPost/docs): `quantity` es
  OPCIONAL en el esquema; la regla "si se omite se calcula de la
  suscripción o default 1" aplica a entradas — para `action=exit` manda la
  regla específica de los docs (flatten completo).
- **Evidencia de la cuenta:** el incidente mismo ES la confirmación
  empírica — exit con `quantity: 1` cerró exactamente 1 de 5, calcado al
  ejemplo de la documentación.

## 3. FIX (propuesto y luego IMPLEMENTADO con OK — ver §6)

**Omitir `quantity` en todo exit.** En `PayloadBuilder.build()`: si
`is_exit`, no incluir la llave `quantity` en el payload (y conservar la
cantidad estimada en `extras` como traza forense, sin efecto en la orden).
Un solo punto de cambio cubre los 4 caminos.

Por qué omitir y NO enviar `PositionState.quantity`:
- El broker es la ÚNICA fuente de verdad sobre fills; nuestro estimado es
  lo despachado, no lo llenado (10 ≠ 5 en el incidente). Enviar el estimado
  = pedir cerrar más de lo abierto → comportamiento no documentado.
- El flatten sin quantity es la semántica DOCUMENTADA de TradersPost para
  "cerrar todo", robusta ante fills parciales, piernas re-armadas y
  cualquier divergencia del estimador.
- Fail-honest: no existe el caso "cantidad desconocida" porque no se envía
  cantidad. Exit sobre cuenta ya plana = inofensivo (asunción ya declarada
  en NX-28).

Alcance: ~3 líneas en `build()` + tests. `forced_exit` conserva
`norm.quantity = position.quantity` en RawSignal/NormalizedSignal (registro
forense) — solo el payload deja de llevarla.

**Límite a declarar (no nuevo):** un exit sin quantity aplana TODO el
ticker en esa cuenta. Con la arquitectura actual (cuenta por
estrategia/perfil) es exactamente lo deseado; si algún día dos estrategias
comparten ticker EN LA MISMA CUENTA, el flatten cerraría ambas — es el
mismo alcance ya conocido del `cancel` (T10 del contrato). Documentarlo
junto al fix.

**Tests del lote (parte 5, tras el OK):**
- entrada escalonada 5+3+2 → exit de LuxAlgo con alerta `quantity: 1` ⇒ el
  payload del exit NO lleva `quantity` (aplana), `cancel: true` intacto;
- `forced_exit` (EOD/max_holding) ⇒ ídem;
- reversal ⇒ el cierre previo al reverso sin `quantity`;
- UI Flatten ⇒ ídem;
- actualizar `test_escalera_mr5c.py:137-140` (hoy pinnea la quantity de la
  alerta en el exit con el comentario "cerrar COMPLETO" — pinnea el bug) y
  cualquier assert de quantity en payloads de exit.

## 4. BARRIDO FORENSE (entregado, read-only)

`scripts/forense_exit_parcial.py` — solo SELECT sobre `WebhookDelivery`
(status SENT). Reconstruye episodios entrada→exit por (estrategia, destino,
ticker) y estima por episodio:

- `huerfano_min` = qty_C1_mercado − qty_exit (lo SEGURO llenado menos lo
  cerrado: si >0, huérfana casi cierta);
- `huerfano_max` = qty_total_despachada − qty_exit (si además llenaron las
  límites antes del cancel).

El real está entre ambos (NTEXECG no ve fills) — el operador coteja contra
el broker. Uso en el server (overlay+restore, código sin pushear):
`python -m scripts.forense_exit_parcial [--desde 2026-07-01] [--strategy X]
[--todo]`. Smoke local verde (DB dev sin SENT: mensaje limpio y sale).

## 5. MIENTRAS NO HAYA FIX (operativo)

Todo exit que salga HOY sigue siendo parcial/impredecible. Hasta el OK y el
deploy: cerrar posiciones desde el broker (o TradersPost manual), no confiar
el cierre a NTEXECG; el estimador marcará FLAT aunque queden micros vivos.

## 6. IMPLEMENTACIÓN (tras el OK del operador)

**Producción — un solo punto:** `app/services/payload_builder.py::build()`:
- `"quantity"` solo se incluye en el payload cuando NO es exit (comentario
  P0-EXIT-PARCIAL con la semántica documentada y el incidente);
- en exits, la cantidad que habría viajado queda en
  `extras.omitted_quantity` (traza forense, sin efecto en la orden);
- contrato del módulo actualizado: "Exits NEVER include stopLoss,
  takeProfit, sentiment NOR quantity".
Cubre los 4 caminos (exit LuxAlgo, forced_exit EOD/max_holding, reversal,
Flatten UI) porque todos convergen en `build()`. `cancel:true` intacto.

**Consumidores verificados** (nadie depende de `quantity` en exits):
`rearm.py` siembra solo desde piernas límite de ENTRADA; `results_import`
lee CSV del broker; `dest_qty`/`primary_qty` del despacho solo se consumen
para entradas; el log de leg muestra `None` (cosmético y honesto).

**Tests:**
- `tests/test_p0_exit_parcial.py` (4): el INCIDENTE completo — escalonada
  [5,3,2] despacha C1=5 mercado + total 10, exit con alerta qty 1 (y 7:
  parametrizado) ⇒ payload SIN quantity + cancel:true + omitted_quantity,
  posición FLAT, delivery registrada sin quantity · forced_exit con
  estimado 10 ⇒ sin quantity, estimado a extras · reversal ⇒ el cierre
  previo al reverso sin quantity.
- `tests/test_exits_lote4.py::_assert_close_only` (invariante compartido de
  TODOS los tests de dispatch de exits, Flatten de UI incluido): ahora exige
  `"quantity" not in payload`.
- `tests/test_payload_builder.py::test_exit_has_no_stop_loss`: ídem.
- `tests/test_escalera_mr5c.py:132-144`: el assert que pineaba el bug
  (`salida["quantity"] == 2  # cerrar COMPLETO`) ahora pinea el fix.

**Suite completa:** `1 failed, 1458 passed, 7 skipped` en 840 s
(pytest_p0_exit_parcial.txt) — la única falla fue
`test_ra0_study.py::test_es_real_seccion_coherente` con **MemoryError**: el
techo de RAM conocido de la auditoría (E-2/E-4, el split FIX-FLAKE-2 sigue
pendiente), SIN relación con este fix (ese test carga masters de datos
reales, no toca payloads). Re-corrido AISLADO: `20 passed` en
test_ra0_study.py completo. Total efectivo: los 1459 tests del repo pasan;
el flaky de RAM refuerza la recomendación E-2/E-4 del LOTE HIGIENE.

**Post-fix operativo:** los 4 micros huérfanos del incidente NO se corrigen
solos — cerrarlos en el broker; correr `scripts/forense_exit_parcial.py` en
el server para el resto del historial. Tras el deploy, el primer exit real
aplana lo que el broker tenga (incluidos residuos previos del mismo ticker).

— Investigación + fix Fable 5, 2026-07-20. Script forense + este reporte;
código de producción: solo `payload_builder.py`.
