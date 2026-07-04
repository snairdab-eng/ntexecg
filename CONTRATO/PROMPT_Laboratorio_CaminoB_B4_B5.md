# PROMPT para Claude Code — Visor Laboratorio · Refinamiento (Fases B4, B5, B6)

> Pégalo en Claude Code. Refina el visor `/ui/lab` (camino B) con el feedback del operador tras
> usarlo. **Tres fases, detente entre cada una** a esperar mi visto bueno. **No commit/push**
> (yo desde NTDEV). Candados de siempre: paridad UI↔reporte vía `lab_metrics` (fuente única),
> guarda n<15 visible, el visor **NO toca dispatch/config de producción ni TradersPost** (único
> punto de escritura = generar cachés / subir CSV en B6).

---

## FASE B4 — Confiabilidad + veredicto visual

**B4.0 — (BLOQUEANTE, primero) Reconciliar la detección de toques intrabar contra MFE/MAE real.**
Se detectó una inconsistencia: en ES con TP 6×, el panel muestra **%TP ≈ 46.7% (in)**, pero a nivel
de trade solo **~15.8%** alcanzan 6× de MFE (`mfe_atr ≥ 6`). Es ~3× más — la caminata 5m está
contando **más fills de TP de los que respalda el MFE real de LuxAlgo**, probablemente por un
**desajuste de referencia de ATR** entre el `mfe_atr` cacheado y el umbral del toque 5m (o entrada/
precio de referencia distinta). Mientras esto no cuadre, **los ΔPF del panel SL/TP y los fill% del
pullback no son de fiar.**
- Revisa AMBOS lados: favorable (TP, `mfe`) y adverso (SL/pullback, `mae`).
- Corrige la referencia para que el toque 5m sea consistente con `mfe_atr`/`mae_atr` (misma ATR,
  mismo precio de entrada).
- **Test de consistencia:** `%TP_intrabar(tp) ≈ %(mfe_atr ≥ tp)` y `%SL_intrabar(k) ≈ %(mae_atr ≥ k)`
  dentro de una tolerancia chica, en ES real. Rojo antes del fix, verde después.

**B4.1 — Todo en ET, sin dudas.** Rótulo global fijo en la cabecera del visor: "todos los tiempos en
ET (America/New_York)". La hora ya es ET (TZ +0 validado); esto es solo claridad.

**B4.2 — Panel de DECISIÓN (no solo veredicto).** Feedback central del operador: los reportes
ANTERIORES eran herramientas de decisión porque **integraban el vector completo de métricas E
interpretaban el tradeoff en palabras** ("el PF baja pero el WR se mantiene y el DD mejora → es
riesgo, no calidad"). El visor actual muestra métricas **aisladas** por ficha y perdió ese "qué
significa". Hay que devolverle la **capa de interpretación**. Reemplaza las tablas separadas (línea
base 11 col + "selección vs base" 5 col) por **un panel de decisión** que:
- ponga base → config **lado a lado** en el **vector completo**: `PF`, `WR%`, `expectancy%`,
  `maxDD%`, `net`, `n (kept%)` — con **Δ coloreado** (verde mejor / rojo peor, variables semánticas).
- **lea el tradeoff en una frase, con reglas deterministas** sobre el patrón de signos del Δ
  (esto es lo que más pidió el operador). Mapeo mínimo:
  `PF↓ + WR↑ + DD↓` → "menos ganancia por trade, más consistente y menos riesgo — tradeoff de
  riesgo, no de calidad"; `PF↑ + WR↓` → "gana más por trade pero acierta menos — más volátil";
  `PF↑ + WR↑` → "mejor en todo"; `PF↓ + WR↓` → "peor en todo — descartar". (La lógica del
  clasificador vive en `lab_metrics`, no en JS, para que sea fuente única y testeable.)
- muestre una **barra de calor 1–10** por bloque como resumen visual rápido (según Δ de PF, y
  marcando si el PF out cruza 1.0: sobrevive vs cae).
