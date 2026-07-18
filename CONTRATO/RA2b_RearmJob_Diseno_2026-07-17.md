# RA-2b — RearmJob (re-armado de piernas en despacho) · DISEÑO · 2026-07-17

> REVISIÓN PROFUNDA (toca despacho real). Este documento resuelve los 7 puntos
> del plan + las TRES PRECISIONES del arquitecto (P1 estado persistente, P2
> constantes con fuente única, P3 fuente de inferencia de precio) con evidencia
> archivo:línea. **Nada se implementa hasta la aprobación de este diseño.**
> Default global: nadie re-arma hasta que el operador aplique `rearm.enabled` por
> estrategia tras observar en demo.

## 0. Supuesto de worker (P1-2, documentado)

El scheduler corre **un solo worker**: `ExitManagerJob` (`app/core/scheduler.py:147-202`)
usa su propio `AsyncIOScheduler` + `AsyncSessionLocal()` por ciclo y **no** hay
lock distribuido ni elección de líder. `RearmJob` HEREDA ese supuesto: **1
instancia del servicio**. Si algún día hay >1 worker, el re-armado necesitaría un
advisory-lock por (account,symbol) — FUERA DE ALCANCE aquí, se deja anotado como
riesgo. La persistencia en DB (P1) hace el job **idempotente ante restart**, no
ante concurrencia de workers.

## 1. Config nuevo — `scale_entry.rearm` (llave NUEVA, default AUSENTE=OFF)

```jsonc
"scale_entry": {
  "mode": "execute", "quantities": [...], "levels": [...],
  "rearm": {                      // AUSENTE ⇒ OFF. Nace SOLO vía Aplicar/gate.
    "enabled": true,              // el operador la enciende tras demo (RA-3 UI)
    "max_ciclos": 3,              // R-RA4 — del veredicto RA-0v3 (n/s→1=OFF)
    "k_sobre_c0": 1.0,            // R-RA3 — sembrado del veredicto
    "umbral_atr": 1.5,            // R-RA7 — ATR vivo/señal
    "min_antes_cierre_min": 30,   // R-RA8 — default conservador
    "timeframe": "5m"             // serie del feed para inferencia (P3)
  }
}
```

- **Jamás nace sola**: solo la escriben `config_from_overrides`/`activacion_from_study`
  (mismo patrón FIX-FX-BACKSTOP), sembrada del veredicto RA-0v3 del panel Piernas.
  Un `rearm` presente en un merge NO enciende nada si falta `enabled=true`.
- `config_resolver` debe SUPERFICIAR `rearm` al effective config (igual que
  `scale_entry`, `app/services/config_resolver.py:235-238`). Sin eso el job no lo ve.
- **Gate**: encender `rearm.enabled` pasa por el gate LX-11 como cualquier palanca
  (un re-armado ON es riesgo → mínimo ÁMBAR, revisar en RA-3).

## 2. Estado del ciclo PERSISTENTE (P1) — DB, no memoria

**Vive en `PositionState.risk_plan_json["rearm"]`** (`app/models/position_state.py:43`,
JSON nullable; ya guarda `opened_at` en `risk_plan_json`, `position_service.py:74`).
Una fila por (account,symbol) — `UniqueConstraint` en `position_state.py:49`.

```jsonc
risk_plan_json["rearm"] = {
  "legs": [
    { "leg_index": 2, "side": "long", "level_atr": 1.64,
      "limit_price": 4984.25, "qty": 3,
      "cycle_n": 2, "last_client_id": "…-r2",
      "last_sent_at": "2026-07-17T14:31:05Z",
      "state": "working",          // working | dead | assumed_filled
      "death_reason": null },      // qué R-RA la mató
    …
  ],
  "signal_atr": 8.0,               // ATR de la señal (para R-RA7, congelado en la entrada)
  "sl_price": 4990.0, "tp_price": 5010.0,   // para R-RA6 (muerte inferida)
  "updated_at": "2026-07-17T14:31:05Z"
}
```

**Restart a mitad de ciclo (invariante duro):**
1. Al inicio de CADA barrido, el job RELEE `risk_plan_json["rearm"]` de la DB.
2. Legible y coherente → continúa el ciclo desde `cycle_n`/`last_sent_at` (jamás
   "ciclo 1 otra vez" — eso arriesga doble orden viva).
3. Ausente / malformado / campos faltantes → **fail-closed**: no re-arma, AuditLog
   `REARM_SKIP{motivo:"estado_ilegible"}`. Una orden que no podemos razonar NO se
   re-envía.
