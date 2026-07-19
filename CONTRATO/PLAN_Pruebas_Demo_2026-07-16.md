# PLAN DE PRUEBAS — Observación en demo · 2026-07-16

> Arquitecto: Fable 5. Fase: validación E2E en demo/paper de todo lo desplegado
> en la etapa (FIX-FLAKE, RA-0v2/v3, FIX-D1..D4-bis, LX-15 completo).
> Regla general: cada prueba deja huella (AuditLog/WebhookDelivery) — anotar
> fecha, resultado y desviación. Una desviación = reporte al arquitecto antes
> de seguir. TODO en paper/demo — jamás real.

## 0. PRE-REQUISITOS (verificar ANTES de cualquier prueba)

- [ ] `tick_size` poblado en el Symbol Mapper para los 8 instrumentos
      (6J=0.0000005 · 6E=0.00005 · micros MES/MNQ/M2K/MGC/MJY incluidos) — sin
      esto FIX-D2 es fail-open (no redondea).
- [ ] "Cancel entry after" configurado A MANO en TradersPost (≤60 min) para
      las estrategias con escalera — NTEXECG no lo transmite (hallazgo D-3).
- [ ] `force_flat_time` con margen ANTES de las 17:00 ET (adenda D-3: rechazo
      post-cancel = posición desnuda contra la ventana de mantenimiento CME).
- [ ] Servicio activo, suite verde en NTDEV, alembic al head en el server.

## A. RECEPCIÓN Y SEGURIDAD

1. **Token válido** → 200 + `signal_id`, RawSignal `token_valid=true`.
2. **Token inválido** → 401 + RawSignal en cuarentena + AuditLog
   WEBHOOK_BLOCKED. Repetir >20 veces en 60s desde la misma IP → a partir de
   la 21 NO crecen filas (ni RawSignal ni audit), 401 siempre (FIX-D1).
3. **Duplicado** (misma señal 2× dentro de la ventana) → dedupe NX-10, una
   sola decisión.
4. **Señal rancia** (time viejo) → BLOCK `signal_stale`.
5. **`{{interval}}` en alertas LuxAlgo** (tarea pendiente): con tf presente y
   distinto → BLOCK `interval_mismatch`; sin tf → `tf_not_verified` anota y
   NO bloquea.
6. **Login/2FA**: lockout tras intentos fallidos; TOTP si activado.

## B. DECISIÓN L1–L5 (una prueba por bloqueo, señal sintética)

7. Estrategia PAUSED → entrada BLOCK / exit PASA (exits exentos).
8. Símbolo no mapeado → BLOCK `symbol_not_mapped`.
9. **Heartbeat del bridge viejo/caído** → entrada BLOCK
   `market_data_not_active`; exit PASA. (Apagar NinjaTrader unos minutos y
   mandar señal — prueba clave de "decisiones sobre datos vivos".)
10. Fuera de ventana de sesión → BLOCK `outside_session_hours`.
11. `symbol_busy`: entrada con posición viva del mismo símbolo → BLOCK;
    reversal exento.
12. PortfolioGuard regla 1: dos símbolos del mismo activo → segunda BLOCK.
13. **Kill-switch por capas**: verificar que envío real ocurre SOLO con las 4
    abiertas (env TRADERSPOST_ENABLED, env DRY_RUN, toggle global AND
    estrategia, dry_run merged). Probar al menos: DRY_RUN=true → todo queda
    en DRY_RUN sin HTTP.

## C. DESPACHO A TRADERSPOST (lo que pediste + detalle)

14. **Orden simple recibida**: entrada ES → verificar en TradersPost:
    llega, ticker=MES, `stopPrice`/`limitPrice` ABSOLUTOS y al tick,
    signalPrice presente. Cotejar byte-a-byte contra
    `WebhookDelivery.payload_json`.
15. **Compra escalonada**: entrada con C1 mercado + C2/C3 límite →
    en TradersPost: 3 órdenes, límites a P0∓nivel×ATR exactos al tick, stop
    común. Dejar expirar C2/C3 sin pullback → TradersPost las cancela por
    cancel_after; NX-28 libera la reserva (AuditLog RESERVE_RELEASED).
16. **Pullback real**: esperar (o provocar en paper) un fill de C2 →
    posición promedia, stop común intacto.
