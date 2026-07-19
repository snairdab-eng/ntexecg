# Reporte de diseño para Fable — Luxy (Riesgo v2) + Módulo de Riesgo de Portafolio · 2026-07-10

> **Qué es esto:** el conjunto de **acuerdos de diseño** tomados por el operador y el arquitecto (Opus 4.8)
> para que **Fable (Claude Code)** los ejecute. NO es aún el detalle lote-por-lote con tests; es el criterio
> completo del que salen los lotes. Fable debe leerlo entero antes de tocar código.
>
> **⚠ EL DASHBOARD DE LUXY YA ESTÁ CONSTRUIDO — es la BASE de trabajo, NO se reinventa.** El prototipo funcional
> es `C:\NTEXECG\LUXY\panel_palancas_multi.html` (con su gráfica de recorrido, palancas, integrador, sesiones/días,
> time-stop). Fable debe **partir de ese dashboard** y adaptarlo (re-skin dark, conectarlo al motor de NTEXECG,
> añadir las Tablas A/B, la banda/columna OOS y el panel de Perfiles) — **jamás diseñar un dashboard nuevo desde
> cero.** La receta `Luxy_Dashboard_Recreacion.md` describe cada componente tal como ya existe.
>
> **Fuentes de verdad del prototipo/diseño:** `C:\NTEXECG\LUXY\` (`panel_palancas_multi.html` = dashboard
> construido, referencia visual/interacción; `luxy_engine.py`/`build_dashboard_data.py`/`estudio_estrategia.py` =
> **andamio de diseño, NO motor de producción** — la producción usa el motor de NTEXECG; `Luxy_Dashboard_Recreacion.md`
> = receta componente por componente). Contrato vivo (contexto completo del proyecto):
> `CONTRATO/CONTRATO_Trabajo_Vivo_2026-07-07.md` §9.

---

## 0. Misión y protocolo (no negociable)

**Misión NTEXECG:** reducir el riesgo de trades catastróficos (el edge es de LuxAlgo), sin saltar señales por
razones arbitrarias, fail-closed en toda la ruta, **solo paper/demo**.

**Protocolo de trabajo:**
- Fable implementa **por lotes, deteniéndose entre cada uno** para el visto bueno del arquitecto.
- **Fable NO hace commit ni push.** El operador commitea/pushea desde NTDEV; el server hace pull.
- Verificación en NTDEV: `.venv\Scripts\python.exe -m pytest -q` (cuelgue flaky → `-o faulthandler_timeout=300
  --timeout=600`). Tests adversariales rojo→verde por cada fix; suite completa verde al final de cada lote.
- El arquitecto (Opus) verifica cada mini-reporte **contra el código real** antes de que el operador despliegue.
- Marcar claramente lo que es prompt/entrega vs comentario.

**Reglas de reutilización (P7 — críticas):** Luxy y el portafolio se **montan sobre el motor existente**, no
lo bifurcan. Reusar: `scripts/nt_riesgo.py`, `scripts/mr_sims.py`, `app/services/lab_metrics.py`,
`app/services/dispatch_profiles.py`, el Puente (`app/web/routes_riesgo.py`), `app/services/sl_tp_calculator.py`
(L5 + guarda P0), `app/services/filter_pipeline.py` (L3 `symbol_busy`), `app/models/symbol_map.py`,
`app/models/position_state.py`. **No borrar** `lab_metrics.py`/`lab_analyze.py`.

---

# PARTE A — Luxy (Riesgo v2)

## A.1 Identidad y propósito

Luxy es un **analizador de riesgo apoyado en palancas**, que **vive DENTRO de la pestaña Estrategias** (sub-vista
del detalle de una estrategia). Es la **v2 de la pestaña Riesgo**; corre **sobre el motor de NTEXECG** (no un
motor propio); **NO despacha nada**.

Entrega: (1) métricas sobre **toda la muestra** — crudo (línea base) y con las palancas [in-sample]; (2) métricas
**OOS** para decidir. Una vez obtenidos los valores (SL, TP, C1/C2/C3, backstop, dirección…), **otros módulos
(Estrategias)** se hacen cargo de aplicar/despachar. Luxy = decisión; no toca producción.

## A.2 Decisiones de arquitectura (P1–P7)

- **P1 — Datos (Crudo / In-sample / OOS).** Luxy calcula sus propios tres números **desde SUS palancas** (mismas
  primitivas del motor: backstop, escalera, TP, lado). **NO lee el estudio de la pestaña Riesgo v1.** Son tres
  cosas distintas:
  - **Línea base / Crudo** = señal **sin palancas**, toda la muestra (la referencia; no sale de las palancas).
  - **In-sample** = **con palancas**, toda la muestra. Es sobre todo **visual** (ver la nube MFE/MAE y hasta
    dónde llegan SL/TP sin cortar el trade).
  - **OOS** = **con palancas**, sobre la **porción reciente apartada**. Es el **semáforo de sobreajuste**.
  - **OOS honesto (obligatorio):** split **por tiempo** — viejo = in-sample (donde se DERIVAN las palancas),
    reciente = OOS (donde se PRUEBAN, sin mirarlo al elegir). El OOS es un **cálculo paralelo e independiente**,
    no referencia la salida del in-sample (sin contaminación).
- **P2 — Motor intrabar único = el de NTEXECG.** El andamio standalone de Luxy (`build_dashboard_data`/
  `estudio_estrategia`/carpeta HOLC de LUXY) es **solo para diseño**. Producción depende de `mr_sims`/`nt_riesgo`
  + master enriched.
- **P3 — Corte `cancel_after` heredado:** al usar el motor de NTEXECG, el corte ≤3600s ya viene incluido (la
  escalera no debe sobrestimar fills).
- **P4 — Perfiles = los que ya existen** (perfil principal + 4 subperfiles; cada subperfil ajusta nº de
  microcontratos según el riesgo de la fondeadora). Luxy NO crea perfiles propios. El módulo de portafolio (Parte
  B) queda encima/aparte.
- **P5 — Sesiones/días = diagnóstico de Luxy.** Se CONSERVAN (parte esencial del estudio de palancas — p.ej.
  viernes perdedores en ES). Default sin bloquear; el operador togglea para VER el efecto; **nunca auto-despacha**
  ni recorta el default (participación 100%).
- **P6 — Luxy no despacha.** Todo despacho pasa por el dispatch de NTEXECG (payload_builder + L5 + guarda P0 +
  kill-switch + `symbol_busy` + solo paper). `risk_dollar_amount` (sizing por riesgo) se **difiere** como modo
  opcional a auditar aparte.
- **P7 — Reutilizar, no duplicar** (ver §0).

## A.3 UI del estudio Luxy

- **Tabla A — métricas, 3 filas** (Crudo / In-sample / OOS) × columnas: Neto total · PF · Max DD · Peor trade ·
  Participación · Win rate. Reemplaza las teselas "crudo→config" del prototipo. **Reactiva** al mover palancas.
- **Tabla B — palancas de salida, 2 filas** (In-sample / OOS) × columnas: SL · TP · C1 · C2 · C3 · backstop…
  **Cada fila deriva sus palancas INDEPENDIENTEMENTE de su propia ventana** (in-sample-óptimo vs OOS-óptimo →
  coinciden = robusto / divergen = sobreajuste). Reactiva.
- **Dos gráficos de barras** (in-sample y OOS) que se mueven al mover palancas — recorrido MFE/MAE/cierre por
  operación con líneas de palanca (TP techo, breakeven, escalonado C1/C2/C3, SL backstop), tal como el prototipo.
- **Botones:** **Recalcular** (se activa al mover una palanca; recomputa) · **Restablecer** (vuelve a la config
  derivada del estudio). Ya existen en `panel_palancas_multi.html`.
- **Tabla de sesiones:** mostrar el **rango horario en tiempo de New York** junto a cada nombre (Asia, Europa…) —
  el motor ya tiene las horas en `ZONES`. Sesiones/días = diagnóstico (§P5).
- **Equivalencias de unidades:** SL, TP, C1/C2/C3… en **puntos, USD y (de ser posible) X×ATR** — reusar Symbol
  Mapper (`tick_value`/`tick_size`) + ATR del motor. Importante para FX (6E/6J): el "punto" es engañoso, mostrar
  ticks/USD/ATR.
- **Ventana de operación derivada del estudio:** que Luxy la arroje — reusar el criterio de **RIES-W / cobertura**
  ya construido en la v1.
- **Re-skin a tema oscuro NTEXECG** (el prototipo está en claro; portar tokens a dark navy — ver §4 de la receta).

## A.4 Consolidación en la pestaña Estrategias (flujo unificado)

**Objetivo: una sola fuente de verdad por estrategia** (config + lista + estudio + descripción en un lugar; cero
duplicación).

- **Alta de estrategia: desde cero** (nombre + activo + subir listado de operaciones de LuxAlgo → Luxy deriva el
  resto). **Sin plantillas** (Templates ya deprecado en el lote P3).
- **Flujo:** crear estrategia → subir listado (+ **HOLC** si falta, ver abajo) → **integrar el master** (cuadre al
  dólar, sha256, reconstrucción intrabar — **reusar `nt_riesgo`**, el punto de entrada/UI migra a Estrategias, el
  código del motor NO se muda) → corre el estudio Luxy → salen las Tablas A/B.
- **Detalle de estrategia con sub-pestañas:** `Config · Luxy · Lab · Perfiles`.
- **Selector: desplegable** para elegir la estrategia en curso (en vez de todas las listas arriba).
- **Aplicar supervisado:** cuando el operador quiera llevar la reco a producción, un **"aplicar supervisado"**
  que **reusa el Puente** (diff + confirmar + AuditLog). **NO amarra Estrategias a Luxy** — el campo de config
  sigue aceptando valores manuales o de otra fuente; Luxy es un botón más.
- **Provisión de HOLC:** los 8 activos actuales ya tienen HOLC (`C:\NTEXECG\NINJATRADER\HOLC`). Para un activo
  nuevo sin HOLC, NTEXECG debe dar un **mecanismo de provisión** (el operador sube el HOLC como sube la lista;
  NTEXECG lo guarda y lo usa). Sin HOLC → estudio **degradado (sin intrabar) + aviso** + botón para proveerlo.
- **Lab migra también a Estrategias**, como segundo tipo de estudio (Lab = análisis técnico + HMM; Luxy = riesgo
  sobre listas de LuxAlgo). **Reubicar el Lab tal cual (mover su UI), SIN rediseñarlo** (su rol se definirá después).
- **Retiro de Riesgo v1:** se deprecará **solo cuando Luxy esté terminado** (nada se rompe en la transición).

## A.5 Referencias de datos (para la fase de integración/verificación)
- Listas de operaciones: `C:\NTEXECG\ListaDeOperaciones\LO070726` (las 8 estrategias con las que se trabajó).
- HOLC actualizado: `C:\NTEXECG\NINJATRADER\HOLC`.
- Verificación: Luxy sobre el motor de NTEXECG debe **cuadrar** con los estudios que hoy da el motor sobre esas
  mismas listas (reconciliación, como Lab↔Motor).

---

# PARTE B — Módulo de Riesgo de Portafolio (lote INDEPENDIENTE)

## B.1 Idea

NTEXECG controla el riesgo **por trade** (backstop) y **por estrategia** (protección). Falta la capa **agregada**:
cuánto se arriesga a la vez sumando todo lo abierto. El módulo es un **marco de reglas con interruptor** (todas
opt-in/inertes; solo una encendida al nacer), con **su lugar puesto** en NTEXECG. Se ajusta/enciende con el tiempo
operando. **Independiente de Luxy** (no dependen entre sí).

## B.2 Catálogo de reglas (cada una con ON/OFF)

1. **No apilar el mismo activo — 🟢 ENCENDIDA.** Una posición abierta por **activo**, entre todas las estrategias.
   Extiende el `symbol_busy` de L3 de "por símbolo" a "por activo". Precisiones (acordadas):
   - **Sin importar dirección** (largo o corto): una posición por activo, punto.
   - **NO bloquea las piernas de la escalera** de la posición actual (esas construyen la MISMA posición). Solo
     bloquea una **entrada nueva independiente** en un activo que ya tiene posición abierta.
2. **No apilar el mismo grupo/clase — ⚫ apagada.** Si un índice está abierto y sale otro índice, se frena (van en
   cardumen). Mapeo de grupos: **índices = ES/NQ/RTY/YM (+ micros MES/MNQ/M2K/MYM) · metales = GC · FX = 6E/6J ·
   energía = CL**.
3. **Tope de riesgo agregado en $ — ⚫ apagada.** Suma del peor-caso de lo abierto (backstop × tamaño × $/punto) ≤
   tope configurable.
4. **Tope de micros totales — ⚫ apagada.** Suma de microcontratos abiertos ≤ N.
5. **Tope de pérdida diaria del portafolio — ⚫ apagada.** Pérdida realizada del día > X → se para todo (kill por
   pérdida; conecta con el kill-switch existente y con `max_daily_loss` de los perfiles).
6. **Máx nº de posiciones simultáneas — ⚫ apagada.**
7. **Enfriamiento tras pérdida grande — ⚫ apagada.** Tras un trade que pega el backstop, pausa entradas nuevas N
   minutos (evita la "revancha" en caliente).
8. **Sesgo direccional del grupo — ⚫ apagada.** Si el cardumen ya va N en una dirección, no sumar más exposición
   en la misma.

## B.3 Dónde vive

- **Aplicación:** guardarraíl de **nivel L3** (capa de riesgo), junto al `symbol_busy` — **fail-closed** (si no se
  puede computar el agregado, bloquea). Cada regla se consulta solo si está encendida (las apagadas son inertes).
- **Config y vista:** una sección/pestaña **"Portafolio"** (config **global**, no por estrategia): los interruptores
  + parámetros + una **vista en vivo de la exposición** (posiciones por activo, suma de peor-caso, micros totales).
- **Reusa:** `PositionState` (lo abierto), el bracket/backstop (peor-caso), Symbol Mapper (agrupar por activo), la
  cuenta editable (referencia del tope).
- **Bloqueos visibles y con motivo:** cuando frene, decirlo ("portafolio al tope" / "ES ya tiene posición") — es un
  tradeoff de participación consciente.

---

## Invariantes que NO se rompen (rojo si se tocan)
- Fail-closed: entrada sin SL **y** TP válidos → BLOCK; `passed=True ⇒ sl_price` nunca None; guarda P0 intacta.
- Kill-switch por capas y `symbol_busy` intactos; el portafolio EXTIENDE, no reemplaza.
- Participación 100% por default (Luxy sesiones/días no auto-cortan; el portafolio frena solo por riesgo, visible).
- Luxy NO despacha; todo despacho por el pipeline de NTEXECG.
- No bifurcar el motor; reusar `lab_metrics`/`lab_analyze`/`mr_sims`/`nt_riesgo`/`dispatch_profiles`/Puente.
- ConflictLog se conserva. Solo paper/demo.

## Orden sugerido de lotes
**Luxy (dentro de Estrategias):**
1. Alta desde cero + subir lista + integrar master en Estrategias (reusar `nt_riesgo`; provisión HOLC básica).
2. Estudio Luxy + **Tablas A/B** (Crudo/In-sample/OOS + palancas derivadas por ventana, split OOS por tiempo).
3. **Gráficos reactivos** (in-sample + OOS) + sesiones con horas ET + equivalencias pts/USD/ATR + ventana de
   operación derivada + re-skin dark.
4. **Aplicar supervisado** (reusar el Puente).
5. Provisión de HOLC completa (subir/guardar/usar; degradado + aviso si falta).
6. Migrar **Lab** a Estrategias (tal cual, sin rediseño).
7. Retiro de **Riesgo v1** (cuando Luxy esté terminado).

**Portafolio (independiente, se puede hacer YA):**
- P-A: marco `PortfolioGuard` en L3 + config "Portafolio" + **regla 1 (no apilar mismo activo) ENCENDIDA** + vista
  de exposición en vivo. Protege la demo desde ya.
- P-B: reglas 2–8 codificadas pero **apagadas** (inertes), listas para encender.

---

# PARTE C — Limpieza del Config de Estrategias (lote independiente)

**Contexto:** al final de la pestaña Config de una estrategia hay secciones que el pivote volvió residuo. Hay
que distinguir seguridad activa de residuo:

- **Guardarraíles (Anexo 08) — NO tocar el chequeo (es seguridad activa).** `symbol_mismatch` (ticker ≠ activo),
  `interval_mismatch` (interval ≠ timeframe) y staleness disparan bloqueos reales (auditoría: N1=11, N2=9). Única
  limpieza: **quitar el toggle y dejarlos SIEMPRE encendidos** (nunca se querría desactivar un chequeo de símbolo
  equivocado). Conservar el chequeo en L1/L2.
- **Filtros de calidad (L4 Fase 5) + Régimen (L4 Fase 6) — LIMPIEZA SOLO DE UI.** Verificado en código: el
  **Lab DEPENDE** de `quality_scorer` (`_SUBSCORES`, `active_filter_names`) y `hmm_service` (`classify_regime`) —
  `scripts/lab_analyze.py` los importa como "lógica viva" para su análisis. Por eso **NO se arrancan los servicios**.
  - **Quitar:** las secciones de filtros de calidad + régimen del **Config de Estrategias**, y la visibilidad de
    **EST-1/EST-2** en la ficha (score de última señal, "probar ahora", evidencia del Lab por filtro, régimen
    actual). El operador ya no configura filtros en producción.
  - **Conservar:** `app/services/quality_scorer.py` + `app/services/hmm_service.py` (el Lab los reusa) y el L4 del
    pipeline **dormido** (ya apagado por default; inerte). El análisis de filtros/régimen **se queda en el Lab**.
  - Conservar columnas de DB sin migración destructiva (como Templates en P3).
  - **BANNER EN EL LAB (pedido del operador):** una nota informativa visible en el Lab que aclare que **los
    filtros de calidad y el régimen están DORMIDOS en producción** (el pivote los descartó; el edge viene de
    LuxAlgo), que su **lógica viva vive en `app/services/quality_scorer.py` y `app/services/hmm_service.py`** y
    que el Lab los reusa para este análisis. Objetivo: que quede claro el estado y la ubicación para cuando se
    retome el tema a mediano plazo.
- Tests: Config sin las secciones; guardarraíles siempre-on (sin toggle, chequeo intacto); Lab sigue verde
  (usa quality_scorer/hmm); pipeline fail-closed/L5 intactos.

## Confirmado por el operador (2026-07-10) — reporte 100% cerrado
- **Grupos de activos (regla 2 del portafolio): CONFIRMADO** — índices ES/NQ/RTY/YM (+micros) · metales GC ·
  FX 6E/6J · energía CL.
- **Regla 1 del portafolio: CONFIRMADO** — una posición por activo **sin importar dirección** (largo/corto) +
  **NO bloquear las piernas de la escalera** (solo entradas nuevas independientes).
- **Limpieza del Config (Parte C): CONFIRMADO** — UI-only para calidad/régimen (el Lab depende de los servicios),
  guardarraíles siempre-on, + **banner informativo en el Lab** (estado dormido + ubicación de la lógica).
