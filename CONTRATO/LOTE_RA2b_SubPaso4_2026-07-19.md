# RA-2b SUB-PASO 4 — Motor de reglas R-RA9 (PURO)

> 2026-07-19 · `app/services/rearm.py` (junto a los sub-pasos 1-3) — sin DB,
> sin scheduler, sin despacho; consume el estado (sub-paso 2) y la
> inferencia (sub-paso 3). Base: RA2b_RearmJob_Diseno_2026-07-17.md §3-§4.

## La función central

`decidir_pierna(leg, *, estado, posicion, inferencia, cfg_rearm, now_et)` →
**una acción** `{accion, regla, detalle}` con
`accion ∈ {REENVIAR, ESPERAR, MATAR, SKIP, ASSUMED_FILLED}` — el par
(regla, detalle) viaja tal cual al AuditLog del sub-paso 5.

Jerarquía estricta (la primera que dispara, corta):
**E1** (ttl_incoherente sembrado ⇒ SKIP antes que todo) → guard pierna
no-working (sin acción) → **R-RA5** (EXITING/FLAT/REVERSING/UNKNOWN/LOCKED ⇒
MATAR; estado no razonable —PENDING/None/basura— ⇒ SKIP fail-closed sin
matar) → **R-RA6** (backstop o TP tocados ⇒ MATAR huérfana, lados invertidos
correctos por lado) → **R-RA1** (inferencia None O ATR vivo ilegible ⇒ SKIP —
jamás matar a ciegas) → **R-RA2** doble lectura (ver abajo) → **R-RA7**
(atr_expandido True ⇒ MATAR; None ya cayó en R-RA1) → **timing §3** →
**R-RA3** (excursión favorable ≥ k×ATR señal ⇒ ESPERAR este ciclo; sin
entry_price/close ⇒ SKIP fail-closed) → **R-RA4** (cycle_n ≥ max_ciclos ⇒
MATAR agotado; max_ciclos=1 = OFF efectivo) → **R-RA8** → REENVIAR.

## Decisiones de diseño (justificadas en el código)

1. **El timing va DESPUÉS de las muertes/toques y ANTES de las reglas de
   re-envío**: una huérfana con el stop tocado se mata YA, no al minuto 62.
   Y R-RA4 solo es juzgable cuando el re-envío está debido — matar la pierna
   antes de que expire su orden viva perdería el rastreo que R-RA2 necesita
   (la orden del ciclo actual sigue viva hasta su cancelAfter).
2. **ASSUMED_FILLED como 5ª acción**: R-RA2 con toque en ventana viva exige
   la transición del sub-paso 2 (`marcar_assumed_filled`) — semánticamente
   distinta de MATAR (bloquea el re-envío Y cuenta exposición, E2; jamás
   toca la posición). El job sabrá qué transicionador llamar.
3. **R-RA8 ⇒ ESPERAR, no MATAR**: si el flujo llegó a R-RA8 es que aún queda
   horizonte (R-RA4 va antes); matar por reloj quemaría horizonte del
   estudio por un artefacto del cierre — la pierna se re-evalúa tras la
   reapertura y la orden viva ya tiene su cancelAfter. Con el mercado ya
   cerrado la inferencia cae antes en R-RA1 (frescura del sub-paso 3).
4. **Excursión favorable de R-RA3** = último `close` del tramo de la
   inferencia vs `entry_price` de la posición (por lado), contra
   `k_sobre_c0 × signal_atr` (el ATR congelado del estado).

## Helpers de timing (§3, sin solape)

- `REARM_GUARDA_CIEGA_S = 120` → `REARM_CICLO_S = 3600 + 120 = 3720 s =
  62 min` — EXACTAMENTE el `REARM_CYCLE_MIN=62` del modelo RA-1 del estudio
  (coherencia de horizonte declarada).
- `toca_reenviar(last_sent_at, now)` — True solo con `now ≥ last_sent +
  TTL + GUARDA` (el cancelAfter ejecutó con certeza; jamás dos órdenes vivas
  al mismo precio). Normaliza tz (los legs guardan UTC ISO; las barras ET).
- `atribuir_toque(t_toque, last_sent_at)` → `viva | ciega` por aritmética
  MODULAR de ciclos: `pos = (t_toque − last_sent) mod 3720`; `pos < 3600 ⇒
  viva`, `pos ∈ [3600, 3720) ⇒ ciega`. Funciona para toques de ciclos
  PREVIOS (delta negativo cae en su posición de ciclo). Granularidad de
  barra: t_toque = INICIO de la primera barra que toca; barra que abre en
  viva ⇒ viva (lado assumed_filled, el conservador: cuenta exposición y
  bloquea re-envío). Una sola lectura por toque: el PRIMER toque del tramo
  decide — nunca dos lecturas del mismo toque.

## Tests (`tests/test_rearm_motor_ra2b4.py`, 28)

Una regla por test con acción exacta (R-RA5 parametrizada en sus 5 estados +
fail-closed en no-razonables; R-RA6 por backstop/TP y por lado; R-RA2
viva⇒ASSUMED_FILLED / ciega⇒MATAR; R-RA3 con umbral por lado y borde justo
debajo; R-RA4 incl. max_ciclos=1=OFF; R-RA8 con ZoneInfo en 16:31/16:30) ·
**6 conflictos de jerarquía** (E1>todas · RA5>RA6 · RA6>RA2 · RA1>RA4
—jamás matar a ciegas— · RA2>RA7 · RA7>RA4) · timing 3719/3720 exactos ·
atribución en los 6 bordes (0/3599/3600/3719/3720/−100/−3720) · contrato de
acción {accion, regla, detalle} siempre completo.

## Suite

118 verdes en local (28 nuevos + sub-pasos 1-3 intactos). Suite completa:
ver cierre. Pendiente: sub-paso 5 (RearmJob — LA revisión profunda) → 6
(AuditLog + adversariales E2E). Commit del arquitecto (Protocolo §0).
