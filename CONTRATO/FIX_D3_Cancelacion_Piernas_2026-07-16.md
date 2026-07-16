# FIX-D3 — Cancelación explícita de piernas al cierre · 2026-07-16

> P2, revisión profunda (toca despacho). Prerequisito de RA-2 (re-armado de piernas
> del SPEC 2026-07-15): las piernas re-armadas deben **morir con la posición**.

## 1. Investigación — ¿qué soporta TradersPost para cancelar por webhook?

**Sí lo soporta.** El esquema oficial del webhook (`TradersPost/docs → webhook-spec.json`)
define:

- **`action`** ∈ `{ "buy", "sell", "exit", "cancel", "add" }`.
- **`cancel`** — booleano de nivel superior. Descripción textual del spec:
  *"Explicitly control whether or not to cancel open orders before submitting new
  orders to your broker."*

Dos formas de cancelar órdenes pendientes:

1. **Acción `cancel` sola** — `{"ticker": "ES...", "action": "cancel"}` cancela las
   órdenes abiertas del ticker (sin enviar otra orden).
2. **Flag `cancel: true` sobre otra acción** — `{"ticker": "ES...", "action": "exit",
   "cancel": true}` **cancela primero** las órdenes de trabajo del ticker y **luego**
   envía el exit. TradersPost lo **recomienda explícitamente** para estrategias que
   mandan exits usando TP/SL con la entrada (para que el broker no rechace el exit
   mientras haya órdenes vivas).

Fuentes:
- <https://docs.traderspost.io/docs/core-concepts/webhooks>
- <https://github.com/TradersPost/docs/blob/main/webhook-spec.json>
- <https://traderspost.io/reference/strategy-message/cancel-open-orders>

## 2. Decisión de diseño

Se usa **`cancel: true` en el payload de EXIT** (no un mensaje `cancel` separado
posterior). Razones:

- **Atómico** — TradersPost cancela-y-luego-aplana en UN mensaje (cancel *before*
  submit). No hay ventana post-cierre donde una pierna profunda (C2/C3) pendiente
  pudiera llenarse contra una posición ya cerrada (**pierna huérfana**, R-RA6 del SPEC).
- **Un solo punto** — todo exit (LuxAlgo normal Y `forced_exit`) se construye con
  `PayloadBuilder.build`; `cancel: true` se añade ahí sólo cuando `action == "exit"`.
  Las entradas **jamás** lo llevan (cancelaría su propio bracket).
- **Cubre RA-2** — cuando exista el re-armado, sus piernas re-enviadas son órdenes de
  trabajo del mismo ticker; el `cancel: true` del exit las cancela igual, sin código
  nuevo en el flujo de cierre.

## 3. Riesgo residual (aceptado, visible)

`cancel: true` es **best-effort**: depende de que el broker lo honre y de que la
entrega del webhook tenga éxito. Cuando **no** toma:

- **Entrega FALLIDA** del exit → la posición pasa a **UNKNOWN** (NX-08): L3 bloquea
  entradas hasta revisión. Visible en `PositionState` y en el AuditLog.
- **Ventana de exposición residual** = `≤ cancel_after` restante de la pierna. Acotada
  por el `cancel_after` nativo de TradersPost (la pierna caduca sola) y por
  **NX-28 `release_unfilled_reservations`** (libera la reserva-fantasma `symbol_busy`
  tras `entry_reserve_timeout_seconds ≈ cancel_after`).

No se añade un job nuevo de "vigilancia de pierna huérfana llenada": sin confirmación
de fills desde TradersPost (R-RA2: los fills se **infieren** por precio), esa detección
pertenece a RA-2 (job de re-armado con inferencia por feed vivo). Aquí basta el
mecanismo soportado + la cota NX-28; el camino de inferencia se implementa en RA-2.

## 3-bis. ADENDA DEL ARQUITECTO (revisión 2026-07-16) — residuales adicionales

- **Rechazo post-cancel (posición desnuda):** el orden cancela-luego-aplana
  implica que si el cancel toma y el flatten es RECHAZADO por el broker después
  del 200 de TradersPost (invisible para NTEXECG), la posición queda abierta SIN
  bracket — antes de D3 un exit fallido dejaba el bracket vivo. Escenario
  concreto: forced_exit EOD contra la ventana de mantenimiento CME 17:00–18:00
  ET. Mitigación: force_flat_time con margen sobre las 17:00 ET + observación en
  demo (reconciliar EXIT_CANCEL_LEGS vs estado real en TradersPost).
- **Alcance del cancel con ticker compartido (MES ×2):** verificar en demo si el
  cancel opera a nivel estrategia-TradersPost o a nivel cuenta/ticker. Hasta
  confirmarlo, JAMÁS dos estrategias del mismo ticker vivas a la vez (hoy lo
  garantizan ConfStrong pausada + PortfolioGuard regla 1).

## 4. Registro (AuditLog)

- **Exit normal** (`webhooks_luxalgo`) → `EXIT_CANCEL_LEGS` con
  `{cancel_requested, any_sent, any_failed, role}`.
- **Forced exit** (`forced_exit`) → `FORCED_EXIT` extendido con `cancel_requested: true`.

La demo puede reconstruir, por cierre, que se pidió cancelar las piernas y si llegó
al broker (`any_sent`).

## 5. Tests adversariales

- **Camino soportado (happy):** exit (normal y forced) lleva `cancel: true`; se audita.
- **Adversarial — entradas:** ninguna entrada (simple ni escalonada C1..Cn) lleva
  `cancel` (cancelaría su bracket).
- **Adversarial — residual:** exit con entrega FALLIDA → `cancel` no tomó → posición
  UNKNOWN (visible); la cota NX-28 sigue libre.
