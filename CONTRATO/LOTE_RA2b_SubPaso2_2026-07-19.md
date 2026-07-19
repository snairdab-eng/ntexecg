# RA-2b SUB-PASO 2 — Estado del ciclo PERSISTENTE (diseño §2 + E1/E2)

> 2026-07-19 · Solo la capa de ESTADO — el RearmJob (sub-paso 5) NO existe;
> cero despacho nuevo. Cero cambios de esquema (risk_plan_json ya es JSON).
> Base: CONTRATO/RA2b_RearmJob_Diseno_2026-07-17.md §2.

## Qué se implementó

### 1. Sembrado al despachar (`app/api/webhooks_luxalgo.py`)

Tras `on_entry_approved` de una entrada NO-exit, si la config efectiva lleva
`scale_entry.rearm.enabled=true`, se siembra `risk_plan_json["rearm"]` desde
las piernas LÍMITE del destino PRIMARIO **tal como se despacharon** (los
mismos `limitPrice`/`qty`/`level_atr`/`leg_index` de los payloads — no una
reconstrucción), con el shape exacto del diseño §2:

```
legs[{leg_index, side, level_atr, limit_price, qty, cycle_n=1,
      last_client_id=None, last_sent_at, state:"working", death_reason:null}]
+ signal_atr (congelado, R-RA7) + sl_price/tp_price (R-RA6) + updated_at
```

- C1 a mercado NO entra (no es re-armable); sin ninguna pierna límite no se
  siembra nada. `last_client_id` nace None (el envío inicial no lleva client
  id propio; los re-envíos usarán `"<base>-r{n}"`, diseño §5).
- **Sin `rearm.enabled` → NO se siembra nada** — cero cambio de
  comportamiento (probado: plan idéntico al de siempre, con `opened_at`).
- **E1**: si `entry_reserve_timeout_seconds ≠ 3600` con rearm ON, el estado
  se siembra CON `"ttl_incoherente": true` — el job (sub-paso 5) lo leerá
  como no-re-armar (defensa en profundidad; el gate LX-11 ya lo pone rojo).

El escritor es `PositionService.set_rearm_state` (método nuevo): SOLO toca
`risk_plan_json["rearm"]` — jamás state/direction/quantity ni las otras
llaves del plan (invariante d, con test).

### 2. Helpers PUROS (`app/services/rearm.py`, junto al sub-paso 1)

- `sembrar_estado(payloads, side, now_iso, ttl_ok)` — puro, sin DB/reloj.
- `leer_estado(risk_plan_json)` → estado VALIDADO o **None si ilegible**
  (fail-closed §2.3): shape completo por llave, tipos estrictos (bool NO
  pasa por int), timestamps parseables, legs no vacías, estados en
  {working, dead, assumed_filled}. Jamás excepción, jamás estado parcial;
  devuelve copia profunda (mutar el resultado no toca el JSON).
- Transicionadores puros (copian, no mutan): `marcar_muerta(leg, razon)`,
  `marcar_assumed_filled(leg)` (E2), `avanzar_ciclo(leg, client_id, ts)` —
  este último SOLO desde "working" (avanzar una muerta/assumed es bug del
  job → ValueError).

## Invariantes fijados por test (`tests/test_rearm_estado_ra2b2.py`, 49 tests)

| Invariante | Test |
|---|---|
| (a) restart = releer de DB, jamás "ciclo 1 otra vez" | round-trip sembrar→avanzar→json→releer: `cycle_n=2`, client id y ts intactos, bit a bit |
| (b) ilegible ⇒ None, nunca excepción/parcial | 29 casos parametrizados: cada llave faltante (top y por pierna), cada tipo malo (incl. `qty=True`), timestamps basura, estados zombie |
| (c) E2: assumed_filled NO muta la posición | foto de PositionState antes/después: idéntica; solo cambió `risk_plan_json["rearm"]` |
| (d) el escritor solo toca `rearm` | `set_rearm_state` sobre posición viva: state/direction/quantity/entry_* y el resto del plan (opened_at, entry_style) intactos |
| (e) E1: TTL≠3600 registra `ttl_incoherente` | por el despacho REAL con ttl=1800 → flag True y estado legible |
| no-siembra sin enabled | despacho real: rearm ausente Y rearm sin `enabled=true` explícito → `"rearm"` no existe en el plan |
| siembra por el pipeline REAL | `process_signal` E2E (harness del despacho): legs [2,3], límites 5492/5484 (P0 5500, ATR 8), sl 5488 (backstop 12), cycle 1, working |

## Suite

Archivos afectados + nuevos: 104 verdes en local (position_service, despacho
E2E, reserve NX-28, scaled_entry, payload_builder intactos). Suite completa:
ver cierre.

## Pendiente

- Sub-paso 3 (inferencia de precio P3) → 4 (motor de reglas) → 5 (RearmJob)
  → 6 (audit + adversariales E2E), según el orden del diseño.
- Commit del arquitecto (Protocolo §0).
