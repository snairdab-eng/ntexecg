# SPEC de Integración por Lotes — Luxy (Riesgo v2) + Portafolio · 2026-07-10

> **Del arquitecto (Fable) para el implementador (Claude Code + Opus).** Convierte el diseño cerrado
> (`SPEC_Luxy_Portafolio_Diseno_2026-07-10.md`) en lotes ejecutables con criterios de aceptación.
> Léase junto con `ANALISIS_Fable_Luxy_Auditoria_2026-07-10.md` (los requisitos R-T de abajo salen de ahí)
> y la receta `LUXY/Luxy_Dashboard_Recreacion.md`.

## 0. Protocolo e invariantes (aplican a TODO lote)

- Lote por lote, **deteniéndose entre cada uno** para visto bueno del arquitecto contra el código real.
- Implementador NO hace commit/push. Verificación en NTDEV: `.venv\Scripts\python.exe -m pytest -q`
  (cuelgue → `-o faulthandler_timeout=300 --timeout=600`). Suite completa verde al cierre de cada lote.
- Invariantes rojos: fail-closed + guarda P0; kill-switch y `symbol_busy` intactos; participación 100%
  por default; Luxy NO despacha; no bifurcar el motor (`mr_sims`/`nt_riesgo`/`lab_metrics` se reusan,
  jamás se duplican); TP en puntos fijos no existe; determinismo (`recrear` bit-a-bit sigue pasando);
  variables Jinja en `<script>` SIEMPRE con `| tojson`; solo paper/demo.

## Requisitos transversales (R-T) — criterios de aceptación citables por lote

- **R-T1** Fills de escalera SIEMPRE con corte temporal: reusar `mr_sims.leg_filled`/cancel_after.
  Prohibido el "alguna vez tocó" del andamio.
- **R-T2** Intrabar SOLO desde el master enriched del motor. La reconstrucción del andamio
  (`entry = hi − mfe`) no se porta.
- **R-T3** Breakeven (palanca NUEVA): convención intrabar **PESIMISTA** — en barra ambigua (toca BE-stop
  y TP/avance en la misma barra) se asume el peor desenlace. Documentada en docstring + test adversarial
  que construye la barra ambigua y verifica el desenlace pesimista. BE se deriva in-sample y se prueba OOS.
- **R-T4** TP techo derivado estilo **p99 de cierres** (robusto); `1.1×MFE_max` solo como referencia
  visual anotada, nunca la recomendación.
- **R-T5** El barrido de SL respeta el **suelo = MAE p95 de las GANADORAS** (que deje respirar) — mismo
  criterio/helper del motor. Sin stops por debajo del suelo en la Tabla B.
- **R-T6** Dirección: se deriva in-sample y se prueba OOS; se muestra como diagnóstico (semántica de la
  gestión por lado del motor con umbrales de muestra). Nunca "mejor neto sobre toda la muestra".
- **R-T7** UNA sola partición de sesiones/zonas horarias: extender `sesion_et` del motor a la
  granularidad de Luxy (Asia/Europa/Apertura/NY media/NY tarde/Cierre) y que estudio, ficha y reporte la
  compartan. Mostrar el rango ET junto al nombre.
- **R-T8** El panel Perfiles muestra payloads generados por el **`payload_builder` REAL** (precios
  absolutos + guarda P0), read-only. `traderspost_payloads`/`exit_payload` del andamio NO se portan.
  `risk_dollar_amount` diferido.
- **R-T9** `usd_por_punto` SIEMPRE del master del motor (cuadre al dólar). El PV 'auto' del andamio no
  se porta.
- **R-T10 (rotulado OOS)** La fila OOS de la Tabla B se rotula **"espejo de robustez — no es la config a
  usar"**; lo aplicable sale SIEMPRE de la fila in-sample probada en OOS. El split es por tiempo
  (viejo=derivar, reciente=probar), compartido con el del motor, con la fecha de corte visible.

---

## LOTE L0 — Preparación (barato, primero)

1. Re-integrar los 7 masters con el HOLC del 07-08 (quita colas "ATR estimado") + recalcular estudios →
   baseline limpio ANTES de construir encima. Vía UI actual (upload/Calcular por estrategia).
2. 🧑‍⚖️ Aclarar con el operador el universo: la SPEC dice "8 estrategias", `LO070726/` tiene 7 CSVs
   (¿falta lista de YM/CL?). Si aparece la 8ª → alta por el flujo actual.
