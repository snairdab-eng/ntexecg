# SEGUIMIENTO PARA OPUS 4.8 — NTEXECG · 2026-07-16 (cierre Fable, 2ª etapa)

> Compañeros: HANDOFF_Contexto_2026-07-15.md (arquitectura de datos, guardias,
> gate, flota) · HANDOFF_2026-07-12 (base Luxy/SEC) · AUDITORIA_Despacho_E2E_2026-07-15.md
> · SPEC_Rearmado_Piernas_2026-07-15.md (reglas R-RA1..9 + anti-solape).
> PROTOCOLO INTACTO: lotes con suite verde + git diff --stat, SIN commit/push
> (el operador commitea/deploya), escalar conflictos con invariantes §0, smoke
> de navegador en lotes JS. Para lotes marcados REVISIÓN PROFUNDA sin Fable:
> Opus incluye en su reporte una sección "AUTO-REVISIÓN" contra los criterios
> del prompt, y el operador hace el smoke antes de commitear. Todo reversible.

## 1. Dónde estamos (2026-07-16)

- Luxy completo y honesto: tabla Crudo/Crudo+/OOS, semáforo (gris con n<10),
  $/trade+retención, gate LX-11 (server-side, frase en rojo), concentrado de
  flota, exploración persistente, panel "Piernas / Re-armado" (RA-0v2 desplegado).
- Datos: NT unificado→share→symlink; contención global (LX-12) y por-trade
  (LX-13); merge policy sin ajustar. Cadena sana de punta a punta.
- Flota: el operador RETIRÓ varias estrategias y está evaluando 6J en paper
  (consciente de su perfil artefacto — WR 98%; vigilar con la demo).
- ACLARADO HOY (no era bug): "Aplicar a la config viva" aplica la fila
  in-sample del ESTUDIO, no las palancas movidas (exploración). El operador
  quiere "aplicar lo que ve" → LOTE LX-15 (abajo), con C1 móvil cableada.

> **ACTUALIZACIÓN 2026-07-16 (arquitecto):** RA-0v3 **APROBADO en revisión
> profunda** (Δnet≤0 jamás recomendado; guard peor/PF domina — RTY verificado;
> n/s conservador sobre n_fills_tardios; change set = 3 archivos; UI con
> fallback ⚪). Sign-off condicionado al smoke del operador (ES 🟢 / GC 🔴,
> consola limpia) antes del commit. La verificación por aislamiento de proceso
> se aceptó SOLO para este lote — NO es precedente: **FIX-FLAKE sube a PRIMERO
> en la cola** (antes de FIX-D2; FX está pausada, D2 no pierde nada). Cola:
> FIX-FLAKE → FIX-D2 → FIX-D3 → FIX-D1 → FIX-D4 → LX-15 → RA-1/2/3.

## 2. EN CURSO — RA-0v3 (Recomendación con juicio) · casi cerrado

Estado reportado por Claude Code: motor (veredicto_rearmado con tolerancias
PEOR 15% / PF 10%, MAX_CICLOS_CAP=8, n/s→OFF), UI (veredicto coloreado), tests
20/20 + 96/96 puros; datos reales confirman el fix (GC no_recomendado MAX=1,
era 57; ES recomendado/duración/8; 6E no_recomendado; RTY recomendado/2h — el
guard de peor-trade recortó "duración" pese a Δnet +11k ✓ misión).
FALTA: corrida verde completa (flake aiosqlite en batches) → reporte §0.
CRITERIOS DE REVISIÓN (del arquitecto, verificar antes de commitear):
veredicto nunca recomienda horizonte con Δnet acumulado negativo; el guard de
peor/PF domina al neto (RTY es el test vivo); n/s conservador; el change set
son SOLO 3 archivos (ra0_study.py, strategy_detail.html, test_ra0_study.py).

## 3. COLA DE LOTES (prompts verificados vigentes al 07-16, en orden)

