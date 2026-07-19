# Análisis de Fable — Auditoría post-handoff + revisión profunda de Luxy · 2026-07-10

## PARTE 1 — Auditoría del trabajo de la semana (Opus 4.8 + Claude Code): ✅ VALIDADO

**Verificado contra el repo real (13 commits `6356548..b270806`, +4,635 líneas, 7 archivos de tests nuevos):**
- Los 9 lotes del handoff implementados y desplegados (RIES-W, LAB-1/2/3, DASH-1/2, ACT-1, EST-1/2)
  + 3 herramientas extra (checkpoint #0, respaldo de config con scrub, reconcile Lab↔Motor).
- **Checkpoint #0 CERRADO con evidencia dura**: NQ short y RTY long despacharon el bracket NUEVO a plena
  precisión (stop fijo exacto + TP nominal; falsos positivos del audit corregidos con criterio — exits
  aparte, tolerancia por tick). La demo despacha el bracket del estudio tal cual. Este era EL checkpoint.
- **R1 CERRADO de la forma alineada**: se AMPLIARON las 4 ventanas (no se recortó nada) → 7/7 con
  cobertura 100%. R5 documentado (`Convencion_Export_LuxAlgo.md`); RTY dejado a propósito (más historia).
- Spot-checks de invariantes: guarda P0 intacta, participación-100 intacta, `manifest_store` correcto
  (locks + símbolos patcheables), reconcile 9/9 en server. Suite reportada 848 verdes.
- **PENDIENTE detectADO: SEC-1 (seguridad) NO se ejecutó** — el prompt sigue en
  `PROMPTS_Seguridad_2026-07-07.md` sin commit asociado. Único lote del handoff sin tocar.
- Menor: la SPEC de Luxy habla de "las 8 estrategias" pero `LO070726/` tiene 7 CSVs (¿la 8ª es YM/CL con
  lista pendiente?). Aclarar el universo objetivo.

## PARTE 2 — Revisión profunda de LUXY

### 2.1 El diseño (SPEC + contrato vivo): sólido y fiel a la misión
Las decisiones P1–P7 resuelven exactamente los 3 problemas de correctitud que el propio contrato
identificó (OOS, motor intrabar único, corte cancel_after). La **Tabla B con derivación independiente
por ventana** (in-sample-óptimo vs OOS-óptimo → convergen = robusto) es metodológicamente valiosa: un
test de estabilidad de palancas VISIBLE, complementario al gate. La consolidación en Estrategias con
sub-pestañas y el "aplicar supervisado" reusando el Puente respetan todo lo construido.

### 2.2 Hallazgos técnicos del andamio (`LUXY/*.py`) — lo que NO debe migrar tal cual
El andamio es buen diseño exploratorio, pero al portarlo sobre el motor hay 9 puntos que el spec de
lotes debe fijar como REQUISITOS:

1. **Fills de escalera sin corte temporal** (`tpnl`: `any(a[k]<=-L)` = "alguna vez tocó") — sobrestima.
   Reusar `mr_sims.leg_filled` con cancel_after (P3 ya lo decide; que el lote lo haga explícito).
2. **Reconstrucción intrabar del andamio es frágil** (deriva la ENTRADA desde el MFE: `entry = hi−mfe`);
   el master enriched del motor tiene entradas REALES → usar siempre el enriched (P2 ✓).
3. **Breakeven (BE) es palanca NUEVA** — no existe en `mr_sims`. Portarla implica: (a) agregarla a las
   primitivas del estudio con la MISMA disciplina (derivar in-sample, probar OOS); (b) definir la
   **convención intrabar pesimista** (si en la misma barra se toca BE-retorno y TP/SL, asumir lo peor —
   el andamio asume un orden optimista por barra). Sin esa convención, el BE inflará números.
4. **TP techo del andamio = 1.1×MFE_max** (frágil: lo define UN outlier). El motor usa p99 de cierres
   (robusto). Recomendación: la Tabla B deriva TP estilo p99; 1.1×max puede mostrarse como referencia.
5. **SL por percentiles de |MAE| de TODOS los trades** ([60,75,85,95]) permite stops que cortan
   ganadoras. El motor usa el **suelo = MAE p95 de GANADORAS** ("que deje respirar"). Luxy debe respetar
   ese suelo en su barrido (misma filosofía, mismos helpers).
6. **Dirección por mejor neto sobre toda la muestra** (andamio) = mirar el examen antes de contestarlo.
   En Luxy: derivar in-sample / probar OOS, y tratarla como diagnóstico (la gestión por lado del motor
   con umbrales estructurales es la semántica correcta a heredar).
7. **Sesiones: dos particiones distintas** (ZONES del andamio ≠ `sesion_et` del motor). UNA sola fuente
   de verdad de zonas horarias (propuesta: extender `sesion_et` del motor a la granularidad de Luxy y
   que ambos la usen). Mostrar rango ET junto al nombre (ya decidido).
8. **Payloads del andamio NO se portan**: `traderspost_payloads` usa offsets (`amount`) y `action:add`,
   distinto del `payload_builder` real (precios ABSOLUTOS + guarda P0). Como Luxy no despacha (P6 ✓),
   el panel Perfiles debe mostrar el payload generado por el **builder real** (paridad con producción),
   nunca el del andamio. `use_risk_sizing` diferido ✓.
9. **PV 'auto' del andamio** (mediana de pnl/move) → en producción, heredar `usd_por_punto` del master
   (cuadre al dólar bloqueante del motor — superior).

**Lo portable con alto valor y bajo costo:** `scale_alloc` (reparto por mayor residuo, C1≥1),
`worst_case_loss` (Σ q·(SL−L)·PV_micro — ¡el mismo número que necesita la regla 3 del Portafolio! →
helper COMPARTIDO), el reparto derivado por frecuencia de pullback (f2/f3 → alloc, con su banner
`why_alloc`), la bandera de **fragilidad ⚠** (recon<90% / flip de signo / mejora>3×), y el patrón
"validado·motor vs estimación·aprox" del front (etiquetado honesto del recálculo client-side).

### 2.3 Nota conceptual sobre la Tabla B (para el rotulado de UI)
Derivar palancas EN la ventana OOS es, por definición, "mirar el OOS" — es legítimo SOLO como
diagnóstico de estabilidad (comparar contra la fila in-sample), nunca como config elegible. La UI debe
rotular la fila OOS de Tabla B como "espejo de robustez", y la config que se lleva a Estrategias sale
SIEMPRE de la fila in-sample probada en OOS (la disciplina del gate). Si no se rotula, el operador del
futuro elegirá la fila OOS "porque se ve mejor" y el semáforo deja de servir.

### 2.4 Datos
- **HOLC actualizado al 2026-07-08** ✓ (antes 06-22): al re-integrar masters desaparecen las colas con
  "ATR estimado". Sugerencia: re-integrar+recalcular los 7 cuando arranque Luxy lote 1 (baseline limpio).
- `LO070726/` sin cambios desde el 07-07 (7 CSVs). El prototipo usa SU copia de HOLC (solo diseño) ✓.

## PARTE 3 — Sobre el Portafolio (Parte B) y la Limpieza (Parte C)
- **P-A es ejecutable YA e independiente** (marco L3 + regla 1 ON + vista de exposición). Nota técnica:
  la regla 1 encaja natural en L3 porque las piernas van en el MISMO despacho multi-leg de una señal —
  el guard evalúa señales de entrada nuevas, no legs. `worst_case_loss` compartido con Luxy (regla 3).
- **Parte C bien acotada** (UI-only; servicios quality/hmm se conservan para el Lab; guardarraíles
  siempre-on; banner en el Lab). Consciente del costo hundido de EST-1/2 — decisión ya tomada, OK.

## PARTE 4 — Siguientes pasos propuestos (a discutir)
1. **Escribir el SPEC de integración por lotes de Luxy** (el "próximo paso" del contrato) — los 7 lotes
   del orden sugerido en la SPEC, cada uno con los requisitos técnicos de §2.2 como criterios de
   aceptación + tests. Lo escribe el arquitecto (Fable u Opus) y lo ejecuta Claude Code lote por lote.
2. **En paralelo: P-A del Portafolio** (independiente, protege la demo desde ya).
3. **SEC-1** — el único lote del handoff viejo sin ejecutar; el panel controla el gateway armado.
4. **Parte C** cuando convenga (independiente, bajo riesgo).
5. Aclaraciones del operador: (a) ¿universo 7 u 8 estrategias (falta lista de YM/CL)?; (b) ¿re-integrar
   los 7 masters con el HOLC fresco antes del lote 1 de Luxy?