3. Localizar y documentar en el reporte del lote: el split por tiempo vigente del motor
   (`split_in_out`), los helpers a reusar (`leg_filled`, `_eval_proteccion`, `metrics_usd`, suelo MAE,
   p99 de cierres, `sesion_et`, `units.py`, Symbol Mapper) — el mapa de reuso de L2.

**Aceptación:** 7/7 masters con HOLC fresco y estudios recalculados; universo aclarado; mapa de reuso
escrito. Sin código nuevo.

## LOTE L1 — Alta y datos DENTRO de Estrategias

- Alta desde cero (nombre + activo + subir lista) → integra master vía `nt_riesgo` (migra el punto de
  entrada/UI; el motor NO se muda). Reusar `manifest_store` + locks + validación con parser real.
- **Provisión de HOLC**: si el activo no tiene HOLC, el flujo ofrece subirlo (se guarda en
  `NINJATRADER/HOLC/` con validación de formato); sin HOLC → estudio **degradado + aviso + botón** para
  proveerlo.
- Esqueleto del detalle con sub-pestañas `Config · Luxy · Lab · Perfiles` (Luxy/Lab pueden ser
  placeholders) + **selector desplegable** de estrategia.
- Riesgo v1 sigue intacta (transición sin romper nada).

**Aceptación:** e2e alta→lista→master integrado desde Estrategias; HOLC provisión con degradado
honesto; sub-pestañas y selector; R-T9. Tests de cada camino (incluido HOLC inválido).

## LOTE L2 — Estudio Luxy core + Tablas A/B (el corazón — revisión de arquitecto OBLIGATORIA)

- Nuevo estudio sobre el motor (módulo `scripts/mr_luxy.py` o extensión de `mr_sims` — decidir y
  justificar; SIN duplicar primitivas): computa **Crudo / In-sample / OOS** con las palancas (backstop,
  escalera con reparto derivado por frecuencia de pullback f2/f3 + `why_alloc`, TP, lado, **BE nueva**).
- **Tabla A** (3 filas × Neto/PF/MaxDD/Peor/Participación/WR) y **Tabla B** (palancas derivadas
  INDEPENDIENTEMENTE por ventana, 2 filas). Persistencia en `runs/` como el resto de estudios
  (determinismo; `recrear` debe seguir pasando).
- Aplican: **R-T1, R-T2, R-T3, R-T4, R-T5, R-T6, R-T9, R-T10.**
- **Reconciliación obligatoria**: sobre las mismas listas, el crudo y las palancas comparables de Luxy
  deben cuadrar con el estudio v1 (mismo espíritu que Lab↔Motor 9/9). Divergencias solo por palancas
  nuevas (BE) o ventanas distintas — explicadas en el reporte del lote.
- Tests adversariales mínimos: barra ambigua del BE (R-T3); contaminación OOS (alterar trades del OOS
  NO cambia la derivación in-sample); fills con y sin corte (con corte ≤ sin corte); suelo del SL;
  reparto f2/f3 con C1≥1.

**Aceptación:** tablas correctas y reconciliadas; disciplina OOS demostrada por test; cero recompute
pesado en request (job + polling como Calcular).

## LOTE L3 — Dashboard (portar `panel_palancas_multi.html`)

- **Partir del prototipo, jamás rediseñar**: re-skin dark (tokens §4 de la receta), conectar al JSON del
  estudio del motor (contrato §1 de la receta, adaptado), Tablas A/B reactivas, **dos gráficos**
  (in-sample + OOS) con líneas de palanca y columna de códigos fuera del plot, botones
  Recalcular/Restablecer, chips validado·motor / estimación·aprox (la estimación client-side se etiqueta
  SIEMPRE), sesiones/días como diagnóstico con toggle (default sin bloquear — R-T7), time-stop
  descartado (diagnóstico), **fragilidad ⚠** (recon<90% / flip / >3×), equivalencias pts·USD·×ATR
  (units + Symbol Mapper), ventana de operación (reusar RIES-W).
- Aplican: **R-T7, R-T10** y la lección `| tojson`.

**Aceptación:** dashboard vivo dentro de la sub-pestaña Luxy con el estudio real; render sano con
estudio nuevo/viejo/ausente; unidades FX correctas (6E/6J en ticks/USD).

## LOTE L4 — Panel Perfiles

- `scale_alloc` (mayor residuo, C1≥1) y `worst_case_loss` (Σ q·(SL−L)·PV_micro) como **helpers
  compartidos** en `app/services/` (los reusará la regla 3 del Portafolio).
