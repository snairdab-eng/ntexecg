# GUÍA EJECUTABLE — Pruebas TradersPost desde NTEXECG · 2026-07-16 (noche)

> Órdenes generadas simulando a LuxAlgo: POST directo al webhook de NTEXECG.
> Ejercita el flujo COMPLETO (recepción → L1–L5 → payload → TradersPost paper).
> Cuentas paper confirmadas — no importa ganar/perder. Cada prueba deja huella:
> anotar resultado y desviación. Desviación = reporte al arquitecto.

## 0. Preparación (una vez)

1. Pre-requisitos del PLAN §0 verificados (tick_size, cancel_after manual en
   TradersPost ≤60min, force_flat_time con margen).
2. Bridge NT ACTIVO (heartbeat fresco) — sin él, toda entrada bloquea (L1.6).
3. Estrategia de trabajo: **ES5m_ConfNormal_TC_TSR** (PAPER, sana, aplicada).
4. Kill-switch: para que el envío sea REAL a TradersPost paper, las 4 capas
   abiertas para esa estrategia (badge NX-03 sin DRY_RUN).
5. En PowerShell, define el kit (rellena dominio, token y precio ACTUAL de MES):

```powershell
$base = "https://TU-DOMINIO"          # el host del server
$sid  = "ES5m_ConfNormal_TC_TSR"      # strategy_id
$tok  = "TOKEN-DE-LA-ESTRATEGIA"      # su token de webhook

function Send-Signal($action, $sentiment, $price, $qty = "1") {
  $body = @{ ticker="ES1!"; action=$action; sentiment=$sentiment;
             quantity=$qty; price="$price"; interval="5" } | ConvertTo-Json
  Invoke-RestMethod -Method Post -ContentType "application/json" `
    -Uri "$base/webhooks/luxalgo/$sid`?token=$tok" -Body $body
}
# Entrada larga:  Send-Signal buy  long  6425.50   (usa el precio VIVO de MES)
# Salida:         Send-Signal exit flat  6425.50
```

Notas: `ticker` = el símbolo TV de la estrategia (el de sus alertas; si difiere
→ BLOCK symbol_mismatch, que también es una prueba). `interval` = el tf de la
estrategia ("5"). `price` = P0: úsalo SIEMPRE cercano al precio vivo — el SL/TP
y las piernas se calculan desde él. Si un BLOCK dice `signal_stale`, añade
`time` con la hora actual ISO al body.

## Secuencia de pruebas (en este orden)

### T1 — Seguridad primero (no toca el mercado)
`?token=INVALIDO` → 401. Repite 25 veces en <60s → desde la 21 no crecen filas
(RawSignal ni audit; FIX-D1). Luego una con token bueno pero `ticker="XX1!"` →
200 y decisión BLOCK symbol_mismatch (guardarraíl vivo, 0 deliveries).

### T2 — Entrada escalonada (C.14/15)
`Send-Signal buy long <precio-vivo>` → verificar:
- NTEXECG: decisión APPROVE (traza L1–L5), Posiciones PENDING→LONG,
  WebhookDelivery por pierna (3) con payload absoluto al tick.
- TradersPost: llegan 3 órdenes MES — C1 mercado (llena), C2/C3 LÍMITE a
  P0−1.64×ATR y P0−3.28×ATR exactos, stop común y TP en todas.
- Cotejar los precios del payload_json contra TradersPost dígito a dígito.

### T3 — LA CRÍTICA: exit con piernas vivas (C.17 / FIX-D3)
Con la posición de T2 viva y C2/C3 AÚN pendientes (no esperes la hora):
`Send-Signal exit flat <precio-vivo>` → verificar:
- TradersPost: posición cerrada Y las límites pendientes CANCELADAS
  (el cancel:true tomó). ANOTA si el bracket también murió limpio.
- NTEXECG: AuditLog EXIT_CANCEL_LEGS con any_sent=true; posición FLAT.

### T4 — Expiración natural + reserva (C.15b)
Nueva entrada (espera >60s de T3 por el dedupe). Esta vez NO mandes exit:
- C2/C3 expiran solas al cancel_after (~60 min) en TradersPost.
- La posición de C1 queda viva con SL/TP → ciérrala con exit al final, o
  déjala para T8 (forced EOD).

### T5 — Dedupe + symbol_busy
- Dos `buy` idénticos en <60s → UNA sola decisión (NX-10).
- Con posición viva, otro `buy` → BLOCK symbol_busy.

### T6 — Reversal
Con posición LONG viva: `Send-Signal sell short <precio>` → cierra el largo
primero; abre el corto SOLO si allow_reversal (verifica la traza NX-27).

### T7 — C1 móvil de punta a punta (D.23-24 / LX-15)
1. UI: pestaña Luxy → mover slider C1 a ~0.5×ATR → Recalcular → participación
   cae → Aplicar estas palancas (gate ÁMBAR "C1 móvil" + checkbox) → audit
   APPLY_LUXY_PALANCAS.
2. `Send-Signal buy long <precio>` → en TradersPost la ENTRADA BASE llega
   LÍMITE a P0−0.5×ATR (jamás mercado).
3. Si no llena: expira por cancel_after → NX-28 libera la reserva
   (RESERVE_RELEASED, symbol vuelve a libre). Si llena: piernas y stop normales.
4. Restablecer → Aplicar → C1 vuelve a 0 (mercado), badge "aplicada".

### T8 — Cierres forzados
Deja una posición viva hasta force_flat_time (o baja max_holding temporal en
la config para no esperar) → FORCED_EXIT con cancel_requested; TradersPost
plano.

### T9 — Datos vivos (B.9)
Con el mercado abierto, APAGA NinjaTrader 3-4 min → `buy` → BLOCK
market_data_not_active; un `exit` (con posición) SÍ pasa. Reenciende NT →
heartbeat fresco → `buy` pasa.

### T10 — Alcance del cancel (C.18, adenda D-3) — versión segura
No hace falta reactivar ConfStrong: pon A MANO una orden límite MES en la
cuenta paper (desde TradersPost/broker), lejos del precio. Luego T2+T3 con
ConfNormal → al exit con cancel:true, ¿tu orden manual sobrevivió?
- Sobrevive → el cancel es por estrategia-TradersPost (scope estrecho, bien).
- Muere → el cancel es por cuenta/ticker → JAMÁS dos estrategias del mismo
  ticker vivas; queda documentado.

### T11 — Entrega fallida (C.22) — opcional avanzado
Perfil de prueba con URL inválida → entrada fallida → FLAT; exit fallido →
UNKNOWN + L3 bloquea. Hazla al final: ensucia el estado a propósito.

## Después de cada prueba, registrar

| # | Hora ET | Señal | Decisión (L1-L5) | TradersPost (qué se vio) | Audit | ✓/✗ |

El registro alimenta la fase F del plan (reconciliación ExecutionResult vs
estudio cuando importes los fills de paper).