4. El job SOLO escribe `risk_plan_json["rearm"]`; jamás toca `state`/`direction`/
   `quantity` (esos son de `position_service`). Lee-modifica-escribe dentro de su
   propia transacción (`async with AsyncSessionLocal()`, patrón `scheduler.py:179`).

## 3. Ciclo SIN SOLAPE (timing)

- TTL de pierna = `cancelAfter` (RA-2a) = `entry_reserve_timeout_seconds` (default
  **3600s**). TradersPost la caduca sola al vencer.
- Re-envío cuando `now ≥ last_sent_at + TTL + GUARDA_CIEGA` (GUARDA_CIEGA ≈ 60-120s
  → minuto 61-62), **después** de que el cancelAfter ejecutó con CERTEZA. El job
  corre cada 60s (`scheduler.py:160`) y solo dispara cuando esa condición se cumple.
- **JAMÁS dos órdenes vivas al mismo precio** (fill doble = 2× posición). La ventana
  ciega [60,62) es costo aceptado (asimetría de la misión).

## 4. Motor de inferencia — jerarquía R-RA9 (primera que dispara, corta)

Orden: **R-RA5/6 > R-RA1 > R-RA2 > R-RA7 > R-RA3/4/8**.

| Regla | Condición | Acción | Fuente |
|-------|-----------|--------|--------|
| **R-RA5** | posición cerrada / `state∈{EXITING,FLAT,REVERSING,UNKNOWN,LOCKED}` | matar re-armados | `position_state.state` (`filter_pipeline.py:468,481`) |
| **R-RA6** | backstop o TP tocados por precio (inferido) | matar YA (huérfana post-stop) | feed (P3) vs `sl_price`/`tp_price` |
| **R-RA1** | heartbeat viejo (feed ciego) | fail-closed, no re-arma | `market_data.is_active` (mismo umbral L1.6) |
| **R-RA2** | nivel YA tocado | jamás re-enviar. Toque c/orden viva ⇒ ASUMIR FILL; toque en ciega ⇒ pierna muerta | feed (P3) vs `limit_price` |
| **R-RA7** | ATR vivo / ATR señal > `umbral_atr` | no re-arma | `get_atr` vs `signal_atr` |
| **R-RA3** | precio `k_sobre_c0`×ATR favorable a C0 a hora t | no (pullback improbable) | feed vs C0 |
| **R-RA4** | `cycle_n > max_ciclos` | no | estado persistente |
| **R-RA8** | a < `min_antes_cierre_min` de 17:00 ET | no | `ZoneInfo("America/New_York")` |

UNKNOWN / EXITING / estado ilegible / feed con hueco → **fail-closed** (§6).

## 5. Mecánica del re-envío

- **Misma orden límite**: `limit_price` y `side` idénticos; `qty` RECALCULADA si el
  perfil cambió (respeta `max_micro_contracts` del catálogo vigente). Reusa
  `PayloadBuilder.build_scaled` NO — se construye la pierna única re-armada
  directamente (un helper `build_rearm_leg`) para no re-emitir C1/C2/C3 completos.
- **Client id correlacionado**: el payload actual no lleva `client_order_id`
  (idempotencia por `signal_id`+`leg_index`, `payload_builder.py:283`). Se añade
  `extras.rearm_cycle=n` y `extras.client_id="<base>-r{n}"` para correlación en
  TradersPost y en el AuditLog.
- **Mismo gate**: `resolve_effective_dry_run(settings, dest_config)`
  (`webhooks_luxalgo.py:326-342`) por destino, + kill-switch por capas. Un
  `dry_run`/pausa corta re-armados EXACTO como entradas. `cancelAfter` en el re-envío.
- Registra `WebhookDelivery` por re-envío (patrón `webhooks_luxalgo.py:441-454`).

## 6. AuditLog (reconstruir la demo)

`AuditService().log(db, actor="rearm_job", action=…, object_type="PositionState",
object_id=f"{account}:{symbol}", new_value={…})` (`audit_service.py:16-53`).

- **REARM_LEG** por re-envío: `{leg_index, cycle_n, limit_price, qty, destino, client_id}`.
- **REARM_KILL** por muerte: `{leg_index, regla:"R-RA6", detalle}` — QUÉ regla cortó.
- **REARM_SKIP** por fail-closed: `{motivo:"heartbeat_viejo"|"estado_ilegible"|"feed_hueco"}`.

## 7. Fail-closed matrix + adversariales