### 3.0 FIX-FLAKE — ✅ APROBADO 07-16 con enmienda pendiente
Revisión del arquitecto: causa raíz correcta (despertar cross-thread del
ProactorEventLoop; el Selector además ACERCA tests a prod-Linux/epoll), tres
silenciadores descartados con evidencia, criterio verde×2 cumplido, callers
del shim verificados (routes_riesgo._run_motor / routes_lab._run_recalc —
contrato exacto). ENMIENDA antes del commit: gatear _install_subprocess_shim()
a sys.platform=="win32" (la cuarentena tan ancha como la enfermedad) + una
corrida verde. Commits separados: RA-0v3 primero, FIX-FLAKE después.
```
LOTE FIX-FLAKE — Cuarentena del teardown aiosqlite (infra, P1 de protocolo).
El cuelgue: engine.dispose() en el teardown del fixture deja el
_connection_worker_thread de aiosqlite bloqueado; hoy casi determinista en
Windows y mata toda corrida completa. (1) Diagnostica y corrige la causa en el
fixture/engine de tests (dispose con timeout, engine por sesión en vez de por
test, o driver de test alternativo — justifica la elección). (2) Criterio de
cierre ÚNICO: la suite completa pasa verde en UNA corrida, dos veces seguidas,
con -o faulthandler_timeout=300 --timeout=600. (3) Cero cambios en app/ o
scripts/ — solo tests/infra de tests; si el fix legítimo exigiera tocar app/,
ESCALA antes. Protocolo §0.
```

### 3.1 FIX-D2 — ✅ APROBADO 07-16 (revisión profunda)
tp_format.py (round_to_tick HALF_UP sobre ticks + dumps fijo-decimal
byte-idéntico), redondeo ANTES de la guarda P0, content= en el único punto de
salida. Cable verificado: config_resolver ya hila SymbolMap.tick_size (:177).
CONDICIÓN OPERATIVA: verificar tick_size poblado en el Symbol Mapper para los
8 instrumentos (6J=0.0000005, 6E=0.00005, micros incluidos) — el fix es
fail-open sin catálogo; 6J está viva en paper. Suite 1091/7 en UNA corrida.
```
LOTE FIX-D2 — Redondeo al tick + formato decimal en el payload (P1, REVISIÓN
PROFUNDA: toca despacho). (1) sl_tp_calculator/payload_builder: TODO precio de
orden se redondea AL TICK del instrumento (tick_size del catálogo, redondeo al
múltiplo más cercano; documentar la dirección en el borde) — nunca round(x,6)
fijo. (2) Formato en el JSON: decimal fijo con los decimales del tick, jamás
notación científica (cubre también D-5 en extras.atr_value). (3) Tests con
precios reales de 6J (tick 5e-7) y 6E (5e-5): string exacto del payload, tick
impar representado, ES/GC sin regresión byte-a-byte en payloads existentes.
Protocolo §0.
```

### 3.2 FIX-D3 (P2 — antes de RA-2; NOTA NUEVA: coordinar con SPEC re-armado §1
y con LX-15 Parte B — la cancelación explícita servirá a C1-límite también)
```
LOTE FIX-D3 — Cancelación explícita de piernas al cierre (P2, REVISIÓN PROFUNDA;
prerequisito de RA-2 del SPEC de re-armado). (1) Investiga y documenta qué
soporta TradersPost para cancelar órdenes pendientes por webhook. (2) Si lo
soporta: en el flujo de exit (forced_exit y exit normal), tras cerrar la
posición se envían cancelaciones de C2/C3 no llenadas, con registro en
AuditLog; si NO lo soporta: documenta la ventana de exposición residual
(≤ cancel_after restante) como riesgo aceptado visible, y el ExitManagerJob la
vigila (pierna huérfana llenada → alerta + cierre inmediato de ese fill).
Tests adversariales de ambos caminos. Protocolo §0.
```

### 3.2-bis FIX-D3 — ✅ APROBADO 07-16 (revisión profunda)
cancel:true atómico en el exit (PayloadBuilder, un punto; entradas jamás),
audit EXIT_CANCEL_LEGS / FORCED_EXIT+cancel_requested, cubre RA-2. Suite
1099/7. ADENDA del arquitecto en FIX_D3_*.md: (a) rechazo post-cancel =
posición desnuda (verificar force_flat_time con margen vs 17:00 ET; observar
en demo); (b) alcance del cancel con ticker compartido MES×2 — verificar en
demo antes de dos estrategias del mismo ticker vivas.