- resalte el bloque **out** (ahí vive el veredicto honesto), separado de `in`.
- cierre con un **veredicto de una línea** (mejor / peor / tradeoff de riesgo).
- la tabla base completa (p95|MAE|, etc.) pasa a **detalle colapsable** debajo.
- **Lenguaje visual = pestaña Analytics.** El operador nota que Analytics (`analytics.html`:
  tarjetas KPI + Chart.js, layout limpio) comunica **mejor** que el Lab (denso, tablero de tablas).
  Adopta ese lenguaje en el panel de decisión: **tarjetas KPI** para las métricas titulares
  (con la de **riesgo/cola** —maxDD, peor trade— resaltada, que es el punto), Chart.js para la
  curva de equity y la barra de calor, mismos estilos (gray-900/800, tipografía, tokens). Que el
  Lab se vea tan claro como Analytics, no una hoja de cálculo.

**B4.3 — Config DEFAULT recomendada (objetivo = REDUCIR RIESGO, no maximizar ganancia).**
> **Principio rector de NTEXECG:** el sistema nace para **disminuir el riesgo** de LuxAlgo (un mal
> trade sin freno puede comerse media cuenta), NO para aumentar la ganancia. El default recomendado
> se optimiza por **riesgo/beneficio**, no por PF.

El estudio **emite una config default recomendada por estrategia**, y el botón la auto-selecciona.
Objetivo (decisión final del operador): **rentable sin poner en riesgo la cuenta** = maximizar la
ganancia **sujeto a un tope duro de riesgo por trade** (default **1% de la cuenta**) + expectancy
positiva OOS. NO es "SL lo más ajustado posible" — una vez que el 1% protege la cuenta, no hace
falta regalar ganancia. Palancas, en orden:
- **SL ancho catastrófico ANCLADO a la señal** (no recalculado sobre el promedio): topa el desastre
  del trade y rara vez saca a un ganador → **conserva el edge** (rentable).
- **Escalonado SOMERO (0.25–0.75×):** mejora la **entrada promedio** → menor pérdida por perdedor
  contra el SL anclado, sin comprometer todo el tamaño al precio de persecución de LuxAlgo. Piernas
  **profundas prohibidas por default** (promedian hacia abajo en los peores trades = MÁS riesgo).
- **Tamaño por riesgo:** contratos tal que `tamaño × distancia_SL = 1%` de la cuenta → la cuenta
  **nunca en riesgo**, aun si todo llena y pega el stop (SL ancho ⇒ tamaño más chico; coherente).
- **Guarda innegociable:** expectancy **positiva OOS** — reducir riesgo hasta volver la estrategia
  no-rentable derrota el propósito. Validado in/out, ⚠ si `n_out < 15`, elige por OUT nunca in-sample.
- Muestra el default **con su costo**: riesgo por trade (%), peor pérdida topada, y cuánta ganancia
  se cede vs nativo. Lógica del optimizador en `lab_analyze`/`lab_metrics` (fuente única, testeable).

> Requiere **modelar el tamaño por riesgo** (hoy el lab trabaja en % por trade; añadir la dimensión
> de sizing a riesgo fijo 1%). Bajo este criterio la política de ES del Anexo 25 §9.1 (SL 8× ancho +
> escalonado somero) **se sostiene** — solo se le suma el sizing al 1%. Avísame si algún default por
> estrategia contradice lo registrado.

## FASE B5 — Configuración combinada + rangos completos

