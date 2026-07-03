# PROMPT para Claude Code — Módulo Laboratorio (camino B / UI preview en Config)

> Pégalo en Claude Code. Construye el **visor interactivo** del Laboratorio en la app web
> (FastAPI + Jinja2 + HTMX + Alpine.js + Chart.js), que consume la **matriz de features ya
> cacheada por el camino A** y permite *what-if* (filtro/umbral/SL/TP/régimen) con **lift vs
> línea base, in/out-of-sample**, al instante. Es una **feature nueva de UI**, no un arreglo.
> **Por fases**, deteniéndote a esperar mi aprobación entre fases. **No hagas commit/push**
> (yo lo hago desde NTDEV). Referencia de diseño: **Anexo 25 §8.1**.

---

## Rol
Actúa como **ingeniero senior full-stack**. El camino A (`scripts/lab_analyze.py`) ya está
validado en 3 fases sobre los 8 instrumentos y produce `REPORTES/lab_features_<SYM>.json` +
`REPORTES/LAB_RESUMEN_<fecha>.md`. Aquí construyes el **UI que los muestra e interactúa**.
Cambios quirúrgicos, con tests, estética consistente con `analytics.html`.

## Principio duro (léelo antes de escribir una línea)
1. **Es un VISOR read-only.** NO recomputa el backtest pesado en la ruta de una petición web.
   Consume la caché del camino A. Si la caché falta o está vieja → banner claro
   *"Regenera con: `python -m scripts.lab_analyze --all-summary [--stitch-db]`"*, no un spinner
   infinito ni un recompute on-request.
2. **NO aplica nada a producción.** Aplicar filtros/`cancel_after` sigue en los CLI auditados
   (`pullback_timing --apply`, etc.) con backup/audit. El UI solo previsualiza. (Un botón
   "aplicar" es una decisión futura, fuera de alcance ahora.)
3. **offline == UI == producción.** Los números que muestre el UI para una selección deben ser
   **idénticos** a los del reporte offline para esa misma selección. Para evitar dos
   implementaciones que diverjan: **extrae del camino A una función pura de agregación**
   (aplica máscara de filtro sobre la matriz → WR/PF/expectancy/DD in&out, y re-sim SL/TP) y
   **llámala desde AMBOS** (el reporte offline y el endpoint del UI). Una sola fuente de verdad.
4. **Línea base SIEMPRE visible.** Cada panel muestra *base vs selección* (delta). Nunca la
   selección sola.
5. **Guarda contra espejismos.** Igual que el reporte: cuando el **out-of-sample n < 15**,
   marca/atenúa el resultado y muéstralo como no-confiable. El UI no debe poder "seducir" con un
   lift de n=3.
6. **Sustractivo vs cambia-desenlace, separados en la UI.** Filtros (calidad/régimen/EMA/ventana)
   solo incluyen/excluyen → re-agregar. **SL/TP CAMBIAN el desenlace** → re-simular (misma lógica
   que el camino A, con el orden de toques intrabar). Deben vivir en secciones distintas para que
   no se confundan conceptualmente.

## Arquitectura sugerida
- **Ruta/endpoint (read-only):**
  - `GET /ui/lab` (o sub-pestaña dentro de Config, como prefieras que encaje en la nav actual):
    selector de instrumento, tarjeta de línea base, y los paneles interactivos.
  - `GET /ui/lab/data?instrument=ES`: sirve la **matriz de features cacheada** (desde
    `REPORTES/lab_features_ES.json`, read-only) + metadatos (fecha de la caché, cobertura,
    #trades, split in/out). Si no existe → 409/banner con el comando para generarla.
  - `POST /ui/lab/aggregate` (o `GET` con querystring): recibe la **selección** (filtros activos,
    umbrales, k de SL, tp de TP, régimen/EMA elegidos) + instrumento, carga la matriz cacheada y
    devuelve el agregado **llamando a la función pura compartida del camino A**. Read-only, sin
    tocar la DB de producción.
- **Interacción:** Alpine.js mantiene el estado de la selección; al cambiar un control, pide el
  agregado (o lo computa en cliente si prefieres, PERO solo si reusas exactamente la misma lógica
  — ante la duda, un endpoint que llama a la función compartida es más seguro que reimplementar
  la agregación en JS). Chart.js para las visualizaciones, estética `gray-900/gray-800`, clases
  Tailwind core, como `analytics.html`.

## Paneles (Anexo 25 §8.1)
- **Base** (siempre): WR, PF, expectancy, DD, cola p95 MAE, #trades — in / out / total.
- **Filtros de calidad**: toggles de los 4 subscores + slider de umbral {50,60,70,80} → lift vs
  base (ΔWR/ΔPF/Δexp), % conservado, in/out, con la guarda n<15.
- **Régimen** (1h/4h) y **EMA-bias** (1h/4h·20/50): selector → desglose y lift, in/out.
- **SL/TP** (sección aparte, "cambia-desenlace"): sliders k∈{1.5..8}, tp∈{3,4,6}, y SL+TP
  conjunto con el orden de toques intrabar; muestra cómo cambia la curva de equity vs base.
- **Ventana / edge-por-hora**: WR/PF/avg% por hora, marcando buckets de n bajo.
- **Pullback**: fill-rate por nivel ×ATR × desenlace, tiempo al toque, y el **`cancel_after`
  sugerido** — con la nota explícita *"= `entry_reserve_timeout_seconds` (NX-17/NX-28): una sola
  caducidad; fija el MISMO valor en TradersPost → Cancel entry after"*.

## FASES (detente entre cada una)
**Fase B1 — cimientos + paridad:** ruta `/ui/lab`, selector de instrumento, lector de la caché,
tarjeta de línea base, y el endpoint de agregación que llama a la función pura compartida del
camino A. **Criterio de aceptación:** para ES, los números del UI (base + al menos un filtro)
**coinciden exactamente** con `REPORTES/LAB_ES_<fecha>.md`. Test que fija esa paridad.

**Fase B2 — filtros interactivos:** toggles de subscores/umbral, régimen, EMA, con lift vs base,
in/out, guarda n<15, y edge-por-hora. Todo reactivo.

**Fase B3 — cambia-desenlace + pullback:** sliders SL/TP (re-sim con orden intrabar) sobre la
curva de equity, y el panel de pullback con el `cancel_after` sugerido y la nota de "una sola
caducidad".

## Protocolo por fase
Reusa la función de agregación del camino A (no reimplementes métricas); tests (paridad UI↔reporte
con respuesta conocida; guarda n<15; caché ausente → banner); corre en ES y **descríbeme/pega la
verificación de paridad**; diff resumido; y **detente** con los commits sugeridos. **No commit/push.**
Estética consistente con `analytics.html`. Read-only puro: sin escrituras a la DB ni a config de
producción.

## Fuera de alcance (por ahora)
Aplicar filtros/`cancel_after` desde el UI (eso sigue en los CLI auditados). Autenticación nueva.
Recompute del backtest pesado en request. Todo eso puede venir después, con el visor ya validado.
