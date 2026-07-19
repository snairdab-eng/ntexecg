# RA-2b SUB-PASO 6 — E3b + adversariales E2E (el cierre del RearmJob)

> 2026-07-19 · `tests/test_rearm_adversarial_ra2b6.py` (14 tests) + E3b en
> `app/services/rearm_job.py`. Tras este lote, RA-3 (UI + sembrado vía gate)
> es lo único entre el re-armado y la demo. Protocolo §0.

## 1. E3b — caso MIXTO (hereda de E3)

Un destino SENT/DRY_RUN + CUALQUIER intento AMBIGUO en el mismo re-envío
(FAILED-ambiguo en otro destino, **o un SENT que necesitó un intento ambiguo
antes en el MISMO destino** — extensión coherente, testeada aparte) ⇒ tras
registrar las deliveries del ciclo, la pierna se **MATA**
(`REARM_KILL{envio_ambiguo_parcial, "posible orden fantasma en un destino —
el siguiente ciclo duplicaría; asimetría de la misión (las órdenes enviadas
de este ciclo viven hasta su cancelAfter; solo se prohíbe el futuro
re-envío)"}`). El SENT+FAILED-inequívoco **avanza normal** (el inequívoco no
contamina). Implementación: `_reenviar_pierna` devuelve `"ambiguo_parcial"`
cuando `any_ok ∧ any_ambiguo` (rearm_job.py); el flag por-intento del
cliente (E3) es la señal.

Tests: mixto SENT+ambiguo ⇒ muerta, 2 deliveries registradas, el siguiente
barrido no llama a send · mixto SENT+500 ⇒ ciclo 2 normal + REARM_LEG ·
SENT-tras-intento-ambiguo (mismo destino) ⇒ muerta.

## 2. Adversariales E2E (pipeline real con fakes)

| Caso | Resultado fijado |
|---|---|
| (a) **RESTART a mitad de ciclo** | el "restart" ES releer de DB: barrido nuevo (sesiones nuevas) tras ciclo 2 ⇒ continúa a ciclo 3 (`-r3`), JAMÁS "ciclo 1 otra vez"; deliveries con rearm_cycle [2, 3] |
| (b) **POST-EXIT no revive** | exit real vía position_service (cancel:true ya canceló las límite) → FLAT ⇒ R-RA5 mata, 0 deliveries, el job no toca la posición |
| (c) **KILL-SWITCH por capas** | cada capa cerrada (env TRADERSPOST_ENABLED / env DRY_RUN / cfg traderspost_enabled / cfg dry_run) con las otras tres ABIERTAS ⇒ el gate resuelve dry_run=True (espía sobre send) y toda delivery queda DRY_RUN sin sent_at |
| (d) **enabled apagado entre ciclos** | ciclo 2 enviado → operador apaga → siguiente barrido `REARM_SKIP{disabled}`, cero deliveries nuevas |
| (e) **UNKNOWN por exit fallido** | on_exit_failed → UNKNOWN ⇒ R-RA5 mata |
| (f) **feed muerto a mitad de vida** | `REARM_SKIP{R-RA1}` SIN matar (working, ciclo intacto); el feed revive ⇒ el siguiente barrido re-envía (ciclo 2) |
| (g) **TTL editado por fuera tras la siembra** | config vigente 1800 ⇒ `REARM_SKIP{ttl_incoherente}` (defensa E1 del barrido), pierna intacta, 0 deliveries |

## 3. RECONSTRUCCIÓN — la demo audita sin leer risk_plan_json

Vida completa simulada: siembra C2/C3 → re-envío de ambas (ciclo 2, 12:00) →
dip a 5491.5 en ventana VIVA del ciclo 2 ⇒ C2 `REARM_ASSUMED` → crash a 5487
cruza el backstop ⇒ C3 `REARM_KILL{R-RA6}`. La historia se reconstruye SOLO
desde `WebhookDeliveries.extras{leg_index, rearm_cycle}` + AuditLog
(REARM_LEG/ASSUMED/KILL) y coincide **bit a bit** con
`{leg_index: {cycle_n, state, death_reason}}` del estado final:
C2 = {2, assumed_filled, None} · C3 = {2, dead, R-RA6}.

## Suite

34 verdes en los archivos RA-2b/5-6 (14 nuevos + sub-paso 5 y E3 intactos).
Suite completa: ver cierre.

## Estado de RA-2b tras este lote

Sub-pasos 1-6 COMPLETOS (config+gate → estado persistente → inferencia →
motor R-RA9 → RearmJob+E3/E3b → adversariales+reconstrucción). Pendiente:
**revisión profunda del arquitecto + commit** (sub-pasos 5-6 juntos), y
después **RA-3** (UI + sembrado del veredicto vía gate) — lo único entre el
re-armado y la demo.
