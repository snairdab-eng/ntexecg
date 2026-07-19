# AUDITORÍA DE CIERRE — Observaciones del arquitecto (Fable 5) · 2026-07-12

> Observaciones acumuladas de las revisiones profundas de esta etapa (L1–L7b, P-A/P-B,
> SEC-1/1b/1c, Parte C, LX-1–LX-10). No sustituye la auditoría total de código —
> esa corre aparte (PROMPT al final del handoff). Prioridades: P0 = antes de confiar
> en las decisiones · P1 = pronto · P2 = deuda registrada · P3 = opcional.

## 1. SEGURIDAD

**Sano (verificado en código en revisión profunda):**
- Login: bcrypt, lockout+backoff (asyncio, solo prod), TOTP RFC-6238 puro opcional,
  fail-fast de SESSION_SECRET (solo /ui — webhook vive), revocación por watermark.
- Headers/CSP/SRI: SAMEORIGIN + frame-ancestors 'self' (iframe Lab OK), SRI real de
  Alpine/htmx, nosniff, HSTS condicional.
- Webhook LuxAlgo: token hasheado+salt, dedupe, one-time display (token_once TTL 60s,
  un solo read — el token jamás viaja en URLs).
- LX-10: endpoints con require_auth, shape validado, tope 8KB.

**Pendientes (P1, lote chico "SEC-2"):**
- TOTP sin guard de replay (código válido reusable ~90s) → set de últimos counters.
- Timing del username no constante → bcrypt dummy cuando el usuario no coincide.
- CSP con 'unsafe-inline' + 'unsafe-eval' (Tailwind CDN + Alpine estándar) →
  self-host Tailwind y build CSP de Alpine (P2 — exige reescribir plantillas).

**Del operador (sin código):**
- Firewall del origin a IPs de Cloudflare (CF-Connecting-IP se confía sin validar).
- Activar 2FA (scripts/setup_totp) o Cloudflare Access.
- Revocación/lockout son EN MEMORIA: reinicio del servicio = reset (garantía dura
  documentada; aceptable single-admin).

## 2. CAPA DE DATOS (la más crítica — alimenta TODAS las decisiones)

**Blindada esta etapa:** costura DB por default en flujo web; TZ canónica ET-naive en
lectura (`_et_naive` — jamás strip ciego); stitch fail-closed triple (solape mínimo 12,
inconsistencia ≤0.01%, continuidad vs rejilla de sesión); snapshot HOLC por-clave
(Luxy reproducible sin DB); tripwire de plausibilidad (PF>50 o participación<90% con
C1 al mercado → banner rojo, semáforo apagado); definición única de "simulable".

**🔴 P0 — SECUENCIA DEL SERVER SIN CERRAR:** nunca se confirmó la corrida de
`audit_ohlcv_tz` en el server ni su `--fix --apply`, ni la reintegración posterior.
Mientras tanto, **la config APLICADA del ES salió del estudio envenenado** (badge
"deriva: aplicada" del 2026-07-12 pre-fix). Pasos: (1) `python -m scripts.audit_ohlcv_tz
--symbol ES --tf 5m` (y por cada activo del catálogo), (2) `--fix` dry-run → revisar →
`--apply`, (3) reintegrar lista + Calcular estudio → verificar PF~2, participación
~100%, sin banner rojo, (4) **re-aplicar** la config desde el estudio sano (Puente).
- P1 relacionado: confirmar la TZ que entrega el bridge .NET y qué símbolos cubre
  MarketBarsUpdater (los que fallan solo se loguean: `market_bars_fetch_failed`).

## 3. LÓGICA DE NEGOCIO

**Invariantes verificados varias veces (sanos):** fail-closed L1–L5; passed⇒sl_price;
exits exentos L3/L4/L5; guardarraíles siempre-on con gate 1.8 fail-honest
(`tf_not_verified` — falta `{{interval}}` en las alertas LuxAlgo, tarea del operador);
PortfolioGuard regla 1 ON fail-closed, 2–8 codificadas INERTES con UI honesta;
R-T1–R-T10 en Luxy (BE pesimista, OOS espejo jamás aplicable, fills con corte);
puentes supervisados como ÚNICOS caminos de escritura (Aplicar, Proponer ventanas —
diff+confirmación+AuditLog); kill-switch intocable por los puentes.

**Matices conocidos (P2, documentados):**
- El estimador cliente es optimista por diseño (piernas llenan por MAE sin corte de
  tiempo, ~10% arriba del motor) — mitigado por chips estimación/validado y LX-7
  (PF "n/s" con <3 perdedores). Posible mejora: aproximar el corte en el cliente.
- Semáforo/retención con OOS chico (n<10 → "muestra chica") — el usuario debe pesarlo.

## 4. CÓDIGO MUERTO / DEUDA (P2 — un lote de limpieza cuando calme)

- `tabla_a` legacy en el payload del estudio (semántica vieja "palancas sobre toda la
  muestra", UI ya retirada) → retirar o renombrar.
- Restos de Riesgo v1: `riesgo.html` + contexto (retención deliberada para rollback
  de L7b) → retiro definitivo tras periodo de confianza.
- Llaves huérfanas en pipeline_config_json: `filters`, `regime`,
  `guardrails.enforce_*` (ignoradas por el resolver).
- Ruta del detalle aún computa regime_now/ultima_eval/lab_evidencia sin UI.
- Link del Lab "nueva estrategia" → /ui/riesgo (hop de redirect).
- Snapshot HOLC por-clave duplica ~370k barras por estrategia → recortar al rango de
  trades + lookback cuando estorbe en disco.
- quality_scorer/hmm NO son código muerto (el Lab los importa) — conservar.

## 5. RECOMENDACIONES (orden del arquitecto)

1. **P0** — Cerrar la secuencia de datos del server (§2) y re-aplicar config sana al ES.
2. **P0** — Auditoría total con Fable 5 en Claude Code (prompt en el handoff).
3. **P1** — Observación en demo: acumular ExecutionResult y comparar contra el estudio
   (deriva real vs prevista) ANTES de considerar promociones. El semáforo OOS por
   re-estudio periódico (lista nueva cada semana) es el ritual correcto.
4. **P1** — Encender reglas 2–8 del Portafolio una por una, con parámetros razonados.
5. **P1** — SEC-2 chico (replay TOTP + bcrypt dummy) + tareas de operador de §1.
6. **P2** — Lote de limpieza (§4) + backups: verificar rutina de respaldo de Postgres
   (ohlcv_bars + config + audit) — no se auditó en esta etapa.
7. **P3** — Self-host Tailwind / Alpine CSP build.

## 6. LO QUE ESTA ETAPA DEMOSTRÓ (método)

El bug de TZ lo cazó el OPERADOR a ojo porque la tabla era honesta; los lotes LX-6/7
hicieron que el próximo se cace solo (tripwire + PF n/s). La cadena
prompt→implementación→revisión (profunda en motor/seguridad/datos)→deploy→smoke de
navegador (obligatorio en lotes JS) funcionó — consérvala. Los escalamientos de
Claude Code ante conflictos con invariantes (§0) fueron correctos las dos veces:
mantener esa regla.
