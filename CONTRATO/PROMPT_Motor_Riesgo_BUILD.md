# Directiva de construcción — Motor de Riesgo NTEXECG (arquitecto → Fable)

> **Objetivo final:** el descrito en `CONTRATO/Motor_Riesgo_SPEC.md` (lo escribió Opus 4.8).
> **Objetivo de aceptación:** reproducir `CONTRATO/Riesgo_ES_REFERENCIA.md` (backstop ~$5k/100pts,
> motor-largo, 60/40 vs balanceada, PF OOS ~3.5–4).
> Esta directiva **no reemplaza** el SPEC — lo **ajusta** con dos cosas que el SPEC no sabía: (1)
> reusar el núcleo YA PROBADO del Lab en vez de reconstruirlo, y (2) mejoras del arquitecto.
> **Por fases, detente entre cada una. No commit/push** (lo hace el operador desde NTDEV).

---

## Directiva 1 — REUSAR el núcleo del Lab (no reconstruir)

El SPEC pide `io_export.py`, `atr.py`, `metrics.py`, `robustez.py` desde cero. **No.** Ese núcleo ya
existe, testeado, y reprodujo la calibración del Anexo 16 exacta. Reconstruirlo crea **dos fuentes de
verdad**. Reusa, a nivel de función, estas piezas ya validadas:

| Pieza del SPEC | REUSAR de (ya existe) |
|---|---|
| `io_export.py` (parser LuxAlgo, pareo Entrada/Salida, MAE/MFE/PnL/lado/dur) | `scripts/lab_analyze.py` (parser) |
| `atr.py` (ATR14 Wilder, carga HOLC, cobertura, ATR estimado) | `lab_analyze` (`load_holc`, `_calc_atr`, `HOLC_DIR` env, validación TZ, **`--stitch-db`**) |
| `metrics.py` (PnL, PF, WinRate, MaxDD HWM, por-lado) | `app/services/lab_metrics.py` (`aggregate`, `baseline_from_rows`, `equity_curve`) |
| `robustez.py` (walk-forward in/out) | `lab_metrics` (split in/out 70/30, guardas OOS + `LOW_N_OUT`) |
| re-sim SL/TP con orden de toques | `lab_metrics.resim_rows` (orden intrabar cacheado, ya corregido por B4.0) |
| sizing a riesgo | `lab_metrics.risk_sized_outcomes` (`m = riesgo ÷ Σ wᵢ·(k−Lᵢ)·atr%`) |
| llaveado por estrategia | `scripts/lab_manifest.py` (CSV↔strategy_id, B6) |
| fill-rate de la escalera | la lógica de pullback del Lab (B4.0, `t_pb_touch`) |

El Motor de Riesgo es, entonces, **un CLI + persistencia + estudios NUEVOS que LLAMAN a ese núcleo** —
no un rebuild. Si algo del núcleo necesita generalizarse (p. ej. `$/punto` por instrumento), se
**extrae a una función compartida**, no se duplica.

## Directiva 2 — NO arrastrar lo que ya no se usa

El operador fue explícito: reusar el núcleo **sin arrastrar temas muertos**. El Lab de filtros no dio
valor. **NO** traigas al Motor de Riesgo: el estudio de subscores de calidad (volume/atr/vwap/time),
el de régimen (HMM), el de EMA-bias, ni el botón "mejor config (superviviente de filtro)". El visor
de filtros (camino B) **se queda como está** (no lo tocamos), pero el Motor de Riesgo **no** hereda su
media de filtros — solo el núcleo de cómputo. El foco es **riesgo**, no mejora de señal.

