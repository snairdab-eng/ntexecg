# HANDOFF — Contexto de trabajo NTEXECG · cierre 2026-07-07 (para el 07-08)

> Reemplaza al HANDOFF del 2026-07-07. Principios, arquitectura, roles, flujo
> de deploy e invariantes de los handoffs anteriores siguen vigentes tal cual.
> **LA DEMO ESTÁ ACTIVA** — lo urgente es la sección "EN OBSERVACIÓN".

## 1. Hecho y DESPLEGADO hoy (todo en origin/main, suite 784 verdes)

1. **Listados LO070726 integrados** (los 7, vía UI): ES_CN 121 trades, RTY
   107, GC 42, NQ 67, 6J 64 (⚠ ventana distinta: empieza 03-23, el anterior
   03-16), ES_ConfStrong 38 (ALTA nueva con su propio export), 6E 96 (alta).
   Estudios recalculados ×2 (tras integrar y tras R-obs-2).

2. **Recomendaciones APLICADAS con el botón del Puente** (deriva "aplicada"
   en las 6 con reco): ES_CN 90pts + TPn 11.5/8 + 7+3 @0.25/0.5 + 2760s ·
   RTY 110pts + 29/26 + 7+3 @0.25/0.5 + 3570s · GC 30pts + 35/28.5 +
   3+7 @1/5 · NQ 375pts + 16/15.5 + 3+7 @0.5/1.5 · ES_CS 60pts + 8/8.5
   (escalera 3@1×+7@7× en **design_only** — ver observación) · 6E 0.02pts
   (400 ticks) + 16.5/7.5. **6J sin reco** ("el crudo manda") → bracket
   manual SL 2.5×ATR + TP único 19.5×ATR (p99 de su estudio).

3. **R-obs-2 completo** (3 sub-lotes, commits hasta `fa19eb0`):
   - **a)** Protección de cuenta con **PARTICIPACIÓN 100% OBLIGATORIA**
     (capar pérdidas SIN saltar señales; el combo 5% de ES quedó fuera —
     `proteccion_para_cuenta` es selección pura, aplica sin recalcular).
     **Cuenta por estrategia** (`MotorRiesgo/<clave>/cuenta.json`, fallback
     global). **Rango de operación POR LADO** en listado crudo/ficha/reporte
     (ES: largos p50 4.2h p90 17.6h · cortos p50 3.4h p90 27.8h — el p90 de
     AMBOS lados >> 1h: el topo de TradersPost, ahora cuantificado).
   - **b)** Ficha de protección = espejo de LÍNEAS de la validada (SL,
     Escalera, TP, Lado, ⏱ cancel_after, 📐 Sizing, ✅ "PF in-sample X — sin
     validar OOS"); las 4 cajas de "efecto" RETIRADAS (pedido del operador).
   - **c)** **SL/TP en ×ATR o PUNTOS FIJOS desde la UI** (form con selector
     de modo; SL pts = `backstop_points`, TP nominal por lado editable).
     **TP en puntos fijos NO existe POR DISEÑO** (validado 2026-07-04: TP
     fijo se estrecha en volatilidad y dispararía antes que LuxAlgo).
     **Los 4 perfiles HEREDAN el bracket COMPLETO de la base** (stop fijo +
     TP nominal + niveles); runtime: `resolve_destinations` carga las llaves,
     `recompute_bracket` espeja la precedencia del L5 con guarda P0 y
     fallback FAIL-CLOSED al bracket base; override Avanzado ×ATR de un
     perfil apaga explícitamente lo heredado en ese destino.
   - Tests: `tests/test_robs2.py` (16) + ajustes en test_mr_sims (el test de
     "lado protege" reescrito a la semántica nueva) y test_riesgo_ui.

4. **ACTIVACIÓN DEMO — pre-flight completo y VIVA**:
   - 7/7 estrategias ARMADAS (envío real, dry_run False, webhooks puestos).
   - `cancel_after` fijado A MANO en TradersPost por el operador ✓
     (ES 2760 · RTY 3570 · resto 3600; GC tenía 3060 → 3600).
   - Bridge 16/16 · symbol maps completos (contratos U/Q-2026) · modo NORMAL.
   - **Smoke natural PASÓ**: buy ES_ConfStrong 07-07 08:20 → APPROVE →
     enviado a TradersPost HTTP 200 a 2 destinos (Base + perfil APEXsim) —
     cadena completa OK, dispatch multi-perfil OK. ⚠ Ese trade salió con el
     bracket VIEJO (8×ATR sin TP — fue antes de aplicar).

## 2. EN OBSERVACIÓN (lo urgente)

1. **Checkpoint pendiente: la primera ENTRADA aprobada post-aplicación** debe
   traer el bracket nuevo: `stopLoss.stopPrice` = precio señal ± puntos
   FIJOS exactos (los decimales del stop deben calcar los de la entrada) y
   `takeProfit.limitPrice` = nominal del lado ×ATR. Revisar en
   /ui/signals → detalle → payload. Si algo se ve raro: NO tocar, capturar
   el payload y auditar contra `sl_tp_calculator`.
2. **Escalera de ES_ConfStrong en `design_only`** (decisión diferida): su
   estudio SÍ modela los 3600s (fill con corte: 89.5% @0.25× · 71% @1× ·
   50% @2× · 34% @3× · 10% @4.5×); la pierna 7@7× casi nunca llenaría → en
   la práctica ~3 micros. Recomendación vigente: observar la demo antes de
   armarla (`scripts/set_scale_execution.py`).
3. **6J**: export nuevo con ventana distinta (64 vs 78 trades) — si el
   operador re-exporta con la ventana completa, re-integrar y recalcular.

## 3. Backlog corto

- Deriva del estudio: al recalcular con exports nuevos las recos pueden
  moverse → el badge "difiere" avisa; re-aplicar es 1 clic con diff.
- (Recortable) UI para `short_size_factor` y armado de escalera desde la
  ficha; hoy: script.
- (Si el operador lo pide con datos) TP en puntos fijos exigiría cambio de
  motor + L5 — hoy descartado por diseño, documentado en el form.

## 4. Operativa (recordatorios del entorno)

- Verificación SIEMPRE en NTDEV (`.venv\Scripts\python.exe -m pytest -q`);
  cuelgue flaky conocido → relanzar con `-o faulthandler_timeout=300
  --timeout=600`. El sandbox del asistente: tope 45s por comando y montaje
  que sirve colas truncadas de archivos recién editados.
- `tests/` es paquete: imports `from tests.test_x import ...`.
- Flujo deploy: NTDEV `git add -u` (+ nuevos intencionales) → commit → push;
  server `git pull` + limpiar `__pycache__` + `sudo systemctl restart
  ntexecg`. La sesión web dura 8h.
- Cuenta editable: ahora POR estrategia (el input de la ficha de protección
  guarda con `strategy`; sin ella escribe el global).
