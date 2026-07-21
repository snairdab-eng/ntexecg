# LOTE SIZING-GATEWAY — la cantidad la decide NTEXECG, jamás la alerta · 2026-07-21

> Doctrina del operador: «NTEXECG no es un passthrough, es un GATEWAY que
> SIEMPRE reconstruye el payload — la cantidad la decide NTEXECG, jamás la
> alerta». P1 de misión: cambia el tamaño de TODAS las entradas. Absorbe A-5
> (cota de quantity) y A-6 (el perfil re-escala también en simple) de la
> auditoría final. Protocolo §0: SIN commit hasta revisión.

## 1. LA CANTIDAD NUNCA VIENE DE LA ALERTA (payload_builder)

El bug: `build()` usaba `signal.quantity` (de LuxAlgo) en toda entrada — un
número arbitrario del backtest, no una decisión de riesgo (evidencia en vivo:
GC despachó 2 y RTY 5 solo porque así están sus alertas). Nueva regla, toda
en `PayloadBuilder`:

| Modo `scale_entry.mode` | Qué despacha | Camino |
|---|---|---|
| `execute` / `live` | reparto del ESTUDIO (quantities por pierna) | `build_scaled` (sin cambio) |
| cualquier otro (`design_only` / `off` / ausente / fallback de execute roto) | **MODO TESTIGO: 1 micro, una sola orden a mercado** | `build()` |

- Constante nombrada `MODO_TESTIGO_QTY = 1` (payload_builder.py).
- **Hallazgo de arquitectura**: `build()` para entradas SOLO se alcanza fuera
  de execute/live (el despacho principal siempre llama `build_scaled`, que en
  execute produce las piernas y en cualquier otro modo cae a `build()`). Por
  eso el testigo vive en `build()` y cubre TODOS esos caminos con un cambio
  mínimo — incluido el fallback de un execute con config rota (antes mandaba
  la alerta; ahora el mínimo honesto de 1).
- La `quantity` de la alerta se conserva SOLO como traza forense en
  `extras.signal_quantity` (patrón `omitted_quantity`), JAMÁS en el campo de
  la orden.
- **Exits: sin cambio** (P0-EXIT-PARCIAL — sin `quantity`, aplanan completo;
  `extras.omitted_quantity` intacto).

## 2. A-6 — EL PERFIL RE-ESCALA TAMBIÉN EN ENTRADA SIMPLE

El bug (confirmado en vivo): en `design_only` el perfil recibía el tamaño
ÍNTEGRO de la base (GC mandó 2 a base Y 2 al perfil "conservador", que debería
recibir menos). **Se resuelve solo con el §1**: en modo testigo cada destino
(base y cada perfil) cae independientemente a `build()` → 1 micro. Sin tocar
`dispatch_profiles`.

**DECISIÓN Y JUSTIFICACIÓN de la semántica (el operador pidió decidir):**
elegido **«el perfil manda 1 también»**, NO «si su reparto < 1 no despacha».

- **NX-02 intacto por construcción**: el perfil (1) jamás supera a la base (1)
  — 1 ≤ 1. El invariante "el perfil solo ENDURECE, jamás recibe más que la
  base" se cumple trivialmente (test adversarial `test_nx02_...`).
- **Sin supresión silenciosa**: un perfil que el operador habilitó SIGUE
  despachando. Suprimirlo en testigo sería un cambio de modo en silencio —
  justo el anti-patrón que llevamos semanas desterrando.
- **Uniforme y verificable**: "testigo = 1 micro a mercado a cada destino
  habilitado". Fácil de razonar y de cotejar en el broker.
- El "< 1 ⇒ no despacha" es la regla correcta del ESCALONADO (execute), donde
  una pierna con qty 0 ya se salta (`if q <= 0: continue`) — no del testigo,
  cuyo piso es un 1 fijo.

En `execute` el perfil SÍ re-escala su reparto (cap `max_contracts`), como
siempre — test `test_a6_execute_el_perfil_reescala_su_reparto` (base [5,3,2],
conservador [3,2,1]).

## 3. INTERACCIONES (resueltas y declaradas)

- **`short_size_factor` (MR-5c) sobre 1 micro**: se aplica pero el piso de 1
  micro lo deja en 1 — `max(1, round(1·f)) = 1`. Un corto TESTIGO NO baja de 1
  (reducidos, no eliminados). El factor sigue viajando en `extras`
  (transparencia; efecto nulo sobre el piso). En execute el factor SÍ reparte.
- **`max_micro_contracts`**: en testigo total = 1 ≤ cualquier cap (validado
  ≥1). Sin efecto. En execute acota como siempre.
- **Re-armado (`build_rearm_leg`)**: NO se ve afectado — es un método aparte y
  re-envía la qty de SU pierna del perfil vigente. Además queda **inerte en
  testigo por diseño**: `sembrar_estado` solo siembra piernas LÍMITE, y el
  testigo es una sola orden a MERCADO → no hay nada que re-armar. Confirmado.