**Descartados que NO se construyen** (mostrar como "descartado – no aporta" si el motor los corre,
nunca recomendar): SL duro ×ATR (net-negativo, mata las ganadoras que aguantan pullback) · filtro de
sesión/hora (no aporta) · **time-stop por duración** (validado 2026-07-04: el hallazgo "los largos
pierden" es real pero **tautológico y no accionable** —las ganadoras resuelven rápido por definición,
y las distribuciones se solapan: el trade más largo del set fue *ganador*—; el net sube pero **PF/DD
quedan planos** = corta ganadores también; **redundante con el backstop** que ya ataja los desastres
largos (#6/#87/#96); y **choca con "que cierre LuxAlgo"**).

## Directiva 3 — Mejoras del arquitecto (sobre el SPEC)

1. **El riesgo se controla con BACKSTOP + escalonada 60/40 — NO con sizing forzado.** (Corrección del
   operador — descarta la idea previa del 1%.) La estrategia está hecha para **tamaño fijo de contrato
   MINI** (1 ES = 10 MES), **sin SL nativo**, y su **edge vive en participar en la mayoría de los
   trades y aceptar las pérdidas normales**. Forzar el tamaño a 1%/3% de la cuenta **rompe el
   propósito**: sizaría para una catástrofe que solo ocurre en ~3/120 trades y estrangularía el edge en
   el 97% restante. Entonces:
   - **Tamaño fijo** (el MINI, repartido en la 60/40) — sin `equity`, sin fórmula de 1%.
   - El **backstop** es el **stop obligatorio** (satisface el fail-closed: toda entrada tiene stop),
     pero **catastrófico/ancho** — toca ~3/120 trades, no bloquea el edge.
   - La **escalonada 60/40** (2 piernas: ~60% + ~40%) mejora el precio promedio de entrada.
   - La recomendación reporta el peor trade **topado por el backstop** en **$ del contrato**, no en %.
   - **El TP también lo decide el estudio, y es NOMINAL — por ENCIMA de donde cierra LuxAlgo.** La idea
     NO es que nuestro TP tome ganancia, sino que **LuxAlgo cierre** (manda su propia salida). El
     estudio mide la **distribución de dónde cierra LuxAlgo sus ganadoras** (excursión al cierre, por
     lado) y fija el TP **por encima** (sobre el p95/p99), por lado, para que **casi nunca dispare antes
     que LuxAlgo** — solo satisface el requisito de TradersPost. El estudio SÍ reporta, informativamente,
     cuánto capturaría un TP-meta (los ~$18.9k en la mesa de la referencia, TP asimétrico 5.5×L/1.0×S —
     eso reproduce el hallazgo de la referencia), pero la **recomendación honra "que cierre LuxAlgo"** →
     TP nominal-arriba. Reusa `resim_rows` + split L/S.
   - **El estudio decide TODA la escalera, no solo las profundidades — "60/40" es solo un ejemplo.**
     El barrido es **conjunto** sobre los TRES grados de libertad: (a) los **puntos ×ATR** de cada
     pierna, (b) la **distribución de contratos** por pierna (60/40, 70/30, 50/50, 2+1, etc. — no se
     fija), y (c) el **número de piernas** (2 o 3). "Jugar con esos números" es literal. **DEBE incluir
     de primera clase las variantes de ALTA PARTICIPACIÓN** (primera pierna a mercado/somera que llena
     casi siempre), además de las profundas. **El estudio corre SIEMPRE a 10 micros = 1 mini**
     (para comparar **1:1 con la línea base de LuxAlgo**, que viene en 1 minicontrato); el barrido varía
     solo la **distribución** de esos 10 (6+4, 7+3, 4+3+3…), las profundidades y el nº de piernas — el
     **total NO cambia**. Por cada config reporta **participación, PF, MaxDD, net, peor trade**; el
     operador elige. Prioridad declarada: **no bloquear la mayoría de los trades** (el edge está en
     participar).
   - **Separación estudio ↔ config:** el estudio **recomienda** (a 10 micros, comparable); la **pestaña
     de config de la estrategia** es donde el operador **aplica y afina** los valores en vivo ("jugar
     con los números" vive ahí, no en el estudio). `recomendacion.json` es el puente entre ambas.
2. **La costura mata el asterisco del HOLC.** El SPEC marca los últimos 18 trades de ES con ATR
   estimado (HOLC al 22-jun). Con `--stitch-db` (cola de Postgres, ya en el server) se cosen hasta hoy
   → el caveat desaparece en vez de solo marcarse. Igual mantén la bandera para cuando no haya costura.
3. **Haircut conservador de slippage/gap.** El SPEC corre "sin comisiones/slippage" — optimista para
   un motor de RIESGO (los fills de límite profundos y el backstop en un gap no son gratis). Añade un
   haircut configurable (comisión + slippage por contrato) y, clave, un **modelo de gap** en el peor
   caso del backstop (un hueco puede atravesarlo): el "peor trade" reportado debe contemplar que el
   backstop **no siempre** ejecuta al precio exacto. Que el número honesto no sea rosa.
4. **`recomendacion.json` alimenta el DISPATCH en vivo.** Estructúralo para el handoff offline→online:
   backstop ($ y pts), escalera (profundidades ×ATR + contratos por pierna), TP largo/corto ×ATR, y la
   fórmula de sizing. Es el contrato entre el motor de estudio y la aplicación en vivo (Directiva 4).
5. **Determinismo reforzado.** El `manifest.json` del `recrear` debe registrar, además del hash del
   master: la **última barra del HOLC + si se usó `--stitch-db`**, la **versión de rejillas**, y el
   **commit git del motor**. Así "recrear" es idéntico bit a bit (mismo código + mismos datos).
6. **Analítica de grado PROFESIONAL** (requisito explícito del operador). El `.md`, el `heatmap.png`
   (matplotlib) y las tablas deben ser de **calidad de publicación**: claros, bien formateados, escalas
   y leyendas correctas, colores con significado (no decorativos), y el **número de confianza OOS
   destacado**. El nivel de la pestaña Analytics que ya validamos — nada de tablas crudas ilegibles. La
   analítica ES el producto; que se vea profesional y se entienda de un vistazo.

## Directiva 4 — La mitad EN VIVO es fase aparte (y es la que protege)

El SPEC (y todo lo de arriba) construye el motor de **ESTUDIO** offline — produce recomendaciones.
**Eso solo no reduce el riesgo.** La reducción real es portar al **dispatch en vivo**, a **tamaño fijo
del contrato** (sin sizing por equity): el **backstop como stop obligatorio** — un stop de **$/puntos
fijos** (100 pts en ES), NO múltiplo-ATR; reemplaza el SL de L5 y, al ser precio fijo, **no depende del
ATR** (más robusto) —, la **escalonada 60/40** (2 piernas), y el **TP nominal por encima del cierre de
LuxAlgo** (por lado — que cierre LuxAlgo; el TP solo satisface el requisito de TradersPost, casi nunca
dispara). **Fail-closed:** si falta ATR para las piernas → cae a entrada única (comportamiento
actual); el backstop de precio fijo siempre se puede calcular, así que **toda entrada tiene stop**.
Esta fase **cambia stops y estructura de entrada en producción** → listón de lote de seguridad: tests
adversariales, backup, paper/demo primero. **Ya NO requiere equity** (la corrección del operador lo
eliminó) — nada la bloquea salvo validar antes los parámetros con el estudio.

**Estructura VALIDADA por el estudio (aceptación cerrada 2026-07-04, ES · 2 muestras).** El motor
reprodujo la referencia al centavo (base $28,175 / PF 1.62 / DD $11,750 / peor −$10,162; motor-largos
exacto 2.60/1.12; backstop ~90 pts; TP-meta L5.5/S1.0 en banda). Al re-correr sobre la 2ª muestra el
**ganador exacto se movió** (híbrido `3+7@1/7×` ↔ balanceada; casi-empate PF OOS 3.8–3.9) → el **split
exacto está dentro del ruido**, y **los exports se DESLIZAN, no se acumulan** (el 07-04 dropeó 12
trades del 06-27). Por tanto MR-5 porta la **ESTRUCTURA ROBUSTA** (estable en ambas muestras), NO el
ganador de una muestra:
- **PORTAR (estable en las 2 muestras):** backstop **90 pts de precio fijo** (≈$450/micro; −37% DD,
  peor ~−$3.2–3.6k, beneficio de riesgo constante aunque el net varíe); **escalera participativa +
  backstop** (PF OOS ~3.8–3.9, participación ~80–86%); **motor de largos** (cortos con tamaño/TP
  reducido); **TP nominal alto en largos** (que cierre LuxAlgo).
- **AFINABLE en la pestaña de config, NO hardcodear:** el split exacto de la escalera (híbrido vs
  balanceada = empate); y el **TP nominal de cortos** (inestable: p99 corto saltó 14.5×→8.0× entre
  muestras; la referencia ya avisó que el TP-corto no generaliza).
- **Principio:** portar la estructura, no el decimal de una muestra; los valores afinables viven en la
  config de la estrategia, no en código. Re-confirmar conforme entren más muestras/datos demo.

**Pendiente antes de MR-5 (robustez del motor, no bloqueante):** `integrar` debe rechazar/avisar el
doble-prefijo de activo (`ES_ES_…`); `calcular` debe imprimir arriba la identidad del master (fecha /
nº trades / sha) y avisar si el folder no corresponde a una integración reciente; y reconciliar en el
reporte el `elegido` vs el `head-to-head`. (Un motor de riesgo no debe correr en silencio sobre datos
que no son — pasó una vez, se blinda.)

## Receta oficial del backstop (multi-instrumento) — YA la automatiza el motor
El backstop se calcula en **$ (unidad universal)**; el "100 pts" de ES **NO se reutiliza** — cada
instrumento tiene el suyo, recalculado sobre su propia lista. Método idéntico para todos (3 pasos que
el motor ejecuta por estrategia):
1. **$/punto del export:** `Tamaño de la posición (valor) ÷ Precio USD` (ES: 331087.5/6621.75 = $50).
   Fiable para cualquier activo; el motor lo infiere y lo **cruza contra la tabla §6 del SPEC** (avisa
   si difieren).
2. **Barrido en $ sobre la MAE** (`Desviación adversa USD`): el nivel que solo capa desastres (~2–3%
   de trades) sin matar ganadoras. ES: ventana net-positiva $4,250–6,000.
3. **A la unidad natural:** `backstop_pts = backstop_$ ÷ ($/pt)` (ES: $5,000/$50 = 100 pts). En
   FX/metales, en **ticks/$**, no "puntos". Salida del motor: `$ óptimo · unidad natural · ×ATR`.

**Matices (consistentes con la lección del decimal):** el óptimo es una **ventana** ($4,250–6,000 en
ES), el punto exacto es sample-sensible ($4,500 en 120 trades, $5,000 en la referencia) → lo robusto
es la ventana/magnitud, se afina dentro. El **×ATR es solo ancla de arranque** (~18–20×ATR), ruidoso
(6–70× por trade); el **$ fijo es la unidad** — y por eso funciona: en $ fijo el stop se aprieta (en
×ATR) en régimen volátil y se afloja en calma.

**Tabla $/punto** (verificar SIEMPRE con el export vía paso 1; micro = full ÷ 10):
ES $50 · RTY $50 · NQ $20 · YM $5 · GC $100 · CL $1,000 · 6E $12.50/pip · **6J: verificar con export**.

> Tie-in con el punto 2 (identidad del master): el `$/punto` derivado del export es parte de la
> procedencia — mostrarlo en la cabecera de `calcular`/reporte y avisar si difiere de la tabla §6.

## Fases sugeridas
- **MR-1 · Ingesta + persistencia** reusando el parser/ATR/stitch del Lab: `integrar`/`estado`,
  estructura `MotorRiesgo/<ACTIVO>_<codigo>/`, snapshots, manifest reforzado. Cuadrar el PnL de la
  línea base contra el export (debe coincidir exacto).
- **MR-2 · Estudios de riesgo** (`sims.py` llamando al núcleo): backstop sweep, escalera por MAE
  (60/40, balanceada, **variantes de alta participación**), **TP nominal por encima del cierre de
  LuxAlgo** (mide la excursión al cierre por lado → TP sobre p95/p99; TP-meta solo informativo; reusa
  `resim_rows` + split L/S), asimetría L/S. Con
  el **gating** automático (supera base + sobrevive OOS; net-negativos marcados "descartado").
  **Reconcilia las tasas de fill de la escalera con el pullback del Lab** (deben coincidir).
- **MR-3 · Robustez + reporte + heatmap**: walk-forward por config, `.md` + `configs.csv` +
  `heatmap.png` + `recomendacion.json` (con el sizing al 1%). **Validación de aceptación: reproducir
  `Riesgo_ES_REFERENCIA.md`.**
- **MR-4 · `recrear` end-to-end** (determinismo).
- **MR-5 (fase aparte, Directiva 4) · Aplicación EN VIVO** (tamaño fijo del contrato): backstop→stop
  obligatorio, escalonada 60/40, TP nominal-arriba (que cierre LuxAlgo). Listón de seguridad. Solo tras
  validar el estudio.

## Protocolo
Honestidad estadística (base siempre, OOS no in-sample, banderas N-bajo / HOLC / no-generaliza→rango);
por estrategia (no trasladar conclusiones de ES); horas en ET; `.venv/bin/python` en server, `py -3` en
NTDEV (los `python`/`python3` pelados son stubs). Tests con respuesta conocida. Reproducir la
referencia ES es el criterio de aceptación. Detente entre fases. No commit/push.