### 3.3 FIX-D1 (P1 seguridad)
```
LOTE FIX-D1 — Cuarentena con cota para señales no autenticadas (P1, estándar).
El webhook NO persiste RawSignal completo antes del 401: lo rechazado va a
cuarentena con COTA por IP/ventana (constantes nombradas) — se preserva la
traza forense (quién/cuándo/AuditLog WEBHOOK_BLOCKED) y se tapa el flood a DB.
Migrar test_raw_signal_saved_on_invalid_token a la semántica nueva. Protocolo §0.
```

### 3.3-bis FIX-D1 — ✅ APROBADO 07-16
quarantine_guard (ventana deslizante por peer TCP — NO header CF, no
spoofeable), cap 20/60s: dentro → RawSignal+WEBHOOK_BLOCKED; sobre → solo
loguru, 401 siempre. Ventana desliza sobre lo persistido (readmite trazas
cada ventana). Suite 1106/7. Notas: el firewall a IPs Cloudflare (tarea
operador) SUBE de peso (acota llaves + procedencia); poda de buckets viejos
= nota P3 para limpieza. Camino válido intacto.

### 3.4 FIX-D4 (P2, LLEVA ALEMBIC en el deploy)
```
LOTE FIX-D4 — Precisión decimal en el registro (P2, estándar). Numeric(18,6) →
Numeric(20,10) en columnas de precio de decision/posición/execution: el 7º
decimal de 6J no se trunca. Migración no destructiva + test round-trip con
precio real de 6J. Deploy: alembic upgrade head. Protocolo §0.
```

### 3.4-bis FIX-D4 — ✅ APROBADO 07-16 (migración c9d0e1f2a3b4, LLEVA ALEMBIC)
6 columnas de precio decision/posición/execution → Numeric(20,10); tests de
introspección + round-trip 6J. AUDITORÍA E2E 07-15 CERRADA (D-1..D-5).
FIX-D4-bis — ✅ APROBADO 07-16 (migración d0e1f2a3b4c5, LLEVA ALEMBIC).
Investigación concluyente: hoy el payload lee el float EN MEMORIA
(expire_on_commit=False, sin refresh de norm) — no había bug activo; la
migración elimina la fragilidad. Hilo de precisión FX CERRADO:
señal (D4-bis) → registro (D4) → cable (D2). ohlcv/market_data_status = P3.

### 3.5-pre LX-15 backend/cable — ✅ APROBADO 07-16 (revisión profunda)
Cable C1-límite en build_scaled (absoluto, al tick vía D2, fail-honest
ValueError), gate ámbar forzado con C1>0, l1 en reco (read-only), NX-28
heredado ESTRUCTURALMENTE (primary_all_limit se computa de los payloads
reales). Periodo intermedio seguro: cero rutas de escritura de c1_depth_atr
(grep+test+callers). Suite 1122/7. FALTA: lote JS (Parte A + slider C1 +
ruta de escritura) CON SMOKE DEL OPERADOR antes de commit.