## 4. HONESTIDAD DE PANTALLA (los tres avisos que mentían)

- **Panel "PERFILES — SIZING Y PEOR-CASO"** (`_perfiles_panel_ro.html` +
  `_perfiles_panel` backend): ahora declara el **MODO VIGENTE**. En testigo un
  banner ámbar avisa "hoy se despacha 1 micro a mercado por destino, NO el
  reparto de abajo — esa tabla es del ESTUDIO"; en ejecuta, banner rojo "se
  despacha el reparto de abajo". El Export ya muestra el payload REAL (1 micro)
  porque pasa por `build_scaled`→`build()`.
- **Etiqueta `design_only`** (`strategy_detail.html`): "DISEÑO (no ejecuta)" →
  **"TESTIGO — 1 micro, sin escalera"**. La llave en DB sigue siendo
  `design_only` (sin migración); cambia solo el display + un `title` que lo
  explica.
- **Aviso "⚠ cancel_after: fijar a mano en TradersPost"** (`lab.html` +
  `strategy_detail.html`): OBSOLETO desde RA-2a. Reescrito: "NTEXECG lo
  transmite como `cancelAfter` en cada orden LÍMITE (verificado en el cable
  2026-07-21); ya NO hay que fijarlo a mano".

## 5. Tests

`tests/test_sizing_gateway.py` (12) — cada modo con su tamaño exacto:
- design_only despacha 1 micro a mercado con alerta=7 (el 7 queda en
  `extras.signal_quantity`) · alerta jamás viaja · off/ausente = testigo ·
  execute conserva [5,3,2] sin regresión · exit sin quantity.
- A-6: testigo → cada destino 1 micro · execute → el perfil re-escala su
  reparto · NX-02 (perfil ≤ base).
- short_size_factor sobre 1 micro se queda en 1 (+ traza) · execute reparte
  sin regresión.

Re-anclados: `test_payload_builder.test_signal_price_..._quantity_is_witness`
(antes pineaba passthrough de la alerta) · `test_escalera_mr5c` — los tres de
ENTRADA ÚNICA (`no_toca_largos_ni_salidas`, `sin_factor_simetrico`,
`factor_invalido_ignorado`) ahora pinean el testigo + `signal_quantity`. La
asimetría REAL de cortos vive en el escalonado, que esos tests siguen
cubriendo intactos.

## 6. Verificación

- Dirigidos (sizing_gateway + payload_builder + scaled_engine +
  dispatch_profiles + gate + mr5c + webhook/escalera_add/p0_exit/luxy_golden/
  despacho_e2e/asset_profiles): **verdes** (73 + 88).
- Suite completa: **1485 passed, 7 skipped** (2026-07-21, tras re-anclajes de
  `test_fix_d2` [payload exacto 6J: quantity 1 + signal_quantity] y
  `test_lab_ui` [aviso cancel_after obsoleto]).

## 7. IMPACTO OPERATIVO al desplegar

**Regla determinista**: al desplegar, TODA estrategia que NO esté en
`execute`/`live` deja de despachar la cantidad de su alerta y pasa a **1 micro
a mercado por destino habilitado**. Las que están en `execute` (p. ej. ES con
la escalera viva) **no cambian** — siguen con el reparto del estudio.

**Evidencia en vivo (handoff 2026-07-20)**: GC despachaba 2 (alerta) → **1**;
RTY 5 (alerta) → **1**. El handoff declara **6 estrategias en `design_only`**
que hoy mandan 1–5 micros según su alerta y pasarían a **1**.

**Dirección del cambio: SIEMPRE a la baja o igual** (nunca sube el riesgo):
- 1-micro-alerta → 1 (sin cambio).
- N-micros-alerta (N>1) → 1 (baja el riesgo al mínimo).
- Los perfiles de esas estrategias que hoy reciben el tamaño íntegro también
  caen a 1 (A-6) → baja adicional por cuenta.

**Roster exacto a confirmar en el server** (read-only, para el reporte de
despliegue) — enumerar modo + quantities por estrategia:

```sql
-- por cada estrategia: modo de scale_entry y su reparto configurado
SELECT strategy_id,
       pipeline_config_json->'scale_entry'->>'mode'        AS mode,
       pipeline_config_json->'scale_entry'->'quantities'   AS quantities
FROM strategy_profiles ORDER BY 1;
```

Las que salgan con `mode` ∉ (execute, live) son las que cambian a 1 micro al
desplegar. (Ajustar tabla/llave al esquema real si difiere.)

## 8. Protocolo §0 — SIN commit

Implementado, dirigidos + e2e verdes, revisión profunda pendiente de tu OK.
Nada se comitea hasta entonces. Al encender: confirmar el roster §7 en el
server y adjuntar el antes/después por estrategia al reporte de despliegue.
