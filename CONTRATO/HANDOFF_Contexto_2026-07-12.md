# HANDOFF — Contexto de trabajo NTEXECG · 2026-07-12 (cierre de etapa Fable 5)

> Estado al cierre del 2026-07-12 (noche). Arquitecto/auditor: Fable 5 (acceso termina
> hoy). Implementador: Claude Code en NTDEV. Operador: Sergio (sagayin).
> Compañero: CONTRATO/AUDITORIA_Fable_Cierre_2026-07-12.md (observaciones P0–P3).

## 0. Qué es NTEXECG y la misión

Gateway de señales fail-closed entre LuxAlgo/TradingView (webhooks) y TradersPost.
FastAPI + Postgres + SQLAlchemy async + Jinja2/HTMX/Alpine. **Misión: reducir el
riesgo de trades catastróficos (el edge es de LuxAlgo) SIN saltarse señales
(participación 100%), fail-closed en toda la ruta, SOLO paper/demo — nunca real.**
Invariantes: entrada sin SL y TP válidos → BLOCK; passed⇒sl_price; kill-switch por
capas; exits exentos de L3/L4/L5; la fila OOS jamás es config aplicable (R-T10).

## 1. Roles y protocolo (PRESERVAR TAL CUAL)

- Arquitecto (IA "grande"): SPECs y prompts POR LOTE, revisa reportes. Revisión
  PROFUNDA obligatoria si el lote toca: motor de decisión, seguridad/login, capa de
  datos de mercado, o participación. Lotes JS: smoke de navegador ANTES del sign-off.
- Claude Code = implementador en NTDEV (C:\NTEXECG). Cierra con suite verde,
  `git diff --stat`, SIN commit/push, "LISTO PARA REVISIÓN". Conflicto con un
  invariante §0 → ESCALA, no decide (funcionó 2 veces: BE en L2, guardarraíl TF).
- SOLO el operador commitea/pushea (NTDEV) y despliega. Server `cadmin@ntexecg`
  `~/ntexecg`: `git pull` → [si el lote agrega tablas: `.venv/bin/python -m alembic
  upgrade head`] → `find app -name '__pycache__' -exec rm -rf {} +` →
  `sudo systemctl restart ntexecg`.
- Verificación NTDEV: `.venv\Scripts\python.exe -m pytest -q` (colgado flaky →
  `-o faulthandler_timeout=300 --timeout=600`). Claude jamás teclea credenciales.

## 2. Desplegado y en producción (todo verde, suite 1004 passed / 2 skipped gated)

**Etapa Luxy + Portafolio + Seguridad (L/P/SEC, commits 11732ac…b07b1a8):**
P-A PortfolioGuard L3 regla 1 ON + exposición · L1 alta integrada · L2 estudio Luxy
(Tablas A/B, OOS disciplinado, BE pesimista be_return_minutes/2) · L3 dashboard dark ·
L4 Perfiles + position_sizing compartido · L5 Aplicar vía Puente (in-sample only,
AuditLog APPLY_LUXY_RECO) · L6 Lab embebido · SEC-1/1b/1c (lockout, TOTP, headers/CSP
con 'unsafe-eval', token-once) · P-B reglas 2–8 inertes · Parte C (filtros/régimen sin
UI, guardarraíles siempre-on, gate 1.8 tf_not_verified) · L7a paridad (ventana nativa,
sesiones_et canónico) · L7b retiro de v1 (redirect, helpers vivos, rollback trivial).

**Etapa LX (2026-07-12, revisión del operador → 11 lotes):**
- LX-1: 2FA condicional en login; fix Restablecer (RECO0 inmutable); diagrama único
  100% cronológico con corte in/OOS; tabla reactiva por ventana.
- LX-2: toggles por sesión/día (switches) con zones_off/days_off validados en motor;
  hora ET fuente única (_entry_hour_et).
- LX-3/3b: filas Crudo · Crudo+ (100% simulable con palancas) · OOS espejo, aisladas;
  semáforo de robustez OOS (constantes ROBUSTEZ_*); columna $/trade + retención OOS;
  banner de cobertura.
- LX-4: costura ohlcv_bars POR DEFAULT en flujo web; snapshot HOLC por-clave
  (MotorRiesgo/<clave>/holc_5m.csv — Luxy lo lee con prioridad, R-T2); umbral de
  inconsistencias.
- LX-5: definición ÚNICA de "simulable" (ATR intrabar real); chip "intrabar N/M"
  (adiós placeholder recon); split doble universo; banner blindado.
- LX-6 (crítico): fix cola cosida TZ — _et_naive canónico en lectura; stitch
  fail-closed (solape mínimo + continuidad); tripwire de plausibilidad;
  scripts/audit_ohlcv_tz.py (--fix dry-run / --apply gated). Causa raíz del bug
  PF 184: cola del bridge con TZ distinta + solape vacío que no abortaba.