| Escenario | Resultado |
|-----------|-----------|
| UNKNOWN / estado ilegible | no re-arma (R-RA5 + §2.3) |
| EXITING | no re-arma |
| heartbeat viejo | no re-arma (R-RA1) |
| hueco en el feed | no re-arma (P3 fail-closed) |
| tras exit con `cancel:true` | el job ve piernas muertas (state≠abierto) → **no revive nada** |
| dry_run / pausa (kill-switch) | corta re-armados como entradas |
| perfil cambió a media vida | recalcula qty con catálogo vigente |
| > max_ciclos | no re-arma (R-RA4) |

Tests adversariales: **cada R-RA1..9 una por una**, sin-solape (60 vs 61-62),
cotas, correlación de ids, kill-switch corta, perfil→recalcula qty, post-exit no
revive, restart→relee estado (no ciclo 1), AuditLog completo.

---

## P2 — Constantes (fuente ÚNICA, default conservador)

| Constante | Regla | Fuente única | Default |
|-----------|-------|--------------|---------|
| `NTBRIDGE_HEARTBEAT_MAX_AGE` | R-RA1 | `app/core/config.py:53` (**el MISMO de L1.6**, no uno propio) | 60 s |
| `min_antes_cierre_min` | R-RA8 | `rearm.min_antes_cierre_min` | **30 min** |
| cierre de sesión | R-RA8 | `force_flat_time`/17:00 ET (`exit_manager.py:47`, `ZoneInfo`) | por sesión |
| `umbral_atr` | R-RA7 | `rearm.umbral_atr` | 1.5 |
| `k_sobre_c0` | R-RA3 | `rearm.k_sobre_c0` (sembrado veredicto RA-0v3) | del veredicto |
| `max_ciclos` | R-RA4 | `rearm.max_ciclos` (sembrado RA-0v3; n/s→1=OFF) | 1 (OFF) |
| TTL / `cancelAfter` | §3 | `entry_reserve_timeout_seconds` (RA-2a) | 3600 s |
| GUARDA_CIEGA | §3 | constante del job | 60-120 s |
| `timeframe` | P3 | `rearm.timeframe` | "5m" |

## P3 — Fuente de la inferencia de precio (declarada)

**Serie:** `MarketDataService.get_bars(symbol, timeframe, limit)`
(`app/services/market_data_service.py:262-280`) → últimas N barras
`{time, open, high, low, close, volume}` del bridge NinjaTrader. **NO hay query por
rango temporal** → se filtra EN MEMORIA por `time ≥ opened_at`
(`risk_plan_json["opened_at"]`).

- **max_high / min_low desde la entrada** = `max(b.high)` / `min(b.low)` sobre las
  barras con `time ≥ opened_at`. (NO existe hoy — se construye.)
- **Nivel tocado (R-RA2)**: long, pierna bajo la entrada → `min_low ≤ limit_price`;
  short, pierna sobre la entrada → `max_high ≥ limit_price`.
- **Backstop/TP tocados (R-RA6)**: long → `min_low ≤ sl_price` (stop) o
  `max_high ≥ tp_price` (TP); short al revés.
- **ATR vivo (R-RA7)**: `MarketDataService.get_atr(symbol, timeframe)`
  (`market_data_service.py:282-288`) vs `signal_atr` congelado en el estado.

**HUECOS = ilegible = fail-closed** (obligatorio):
1. barras esperadas en `[opened_at, now]` dado el `timeframe` vs barras reales
   filtradas; si `reales < esperadas − tolerancia` → hueco.
2. cualquier `high/low` None/ausente en el tramo → ilegible.
3. barra más nueva más vieja que `NTBRIDGE_HEARTBEAT_MAX_AGE` → feed frío.
En cualquiera de los tres: **no se infiere, no se re-arma**, AuditLog
`REARM_SKIP{feed_hueco}`. Test de cada caso (tocado / no-tocado / hueco / None /
backstop tocado / TP tocado / régimen ATR).

## Orden de implementación propuesto (sub-pasos de RA-2b)

1. Config + resolver + Aplicar (llave `rearm`, gate, sembrado) — sin job aún.
2. Estado persistente en `risk_plan_json["rearm"]` + helpers de lectura fail-closed.
3. Inferencia de precio P3 (bars→high/low, tocado, gap) — módulo puro + tests.
4. Motor de reglas R-RA9 (puro, sobre estado+inferencia) + tests una-por-una.
5. `RearmJob` en el scheduler (60s) + re-envío por el mismo gate + WebhookDelivery.
6. AuditLog (LEG/KILL/SKIP) + adversariales E2E (restart, post-exit, kill-switch).