17. **Exit con piernas vivas (FIX-D3)**: exit de LuxAlgo con C2/C3 sin
    llenar → verificar en TradersPost que las pendientes SE CANCELAN
    (cancel:true) + AuditLog EXIT_CANCEL_LEGS. LA PRUEBA MÁS IMPORTANTE DE
    LA ETAPA.
18. **Alcance del cancel (adenda D-3)**: con órdenes de prueba de las DOS
    estrategias MES en la cuenta paper, exit de una → ¿cancela solo las
    suyas o todas las del ticker? Documentar el resultado (decide si algún
    día pueden convivir dos estrategias del mismo ticker).
19. **Forced exit / EOD**: posición abierta al `force_flat_time` → cierre
    forzado + FORCED_EXIT con cancel_requested.
20. **Reversal**: señal opuesta con posición viva → cierra primero, y abre
    la opuesta solo si `allow_reversal`.
21. **FX (cuando 6E/6J reactiven o con señal sintética en paper)**: payload
    de 6J con string decimal fijo (sin `e-`), precio al tick de 7 decimales;
    round-trip en Postgres conserva el 7º decimal (D-4/D-4-bis).
22. **Entrega fallida** (apuntar a URL inválida en un perfil de prueba):
    entrada fallida → FLAT; exit fallido → UNKNOWN + L3 bloquea entradas
    (NX-08).

## D. C1 MÓVIL (LX-15) — el cable en el mundo real

23. Aplicar C1>0 vía "Aplicar estas palancas" (gate ámbar + checkbox +
    AuditLog APPLY_LUXY_PALANCAS) → señal → verificar en TradersPost que la
    ENTRADA BASE llega como LÍMITE a P0−depth al tick — jamás a mercado.
24. Con C1>0 y sin pullback → la entrada expira por cancel_after → NX-28
    libera (entrada era limit_only). Sin fantasmas de symbol_busy.
25. Restablecer → Aplicar → config vuelve al estudio, badge "aplicada".

## E. DATOS VIVOS (HOLC / bridge NT)

26. **Ritual diario**: activar NTraderUnifiedBridge → CSVs {SYM}_{tf}
    reescritos completos en Bridge\out → symlink del server los ve →
    heartbeat fresco.
27. Durante la jornada: barras cerradas appendean; feed vivo (bars_*.json)
    avanza; decisiones usan el ATR VIVO del bridge (cotejar `extras.
    atr_value` del payload contra el ATR del bridge en ese minuto).
28. Re-export de sanación cada 4h ocurre (mtime de los CSV).
29. **Integrar lista nueva** (ritual semanal): contención global ≥80%
    (LX-12), outliers de roll excluidos con nombre (LX-13), estudio sin
    banner rojo, digest de flota poblado.

## F. REGISTRO Y RECONCILIACIÓN (la fase de observación propiamente)

30. Por cada trade real de paper: WebhookDelivery (por leg×destino) ↔
    decisión (pipeline_execution_json L1–L5) ↔ ExecutionResult importado
    (match por decisión). Cadena completa reconstruible.
31. **ExecutionResult vs estudio** (el objetivo de la fase): PnL/fills
    reales vs lo que el estudio predijo — deriva real vs prevista, por
    estrategia. Ritual semanal: lista nueva → integrar → estudio →
    semáforo → gate.
32. **6J bajo vigilancia especial**: perfil artefacto (WR 98%) — que los
    ExecutionResult confirmen o desmientan; sin promoción hasta evidencia.
33. Panel Piernas (RA-0v3): cotejar fills tardíos reales vs la curva de
    llegada del estudio — insumo para decidir RA-1/2/3.

## G. OPERACIÓN / RESILIENCIA

34. Restart del servicio a media sesión → posiciones/decisiones intactas
    (DB); lockout/revocación se resetean (en memoria — documentado).
35. Backups de Postgres: verificar rutina + UNA restauración de prueba.
36. Firewall del origin a IPs de Cloudflare (pendiente desde 07-12 — con
    FIX-D1 subió de peso).
37. Disco: crecimiento de raw_signals/audit acotado tras A.2.

## Orden sugerido

Día 1: Pre-requisitos + A + B (sintéticas, sin riesgo).
Día 2: C.14-15 + D (despacho real en paper con tamaño mínimo).
Día 3+: C.16-22 según dé el mercado + E continuo.
Semanal: F (el ritual — es LA fase, no una prueba).
Cuando calme: G.