- LX-7: PF "n/s (N perdedores)" cuando la muestra filtrada tiene <3 perdedores
  (anti-cherry-picking; el PF 182 de largos−viernes−asia era aritmética correcta
  sobre 1 solo perdedor superviviente — espejismo, no bug).
- LX-8: Puente de ventanas — toggles → compilar_ventanas_l2 → preview con %fuera
  (_pct_trades_fuera) + avisos → escribe en el store canónico + AuditLog
  APPLY_LUXY_VENTANAS.
- LX-9: exploración persistente en localStorage (invalidación por estudio_id;
  VLAST jamás se restaura).
- LX-10: snapshot de exploración en SERVER (tabla luxy_exploracion, migración
  a7b8c9d0e1f2, botón Guardar/borrar, precedencia local>server). Desplegado 4b5d691.

## 3. Mapa del sistema (dónde vive qué)

- Detalle de estrategia = casa de todo: Config · Luxy (tabla reactiva + semáforo +
  diagrama + palancas + toggles + ventana + Tabla B + Aplicar + Proponer ventanas +
  Guardar exploración) · Lab (iframe) · Perfiles (sizing/caps/Export).
- Motor: scripts/nt_riesgo.py (integrar/calcular/recrear, manifest, snapshot HOLC),
  mr_sims.py, mr_luxy.py (estudio, evaluate_overrides, tripwire, semáforo),
  sesiones_et.py (zonas + compilar_ventanas_l2), lab_analyze.py (walk B4.0,
  be_return_minutes, load_holc, stitch_from_db, _et_naive, enrich_with_bars).
- Datos: HOLC CSV estático (NINJATRADER/HOLC/{SYM}_5m.csv) + ohlcv_bars Postgres
  (backfill + MarketBarsUpdater del bridge) + costura al integrar/calcular +
  snapshot por-clave. audit_ohlcv_tz para diagnosticar/corregir TZ.
- Guardas: filter_pipeline L1–L5 (+3.5 PortfolioGuard), guardarraíles siempre-on en
  ConfigResolver, dispatch_profiles (recompute_bracket), payload_builder (absolutos+P0).
- Seguridad: core/login_guard, totp, token_once, auth (jti+watermark); CSP en main.

## 4. PENDIENTE INMEDIATO (P0 — primera sesión próxima)

1. **Cerrar la secuencia de datos del server** (nunca confirmada): audit_ohlcv_tz por
   activo → --fix/--apply si procede → reintegrar ES → Calcular estudio → verificar
   PF~2 / participación ~100% / sin banner rojo → **RE-APLICAR la config del ES**
   (la aplicada hoy salió del estudio envenenado pre-LX-6).
2. Smoke pendiente de LX-10 (5 casos del reporte) si no se hizo.
3. Auditoría total (prompt abajo en §7 / ya entregado al operador).

## 5. Backlog ordenado

P1: observación en demo (ExecutionResult vs estudio, ritual de re-estudio semanal con
el semáforo) · reglas Portafolio 2–8 una a una · SEC-2 chico (replay TOTP, bcrypt
dummy) · tareas operador ({{interval}} en alertas LuxAlgo, firewall a IPs Cloudflare,
2FA, TZ del bridge, cobertura MarketBarsUpdater).
P2: lote de limpieza (tabla_a legacy, restos v1, llaves huérfanas, ruta con régimen,
link Lab, recorte de snapshots) · verificación de backups Postgres.
P3: self-host Tailwind / Alpine CSP build (quitar unsafe-inline/eval).

## 6. Lecciones (no reaprender)

- Jinja en <script> SIEMPRE | tojson · SRI del archivo servido real · CSP sin
  'unsafe-eval' mata Alpine estándar (suite no ejecuta JS → smoke navegador).
- Mount del sandbox del arquitecto trunca archivos recién editados (host Read
  autoritativo; git via mount deja index.lock huérfanos — borrar si no corre git).
- DB de barras: jamás .replace(tzinfo=None) a ciegas; solape vacío NO es solape
  verificado; convención canónica ET-naive.
- Un PF sobre una muestra filtrada sin perdedores es un espejismo (LX-7): la
  evidencia para bloquear sesiones/días son las FICHAS, no el PF filtrado.
- "Pesimista PARA LA PALANCA": la ambigüedad siempre en contra de la palanca.
- Los toggles de Luxy son diagnóstico; los ÚNICOS caminos a producción son los
  puentes con diff+confirmación+AuditLog.

## 7. Continuidad

El prompt de la AUDITORÍA TOTAL para Claude Code (modelo Fable 5) se entregó al
operador en el chat de cierre — alcance: seguridad, lógica, código muerto,
consistencia de invariantes, con reporte priorizado P0–P3 y sin cambios de código.
Tras la auditoría: triage con el operador → lotes de fix con el protocolo de siempre.