- Tabla de 5 perfiles (principal + 4): micros escalados, peor-caso por operación, caps
  (max_contracts/max_loss_per_trade/max_daily_loss), webhook; si `max_loss_per_trade` se excede, el
  tamaño baja hasta cumplir (lógica del andamio, portada con tests).
- Sub-vista Export: payload por perfil/lado del **builder real** (R-T8), read-only.
- Exhibir el insight: "con SL ancho, una fondeadora con tope $X solo aguanta N micros".

**Aceptación:** perfiles = los de NTEXECG (P4 — sin perfiles propios de Luxy); helpers compartidos con
tests; paridad payload↔builder.

## LOTE L5 — Aplicar supervisado

- Botón en Luxy que reusa el **Puente** (diff + confirmar + AuditLog): la config aplicable sale SIEMPRE
  de la fila **in-sample** (R-T10). Sin acoplamiento: el Config sigue aceptando manual/otra fuente.
- El diff incluye el recordatorio de `cancel_after` manual en TradersPost.

**Aceptación:** aplicar desde Luxy = mismas garantías del Puente (kill-switch intacto, NX-11, audit);
test de que la fila OOS NO es aplicable.

## LOTE L6 — Migrar Lab a Estrategias (tal cual)

- Mover la UI del Lab a la sub-pestaña `Lab` SIN rediseño (su rol se define después). Redirects de
  `/ui/lab` para bookmarks. El **banner de Parte C** (filtros/régimen dormidos + ubicación de la lógica)
  puede entrar aquí si Parte C aún no corrió.

**Aceptación:** Lab funcional en su nueva casa, cero regresiones en sus tests.

## LOTE L7 — Retiro de Riesgo v1 (solo con Luxy TERMINADO)

- Deprecación **no destructiva** (patrón P3): UI fuera, motor/datos/estudios intactos, redirect
  `/ui/riesgo` → detalle Estrategias/Luxy. Checklist previo: todo lo que Riesgo v1 ofrecía existe en
  Luxy (aplicar, deriva, cuenta por estrategia, protección, rango por lado, ventana).

**Aceptación:** paridad funcional demostrada ANTES de retirar; rollback trivial.

---

## PARTE B — Portafolio (independiente; puede correr en PARALELO desde ya)

**P-A (protege la demo — prioridad alta):**
- `PortfolioGuard` como guardarraíl **L3** junto a `symbol_busy`, **fail-closed** (agregado no
  computable → BLOCK con motivo visible).
- **Regla 1 ON**: una posición por ACTIVO (raíz vía Symbol Mapper: MES/ES→ES) entre todas las
  estrategias, sin importar dirección; **NO bloquea las piernas** (evalúa señales de ENTRADA nuevas; las
  legs van en el mismo despacho multi-leg de su señal). Reusar `PositionState`.
- Config global "Portafolio" (interruptores + parámetros) + **vista de exposición en vivo** (posiciones
  por activo, suma de peor-caso con el helper de L4 si ya existe, micros totales).
- Tests adversariales: 2ª estrategia mismo activo → BLOCK con motivo "ES ya tiene posición"; legs no
  bloqueadas; regla apagada = comportamiento idéntico al actual (byte-a-byte en decisiones); fail-closed
  sin PositionState legible.

**P-B:** reglas 2–8 codificadas e **inertes** (grupos: índices ES/NQ/RTY/YM+micros · metales GC · FX
6E/6J · energía CL). Test por regla: apagada no altera nada; encendida hace exactamente lo suyo.

## PARTE C — Limpieza del Config (independiente, según SPEC de diseño)

UI-only para filtros/régimen (servicios se CONSERVAN — el Lab los importa), guardarraíles siempre-on sin
toggle (chequeo intacto), banner en el Lab, columnas DB sin migración destructiva.

## SEC-1 — pendiente del handoff anterior (no olvidar)

`PROMPTS_Seguridad_2026-07-07.md` sigue sin ejecutar. Recomendado antes de que Luxy multiplique la
superficie del panel. Independiente de todo lo anterior.

---

## Orden recomendado

**P-A (paralelo, ya) → L0 → L1 → L2 → L3 → L4 → L5 → L6 → L7**, con P-B y Parte C intercalables en
huecos y **SEC-1 cuanto antes**. Revisión de arquitecto obligatoria al cierre de **L2** (correctitud del
motor) y **P-A** (ruta de riesgo L3); el resto, revisión estándar por reporte.