### 3.5 LX-15 — Aplicar-lo-que-ves + C1 móvil (pedido HOY por el operador)
```
Ejecuta el LOTE LX-15 — Aplicar-lo-que-ves + C1 móvil (cableada de punta a punta).
Principio del operador: las palancas en pantalla son su cabina de decisión — el
estudio es la base (Restablecer), su criterio ajusta, y LO QUE VE ES LO QUE SE
APLICA. Ninguna palanca puede ser espejismo: o está cableada hasta el despacho,
o no existe en pantalla.

PARTE A — Aplicar lo que ves (evolución del Aplicar):
1. Botón principal "Aplicar estas palancas…": aplica el ESTADO ACTUAL de las
   palancas, habilitado SOLO en "validado · motor" (tras Recalcular o tras
   Restablecer). Aplicar tras Restablecer ≡ aplicar el estudio: un flujo, dos
   casos del operador.
2. El preview muestra el diff config→palancas actuales + la evidencia de ESTAS
   palancas: su fila OOS espejo validada (evaluate_overrides) — y el GATE LX-11
   evalúa sobre ESA robustez (no la del estudio): OOS rojo/n-chico/
   participación<90% → fricción correspondiente. Toggles de sesión/día NO
   viajan; BE sigue sin aplicarse.
3. AuditLog origen "luxy_aplicar_palancas" con palancas + evidencia OOS. El
   botón viejo (fila in-sample) puede retirarse si queda redundante —
   repórtalo; R-T10 sigue: la fila OOS-óptimo JAMÁS es aplicable.
4. Badge deriva: palancas ≠ estudio → "difiere del estudio" (correcto: el
   operador decidió); base restablecida → "aplicada".

PARTE B — C1 móvil, SIN espejismo (REVISIÓN PROFUNDA — toca despacho):
5. ESTUDIO/UI: C1 con slider de profundidad (0 = mercado, actual). Estimador y
   evaluate_overrides la respetan; participación cae honestamente con C1>0
   (verificar que el tripwire c1_market no dispare falso).
6. APLICAR: scale_entry gana profundidad de C1 explícita; C1>0 ⇒
   participación<100% ⇒ mínimo fricción ámbar SIEMPRE en el gate.
7. DESPACHO — el cable: si C1>0, la entrada se despacha como LÍMITE a P0−depth
   (absoluto, al tick), con cancel_after y semántica de reservas NX-28 como
   C2/C3; stop común y TP se anclan igual. Si el despacho de C1-límite NO se
   puede completar en este lote: FAIL-HONEST TOTAL — aplicar C1>0 se BLOQUEA
   con mensaje claro y el slider lo indica; JAMÁS C1>0 que despache a mercado
   en silencio. Nada de palancas de utilería.
8. TEST E2E DEL CABLE: mover C1 → Recalcular → Aplicar (gate) → config con C1
   depth → señal sintética → payload de entrada LÍMITE al precio absoluto
   correcto → o el bloqueo fail-honest del punto 7. + participación honesta;
   gate ámbar forzado; audit origen; badge; suite completa.

Protocolo §0. PARTE A revisión estándar; PARTE B con AUTO-REVISIÓN explícita
contra criterios + smoke del operador antes de commit.
```
NOTA de dependencia: la Parte B (C1-límite al tick) SE BENEFICIA de FIX-D2
(redondeo al tick) — ideal: D2 primero.

> **ESTADO 07-17:** LX-15 CERRADO de punta a punta (backend+JS+3 bugfixes,
> re-smoke del operador OK; guarda permanente de render HTML añadida).
> FILTROS-OFF desplegado y APLICADO (4 estrategias neutralizadas, 7/7
> passthrough). Pruebas E2E en curso (T1 seguridad ✓, FIX-D1 exacto en prod).
> SIGUIENTE: pruebas de despacho T2/T3 + arrancar RA-1.

### 3.6 Después: RA-1/RA-2/RA-3 (SPEC re-armado — solo estrategias con veredicto
"recomendado" del panel Piernas) · FIX-FLAKE (cuarentena del teardown aiosqlite,
ya costó horas) · limpieza P2 (ohlcv_bars, tabla_a, restos v1, llaves huérfanas,
HOLC_DIR a Settings) · SEC-2 · backups Postgres · reglas Portafolio 2–8.

## 4. Pendientes del OPERADOR

6J en paper bajo evaluación consciente (perfil artefacto — que la demo hable);
varias estrategias RETIRED hoy; {{interval}} en alertas LuxAlgo; firewall a IPs
Cloudflare; 2FA; decisión ES-ConfStrong (ámbar).

## 5. Regla de oro para Opus

Cuando un criterio choque con un invariante (§0: fail-closed, participación,
kill-switch, R-T10, "ninguna palanca espejismo"), NO decidas solo: escala al
operador con opciones. Las dos veces que pasó (BE en L2, guardarraíl TF en
Parte C) el sistema salió mejor. Y las observaciones operativas de Sergio se
convierten en invariantes — ya cazó el PF imposible, el solape de órdenes y el
control desconectado antes de que costaran dinero. Escúchalo.
