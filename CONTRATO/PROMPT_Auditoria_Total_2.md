# PROMPT para Claude Code (Fable) — Auditoría total 2 · código + web

> **Contexto y urgencia:** Fable está disponible hasta el **7 de julio**. Hacemos una **auditoría
> total del código y de la página web** (como la de 2026-07-02) y **ejecutamos** el backlog de mayor
> valor en la ventana. **Estándar y formato:** iguala `CONTRATO/PROMPT_Revision_Arquitectura_NTEXECG.md`
> y el reporte `CONTRATO/REVISION_ARQUITECTURA_2026-07-02_claude-fable-5.md` (hallazgos con evidencia
> `ruta:línea`, backlog priorizado). **No commit/push** (el operador desde NTDEV). **Por lotes,
> deteniéndote entre cada uno** para el visto bueno del arquitecto.

## Rol
Auditor senior + ejecutor. Honestidad estadística/técnica por encima de todo: cada afirmación de uso
o de código muerto va con **evidencia `ruta:línea`** (quién lo llama / no lo llama). No inventes uso.

## FASE A — Auditoría (produce el reporte + backlog)

**A1. Mapa de uso de la WEB, pestaña por pestaña.** Para cada una: qué hace, si se usa (con qué la
llama), y qué código/plantillas/rutas cuelgan de ella. Marca **MANTENER / CONSOLIDAR / DEPRECAR** con
evidencia. Pestañas: Dashboard, Estrategias, Señales, Analítica, **Lab**, Riesgo, Posiciones,
**Activos**, Symbol Mapper, **Templates**, Settings, Audit.
- **Sospechas del operador a confirmar o desmentir con evidencia:**
  - **Templates** (`routes_strategy_templates.py`, la más chica) — ¿tiene uso real o se arrastra?
  - **Dashboard ↔ Analítica** — ¿se solapan? (ambos muestran KPIs/resumen). Propón unificar o mejorar.
  - **Lab** (`routes_lab.py`, visor de filtros) — los filtros NO dieron valor (giramos al Motor de
    Riesgo). La **UI del Lab es candidata a deprecar**, PERO ⚠ **`app/services/lab_metrics.py` y
    `scripts/lab_analyze.py` los REUSA el Motor de Riesgo / la pestaña Riesgo** — quitar la UI **NO**
    es borrar el engine. Deja claro qué se puede quitar sin romper Riesgo.
  - **Activos** (`routes_assets.py`) — confirma que se usa (sizing/redondeo, $/punto).

**A2. Barrido de código MUERTO.** Módulos, funciones, rutas, endpoints, plantillas, imports y scripts
sin uso — incluido el que quede huérfano al deprecar una pestaña. Lista con evidencia.

**A3. Dashboard + Analítica.** Confirma el solape leyendo ambos; recomienda **unificar en un dashboard
rico** o dejar Analítica y hacer del Dashboard un landing útil. Con evidencia de qué comparten.

**A4. Pasada de SEGURIDAD sobre lo NUEVO no auditado** (mismo rigor que la primera auditoría, que cazó
el kill-switch evadible y la "calidad mentira"). Cubre:
- **MR-5 (dispatch en producción):** backstop de precio fijo en L5 (`sl_tp_calculator.py`), TP nominal
  por lado, escalera desde config, `short_size_factor` (`payload_builder.py`, `config_resolver.py`).
  Verifica: **fail-closed preservado** (toda entrada con stop; sin precio → BLOCK), kill-switch por
  capas intacto, `symbol_busy` intacto, y que **nada active dispatch por accidente** (todo opt-in).
- **Motor de Riesgo + pestaña Riesgo** (`nt_riesgo.py`, `mr_sims.py`, `mr_report.py`, `routes_riesgo.py`):
  subprocesos, anti-traversal de `strategy_id`, validación de subida, jobs en background, robustez de
  bordes.

**A5. Ítems ya detectados (inclúyelos en el backlog):**
- **UX "ELEGIDO ninguno":** cuando el walk-forward no valida nada (6E/6J/YM — nativo de bajo riesgo,
  OOS sin pérdidas, pocos trades), la pestaña Riesgo **NO debe verse vacía** — muestra el **motivo**
  ("nada que recomendar: sin catástrofe que atajar / OOS sin pérdidas → no validable / muestra
  insuficiente") + las **top configs aprobadas como referencia** (marcadas "no validadas por OOS").
- **Backstop de FX en ticks/$**, no en "puntos" (el yen da "0 pts" porque su $/punto es enorme).
- **matplotlib en el deploy:** debe instalarse en el server (ya está en el extra `[riesgo]` del
  `pyproject`) — documéntalo (`pip install -e .[riesgo]`) para que un server nuevo tenga heatmaps.

## FASE B — Ejecución (con el tiempo que quede, por lotes)
Tras el visto bueno del reporte/backlog, ejecuta **por lotes, deteniéndote entre cada uno**, en este
orden de prioridad:
- **P0 — Seguridad:** cualquier hallazgo de A4 (fail-closed, kill-switch, traversal, activación
  accidental).
- **P1 — Dolor del Riesgo / bugs:** UX "ELEGIDO ninguno", FX en ticks, y lo que A2/A4 encuentre roto.
- **P2 — Consolidación:** Dashboard + Analítica.
- **P3 — Limpieza:** deprecar UI muerta (Lab, Templates si aplica) + barrer su código muerto.

## Invariantes que NO se rompen (rojo si se tocan)
- **Fail-closed:** entrada sin stop → BLOCK; `passed=True ⇒ sl_price` nunca None.
- **Kill-switch por capas** y `symbol_busy` intactos.
- **NO borrar `app/services/lab_metrics.py` ni `scripts/lab_analyze.py`** — el Motor de Riesgo los
  reusa (quitar la UI del Lab está bien; borrar el engine rompe Riesgo).
- **ConflictLog se conserva** (NX-18 Fase C).

## Protocolo
Evidencia `ruta:línea` en todo; re-verifica contra el código real; tests adversariales rojo→verde en
cada fix; sin regresiones (corre la suite completa); mensajes de commit sugeridos por lote; **no
commit/push**; una fase/lote a la vez esperando el visto bueno.

## Entregable de la Fase A (antes de ejecutar nada)
Un reporte tipo `REVISION_ARQUITECTURA` con: (1) el mapa de uso de pestañas (MANTENER/CONSOLIDAR/
DEPRECAR + evidencia), (2) el inventario de código muerto, (3) la recomendación Dashboard/Analítica,
(4) los hallazgos de seguridad de MR-5/Riesgo, y (5) el **backlog priorizado P0→P3**. **Detente ahí**
para mi revisión antes de tocar código.