**B5.1 — Una sola configuración COMBINADA (no fichas descorrelacionadas).** Hoy cada ficha (filtros,
SL/TP, pullback, régimen, EMA) calcula su efecto **aislado contra la base**, pero una config real es
la **combinación** y las perillas interactúan. Introduce **un estado de config único** que aplique
todo junto, en orden:
1. **Sustractivos** (filtros calidad + régimen + EMA) recortan el universo de trades primero.
2. **SL/TP** re-simula sobre **ese subconjunto ya filtrado** (no sobre la base completa).
3. **Piernas/pullback** afectan el precio de entrada.
Todo vía `lab_metrics` (una sola agregación, orden documentado, preserva paridad). La salida central
= **la curva de equity que le gusta al operador**: `base (nativo)` vs `config combinada`, con el corte
in/out marcado, + la barra de calor 1–10 y la **frase de interpretación del tradeoff** (B4.2) del
resultado COMBINADO. Los deltas por perilla se **conservan pero rotulados "efecto aislado de esta
perilla"**, para no confundirlos con el resultado combinado.

**Explicación del escalonado (el "por qué suma").** El operador extraña entender *por qué* las
compras escalonadas aumentan el beneficio. Añade un mini-panel que lo haga explícito: **precio de
entrada base vs entrada promedio ponderada** (con las piernas que llenan), la **contribución neta**
al resultado, y el porqué en una línea — *"las piernas llenan en pullbacks someros a mejor precio,
sobre los buenos trades; las profundas casi no llenan"* (conecta con la tabla de pullback). No es
una métrica aislada más: es la mecánica del escalonado hecha visible dentro del resultado combinado.

**B5.2 — Extender las rejillas para ver los límites.**
- **TP:** la rejilla llega solo a 6× y quedó corto (capa dentro del cuerpo de la distribución).
  Extiéndela a valores altos ({8,10,12,15,20}) o, mejor, un modo **"TP nominal = sobre el MFE p99"**
  que lo calcule por estrategia — el TP es un bracket nominal ancho (TradersPost lo exige), no una
  meta. SL análogo (catastrófico ancho).
- **Pullback:** la tabla de fill-rate × desenlace llega solo a **5×ATR**; extiéndela **hasta 10×**
  (añade 6,7,8,9,10 a `PULLBACK_LEVELS`) para ver dónde tocan fondo el fill-rate y el desenlace.

## FASE B6 — Estudio por estrategia + gestión de datos

**B6.1 — Llavear por ESTRATEGIA, no por activo.** Cada CSV de LuxAlgo es en realidad **una
estrategia**, y un activo corre varias con números distintos. Refactor:
- **Manifest `CSV ↔ strategy_id`** (p. ej. `REPORTES/lab_manifest.json`): lee las estrategias
  existentes (config/DB) y **propón el mapeo**; el operador confirma. Siembra con los 8 actuales
  (cada uno → la estrategia primaria de su símbolo; el instrumento se deriva de la estrategia).
- `lab_analyze` acepta `--strategy <id>` (además de `--instrument` para retrocompat); cachés pasan a
  `lab_features_<strategy_id>.json`; `--all-summary` itera estrategias.
- El **selector del visor** pasa a ser **por estrategia, agrupada por símbolo**. Paridad por estrategia.

**B6.2 — Actualizar lista + recalcular.** Sección de **datos** en el visor: muestra por estrategia el
CSV actual y su **fecha**; permite **subir** uno nuevo (etiquetado con su `strategy_id`); y botón
**"recalcular"** que dispara la regeneración **en segundo plano** (job async; en el server con
`--stitch-db` y `HOLC_DIR`) con progreso, y **refresca la caché** al terminar. Único punto de
escritura del visor; **sin recomputo pesado en el hilo de la petición**; sin tocar dispatch/config
de producción ni TradersPost.

## Protocolo (todas las fases)
Reusa `lab_metrics` (paridad = fuente única, no metas métricas en JS); tests: B4.0 consistencia
intrabar↔MFE/MAE (rojo→verde), paridad tras la config combinada y tras el refactor por estrategia,
B4.3 elige el superviviente OOS (no in-sample), guarda n<15 visible. Corre en ES/GC, enséñame la
verificación, diff resumido, y **detente**. Una fase a la vez. No commit/push.

## Fuera de alcance
Aplicar filtros/config a producción desde el visor (sigue en los CLI auditados); autenticación nueva;
recomputo pesado sincrónico en request.
