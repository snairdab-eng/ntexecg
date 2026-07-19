# CONTRATO DE TRABAJO VIVO — NTEXECG · abierto 2026-07-07

> Documento **vivo**: fija el foco (misión + backlog priorizado) para no perder el hilo.
> Se **amplía** conforme aparezcan hallazgos o desviaciones — ver §8 "Log de cambios".
> Fuentes: `AUDITORIA_Sintesis_Mision_2026-07-07.md`, `HANDOFF_Contexto_2026-07-08.md`,
> `HANDOFF_Contexto_2026-07-07.md`.
> Leyenda de estado: 🔲 pendiente · 🔄 en curso · ✅ hecho · ⚠ en observación · 🧑‍⚖️ decisión del operador.

---

## 1. Misión (la estrella polar — no cambia)

NTEXECG **no** existe para mejorar la señal (el edge es de LuxAlgo). Existe para que **ningún trade
catastrófico se lleve la cuenta**, con palancas de riesgo validadas fuera de muestra (backstop fijo,
escalera, TP nominal, gestión por lado), **sin saltarse señales** (participación 100%), **fail-closed**
en toda la ruta de despacho, y **solo paper/demo — nunca real/live.**

Toda tarea entra aquí solo si sirve a esa misión. Si algo la contradice, se marca como desvío (§4).

## 2. Estado actual (2026-07-07)

- **La demo está VIVA:** 7/7 estrategias armadas (envío real, `dry_run` off, webhooks puestos),
  `cancel_after` fijado a mano en TradersPost, smoke natural OK (señal→L5→payload→TradersPost 200
  multi-perfil).
- **Ruta crítica alineada y verificada:** pipeline L1–L5 fail-closed (entrada sin bracket → BLOCK,
  guarda P0 activa), Motor de Riesgo (gate OOS + corte cancel_after + TP nominal p99 + participación
  100%), Puente Riesgo→Estrategias (única puerta de escritura supervisada), dispatch multi-perfil.
- **Desplegado:** todo en `origin/main` (último hito R-obs `b2d2703`, luego bug botones `493b6d0`,
  Puente y R-obs-2 hasta `fa19eb0`), suite ~784 verdes.

## 3. Backlog priorizado (el foco)

| # | Lote | Qué resuelve | Prioridad | Estado |
|---|---|---|---|---|
| 0 | **Checkpoint demo — primer bracket real** | Confirmar que la 1ª entrada aprobada *post-aplicación* llevó el bracket NUEVO (stop pts fijos exactos + TP nominal del lado) en el payload de TradersPost | 🔴 la más alta (barata, valida todo) | ✅ **CERRADO 2026-07-08** — NQ short y RTY long PASS a plena precisión (stop fijo exacto + TP nominal); audit corregido (exits aparte, tolerancia por tick). |
| 1 | **RIES-W** | Ventanas L2 filtran señales (R1, anti-misión directa): ventana de cobertura desde el estudio + % de trades fuera de la ventana vigente | 🔴 alta (único desvío que toca la misión) | 🔲 |
| 2 | **LAB-1** | Cachés del Lab con datos viejos (07-04) → información inservible para decidir | 🟠 | ✅ desplegado 2026-07-08 + 7 cachés recalculadas (manifest_store compartido, upload→recalc, ficha sin caché, banner stale) |
| 3 | **DASH-1** | Tabla del bridge sana: ATR 1h + heartbeat reales, FX en ticks, micro/padre agrupados | 🟠 | ✅ desplegado 2026-07-08 (804 tests). **Confirmado: el bridge SÍ exporta bars 1h** → EST-1 no es adorno. (Posiciones/entregas-con-bracket/deriva = DASH-2) |
| 4 | **EST-1** | Evidencia L4 en vivo: régimen 1h actual + último score + "probar ahora" (read-only) | 🟠 | ✅ desplegado 2026-07-08 (813 tests). 1h confirmado real; wrappers verificados byte-a-byte (cero cambio de semántica). |
| 5 | **ACT-1** | Activos: limpiar info muerta + conectar con los bloqueos N2 que sus ventanas producen | 🟡 | ✅ desplegado 2026-07-08 (820 tests). Tabla en uso/sin uso, herencia efectiva, bloqueos N2 por ventana, limpieza. Esquema/herencia intactos. |
| 6 | **LAB-2** | Gestión de datos del Lab: descargar CSV/enriched (anti-traversal), eliminar listado (reuse v2-D), link config viva read-only | 🟢 | ✅ desplegado 2026-07-09 (`4f52826`). Anti-traversal doble (resolve_key + _inside) verificado; borrado reusa Riesgo. |
| 7 | **DASH-2** | Fila de vigilancia: posiciones abiertas, últimas entregas con bracket, deriva global cacheada, kill-switch por capas | 🟢 | ✅ desplegado 2026-07-09 (`5dc3859`, 837 tests). Read-only; reusa PositionState + deriva_estudio; deriva cacheada TTL 60s. ⚠ pendiente cosmético: "desde cuándo" usa `updated_at` (última mod.), no `opened_at` de risk_plan_json — decidir label/origen. |
| 8 | **EST-2** | Veredicto del Lab por filtro en la ficha (read-only): evidencia OOS junto a cada checkbox | 🟢 | ✅ desplegado 2026-07-09 (842 tests). Paridad `lab_metrics` (selection_mask+aggregate), mapeo explícito, cero escrituras, stale honesto. |
| 9 | **LAB-3** | Sanidad de números: reconciliación Lab↔Motor + barrido de outputs muertos | 🟢 | ✅ desplegado 2026-07-09 (`fab3ca2`, 848 tests). **Reconcile en server: 9/9 coinciden, 0 difieren.** + fix comentario + 8 ecos JSON muertos quitados. Barrido entregado como reporte. |

Orden global sugerido por Fable: **Checkpoint → RIES-W → LAB-1 → DASH-1 → EST-1 → ACT-1 → LAB-2 →
DASH-2 → EST-2 → LAB-3**. Los prompts de estos lotes ya están escritos en `CONTRATO/PROMPTS_*.md`.

## 4. Desvíos contra la misión (auditoría de síntesis, priorizados)

- **R1 — Ventanas L2 recortan señales** (anti-misión DIRECTA). 9 de 26 bloqueos de la semana son N2
  Temporal. → lote **RIES-W** (implementado y desplegado `5afac92`; la ficha ya mide el % fuera).
  **MAPA COMPLETO 2026-07-07** (% de trades del backtest que la ventana L2 vigente deja fuera):
  RTY **89.7%** · 6E **81.2%** · GC **69.0%** · ES_ConfNormal_TC_TSR **65.3%** · ES_ConfStrong ✓100% ·
  NQ ✓100% · 6J ✓100%. **4 de 7 recortan la mayoría de las señales** (RTY tira ~9 de 10). La ventana
  mínima de cobertura de las 4 es ~24h dom–vie. El filtro de sesión no aporta edge (validado 2026-07-04)
  y las palancas de riesgo ya capan el desastre → lo alineado es **AMPLIAR la ventana, no recortar**.
  **RESUELTO 2026-07-07:** ampliadas las 4 ventanas a **Dom–Vie 00:00–23:59** (RTY, 6E, GC, ES_ConfNormal)
  vía la config de estrategia; verificado que **las 7 estrategias ahora cubren el 100%** de sus trades
  (banner verde en todas). Cambio en vivo — esas 4 ya aprueban señales a toda hora/domingo. Estado: ✅
  R1 cerrado (config). Vigilar en demo que la mayor participación se comporte como el estudio previó.
- **R2 — `cancel_after` NTEXECG↔TradersPost es acuerdo humano sin verificación.** El sistema no lee la
  config de TradersPost. Mitigación: avisos en ficha/diff. Mejora futura: campo manual "confirmado en
  TradersPost el <fecha>" por estrategia. Estado: ⚠ dependencia humana
- **R3 — Lab stale** como fuente de decisiones erróneas (cachés del 07-04). Regla temporal: **cualquier
  lectura del Lab es vieja por defecto** hasta LAB-1. Estado: ⚠
- **R4 — Cuenta $10k vs instrumentos grandes: límite ESTRUCTURAL, no bug.** Ni el mejor combo deja el
  peor trade de ES ≤10% de $10k (peor histórico ~$10k/mini). **Resolución decidida:** se aborda con el
  **Módulo de Riesgo de Portafolio** (§9) o bajando el número de contratos micro. Estado: 🧑‍⚖️→📦 movido
  al workstream especial (§9).
- **R5 — Integridad de exports de LuxAlgo** (eslabón más frágil). Caso 6J: export nuevo con ventana más
  corta (64 vs 78 trades) degrada el estudio sin romper nada visible. → documentar convención de export
  + reforzar el aviso de superconjunto para que un encogimiento de ventana sea imposible de pasar por
  alto. **Nota LAB-3 (2026-07-09):** RTY cubre desde 2025-09-05 y CL/YM desde 2025-08, mientras el resto
  arranca en 2026-03 (Lab y Motor coinciden entre sí, no es deriva). **RESUELTO 2026-07-10:** decisión
  del operador = **dejar RTY como está** (más historia in-sample es un activo; no es la trampa del 6J).
  Convención de export documentada en `CONTRATO/Convencion_Export_LuxAlgo.md` (regla clave: nunca encoger
  la ventana; un export con menos trades = alarma). Estado: ✅ documentado — RTY se deja a propósito.
- **R6 — Menores:** escalera ES_ConfStrong diferida (`design_only`, decidir con fills reales); columnas
  muertas del bridge; override Avanzado por perfil puede degradar el bracket del estudio en un destino
  (documentado — vigilar en demo). Estado: ⚠

## 5. Checkpoints que la demo debe demostrar

1. 🔄 Primera **entrada aprobada post-aplicación** con bracket nuevo en el payload (stop fijo exacto +
   TP nominal del lado). ← **lote #0, en guardia** (aplicaciones registradas OK; falta la entrada real).
   Herramienta: `scripts/audit_bracket_post_apply.py` (read-only, correr en el server tras la 1ª entrada).
2. 🔲 **Escalera:** piernas límite que llenan/cancelan dentro del `cancel_after` — comparar fills reales
   vs la tabla de corte del estudio (primera validación empírica del modelo de fills).
3. 🔲 Un **trade perdedor real capado por el backstop** (la razón de ser del sistema — cuando ocurra,
   auditar el ciclo completo).
4. 🔲 **Cero envíos sin bracket, cero órdenes fuera de paper** (invariante permanente).

## 6. Roles y flujo de trabajo (actualizado 2026-07-07)

- **Opus 4.8 (este chat) = ARQUITECTO.** Escribe los prompts, verifica cada reporte contra el código
  real, da los comandos de deploy. **Nunca hace commit/push.**
- **Claude Code + Opus 4.8 (en NTDEV) = IMPLEMENTADOR.** (Fable queda como reserva: ~6% de uso, resetea
  el 10-jul — solo para emergencias.) Implementa y corre la suite **en NTDEV** (donde los tests sí
  funcionan; el sandbox del arquitecto sirve colas truncadas y tiene tope de 45s).
- **El operador (tú)** hace commit + push desde NTDEV; el server hace pull.
- Convención de prompts: bloques `📋 PROMPT PARA CLAUDE CODE` para pegar; el resto (`💬 Para ti`) es
  comentario del arquitecto.

**Flujo de deploy:**
```
# NTDEV (PowerShell) — dentro de C:\NTEXECG
git add -u ; git add <nuevos intencionales>
git commit -m "..." ; git push
# Server (cadmin@ntexecg) — dentro de ~/ntexecg
git pull
find app -name '__pycache__' -exec rm -rf {} + 2>/dev/null; true
sudo systemctl restart ntexecg
```
Notas: en NTDEV verificar con `.venv\Scripts\python.exe -m pytest -q` (el python global no tiene deps);
cuelgue flaky → `-o faulthandler_timeout=300 --timeout=600`. `tests/` es paquete (`from tests.test_x
import ...`). Sesión web dura 8h. Variables Jinja dentro de `<script>` **siempre** con `| tojson`.

## 7. Invariantes que NO se rompen (rojo si se tocan)

- Fail-closed: entrada sin SL **y** TP válidos → BLOCK; `passed=True ⇒ sl_price` nunca None; guarda P0.
- Kill-switch por capas y `symbol_busy` intactos.
- Participación **100%** (capar pérdidas SIN saltar señales — R-obs-2).
- **NO borrar** `app/services/lab_metrics.py` ni `scripts/lab_analyze.py` (el Motor los reusa).
- **Lab UI se conserva.** `ConflictLog` se conserva. TP en puntos fijos NO existe por diseño.
- El **Puente** jamás toca `mode`/`dry_run`/`traderspost`/`status` al aplicar.
- Solo paper/demo — nunca real/live.

## 9. Workstreams especiales (esfuerzo dedicado, aparte del backlog)

### 9.1 Módulo de Riesgo de Portafolio  📦  (resuelve R4)

> Esfuerzo dedicado y separado del backlog de §3 — se le asigna tiempo propio, NO se mezcla con los
> lotes de observación. Sube el control de riesgo de "por trade / por estrategia" a **nivel cartera**.

Requisitos capturados (2026-07-07, del operador):
- **Tope de riesgo de portafolio:** la **suma del riesgo** de las posiciones/estrategias activas **no
  debe pasar de un tope** (configurable). Nueva entrada que haría exceder el tope → se frena.
- **Un activo, una estrategia a la vez:** si ya hay una estrategia activa del **activo A**, **otra
  estrategia del mismo activo A no puede entrar** (evita apilar riesgo correlacionado en el mismo
  subyacente). Extiende el `symbol_busy` de L3 (hoy por símbolo/posición) a **nivel activo entre
  estrategias**.
- **Palanca de tamaño:** reducir el número de contratos micro como forma directa de bajar el riesgo
  agregado.
- Conecta con lo ya existente: la **cuenta editable por estrategia** ya cuantifica el riesgo por
  estrategia; el módulo **agrega** ese riesgo a través de todas las activas y decide contra el tope.

**DISEÑO (RESUELTO 2026-07-10):** **marco de reglas con interruptor** (todas opt-in/inertes; solo una ON al
nacer), con su lugar puesto en NTEXECG; se ajusta/enciende operando. **Independiente de Luxy.** Reglas:
(1) **no apilar mismo activo — ON** (una posición por activo entre estrategias; sin importar dirección; NO
bloquea las piernas de la escalera, solo entradas nuevas independientes); (2) no apilar grupo/clase — OFF
(índices ES/NQ/RTY/YM · metales GC · FX 6E/6J · energía CL); (3) tope $ agregado — OFF; (4) tope micros
totales — OFF; (5) tope pérdida diaria — OFF; (6) máx posiciones simultáneas — OFF; (7) enfriamiento tras
pérdida grande — OFF; (8) sesgo direccional del grupo — OFF. **Vive en:** guardarraíl L3 (junto a
`symbol_busy`, fail-closed) + config "Portafolio" global (interruptores + params + vista de exposición en
vivo). Reusa PositionState + bracket/backstop + Symbol Mapper + cuenta editable.

Estado: 📦 **diseño cerrado → lote independiente listo para ejecutar** (P-A: marco L3 + regla 1 ON + vista;
P-B: reglas 2–8 inertes). Documentado en `SPEC_Luxy_Portafolio_Diseno_2026-07-10.md`.

### 9.2 Luxy — Riesgo v2 (dashboard de estudio por estrategia)  🧪  (esfuerzo aparte)

> Desarrollo hecho con Opus 4.8 en otro chat, dejado en `C:\NTEXECG\LUXY\` (`luxy_engine.py`,
> `build_dashboard_data.py`, `estudio_estrategia.py`, `panel_palancas_multi.html`,
> `Luxy_Dashboard_Recreacion.md`). Es la **v2 de la pestaña Riesgo**. Revisado a fondo 2026-07-09.

**Qué aporta:** dashboard de riesgo por estrategia mucho más rico — palancas derivadas de datos
(in-sample, 100% de trades, SIN OOS), **reconstrucción intrabar visible** (FAV/ADV por barra), gráfica
interactiva MFE/MAE/cierre con líneas de palanca, cascada "integrador" (crudo→+SL→+escalonado→+TP→+BE→
+dirección con Δ neto **y drawdown**), y — lo nuevo — **panel de Perfiles** (cuenta propia + fondeadoras,
re-escala tamaño + caps por cuenta + webhook + payload TradersPost por perfil). Es la evolución de la
mitad "Protección de cuenta" del Riesgo v1.

**7 puntos a validar juntos ANTES de integrar (sesión de decisiones de arquitectura):**
1. **OOS vs in-sample** — Luxy es 100% in-sample sin OOS. ¿Reemplaza solo la superficie de protección/
   exploración y el número que DESPACHA sigue del gate OOS? ¿O la reco in-sample de Luxy dispara
   (soltando el gate)? Define todo lo demás. **(correctitud)**
2. **Motor intrabar único** — Luxy trae su reconstrucción intrabar; NTEXECG ya tiene la suya (B4.0 del
   Lab, `mr_sims`, master enriched). No puede haber dos motores con fills distintos → reconciliar (montar
   Luxy sobre el motor de NTEXECG). **(correctitud)**
3. **Corte `cancel_after`** — el escalonado de Luxy asume fill si el precio tocó el nivel en cualquier
   barra; falta el corte ≤3600s (un pullback tras 1h NO llena). Hoy Luxy SOBRESTIMA fills. Meterle el
   corte o sus números de escalera son optimistas. **(correctitud)**
4. **Perfiles ↔ Portafolio (R4, §9.1)** — los caps por-cuenta de Luxy son complementarios al tope
   por-portafolio de R4. Los perfiles de Luxy podrían ser donde vivan los caps de cuenta.
   _(nota menores: DASH-2 `updated_at`→`opened_at` ✅ desplegado `b270806`; "Live" rotulado solo-paper ✅.)_
5. **Sesiones/días ↔ R1** — el bloqueo por sesión de Luxy debe quedarse diagnóstico (default off); no
   reintroducir corte de señales tras haber resuelto R1 (participación 100%).
6. **Despacho fail-closed** — payloads de Luxy en puntos (offset) vs precio absoluto con guarda P0 de
   NTEXECG; todo debe pasar por el dispatch (fail-closed, P0, kill-switch, symbol_busy, solo paper). El
   sizing `risk_dollar_amount` sería un modo de despacho nuevo a auditar.
7. **Reutilizar, no duplicar** — portar la UI de Luxy SOBRE `mr_sims`/`nt_riesgo`/`lab_metrics`/
   `dispatch_profiles` existentes, no correr el motor standalone de Luxy en paralelo.

**DECISIONES (sesión 2026-07-10):**
- **P1 (OOS vs in-sample) → RESUELTO: opción C + refinamiento + corrección 2026-07-10.** Luxy **calcula
  sus propios Crudo / In-sample / OOS desde SUS palancas** (mismas primitivas del motor: backstop, escalera,
  TP, lado). **NO lee el estudio de la pestaña Riesgo v1** — lo de Riesgo se queda en Riesgo; Luxy es
  autocontenido (es la **v2** de Riesgo; v1 sigue viva durante la construcción, v2 la sucede). Los tres
  números son distintos: **Línea base/Crudo** = señal SIN palancas (la referencia, no sale de las palancas);
  **In-sample** = CON palancas sobre toda la muestra; **OOS** = CON palancas sobre la porción reservada
  (semáforo de sobreajuste). **DISCIPLINA OOS (a confirmar/implementar):** las palancas se **derivan en el
  in-sample y se prueban en el OOS** (split por tiempo: viejo=fit, reciente=test) — sin mirar el OOS al
  elegirlas, o el OOS miente. Misma disciplina que el gate actual.
- **P2 (motor intrabar único) → RESUELTO: el motor de NTEXECG.** El standalone de Luxy
  (`build_dashboard_data`/`estudio_estrategia` + carpeta HOLC) es **solo andamio de diseño**; la versión
  de producción depende por completo de `mr_sims`/`nt_riesgo`/`lab_metrics` + master enriched.
- **P3 (corte cancel_after) → RESUELTO por herencia:** al usar el motor de NTEXECG, el corte ≤3600s ya
  viene incluido.
- **P7 (reutilizar, no duplicar) → RESUELTO:** la UI de Luxy se monta SOBRE el motor de NTEXECG.
- **P4 (perfiles) → RESUELTO: se queda como hoy.** Sistema existente de NTEXECG — perfil principal + 4
  subperfiles (cada subperfil ajusta nº de microcontratos según el riesgo de la fondeadora). Luxy NO crea
  perfiles propios. R4 (portafolio agregado) sigue aparte, encima.
- **P5 (sesiones/días) → RESUELTO: Luxy los CONSERVA para su estudio de palancas** (parte esencial del
  analizador — p.ej. viernes perdedores en ES). Son diagnóstico/exploración: nunca auto-despachan ni
  recortan el default (participación 100%); el operador togglea para VER el efecto, no para aplicarlo.
- **P6 (despacho) → RESUELTO: Luxy NO despacha.** Como hasta hoy.

**PROPÓSITO DE LUXY (definido por el operador 2026-07-10):** un **analizador apoyado en palancas**. Entrega
(1) métricas sobre TODA la muestra en crudo (línea base) y con las palancas [in-sample], y (2) métricas
**OOS** para decidir. Una vez obtenidos los valores (SL, TP, C1/C2/C3, backstop, dirección…), los otros
módulos (**Estrategias**) se hacen cargo de aplicar y despachar. Luxy = decisión; no toca producción.

**Hand-off a Estrategias (P2 de la sesión, resuelto 2026-07-10):** Luxy da un **resumen del estudio**; el
operador lo configura **a mano** en Estrategias. **Sin botón "llevar a Estrategias", sin acoplamiento** —
Estrategias recibe parámetros de cualquier fuente (Luxy es una más). Se descarta enganchar Luxy al Puente.

**Datos HOLC actualizados para integración:** `C:\NTEXECG\NINJATRADER\HOLC` (para cuando conectemos el
dashboard al motor real; hoy el prototipo usa la carpeta HOLC de diseño de LUXY).

**Prototipo original:** `LUXY/panel_palancas_multi.html` (autocontenido, dato embebido de 6 estrategias).
Se itera editando el archivo directamente (re-skin dark + banda OOS + panel Perfiles), sin tocar el blob
de datos.

**DISEÑO UI (definido por el operador 2026-07-10):**
- **Tabla A — métricas, 3 filas:** Crudo / In-sample / OOS; columnas = Neto total · PF · Max DD · Peor
  trade · Participación · Win rate. Reemplaza las teselas "crudo→config". Reactiva al mover palancas.
- **Tabla B — palancas de salida, 2 filas:** In-sample / OOS; columnas = SL · TP · C1 · C2 · C3 · backstop…
  **Cada fila es la palanca DERIVADA INDEPENDIENTEMENTE de su propia ventana** (in-sample-óptimo vs
  OOS-óptimo) → si coinciden = robusto, si divergen = sobreajuste. Reactiva.
- **OOS = cálculo paralelo e independiente**, sobre la porción reciente apartada; no referencia la salida
  del in-sample (sin contaminación). In-sample = 100% de trades (visual: nube MFE/MAE + líneas de palanca).
- **Dos gráficos de barras** (in-sample y OOS) que se mueven al mover palancas.
- **Botones:** Recalcular (se activa al mover una palanca; recomputa) · Restablecer (vuelve a la config
  derivada del estudio). Ya existen en `panel_palancas_multi.html`.

**UBICACIÓN Y FLUJO (RESUELTO 2026-07-10):** **Luxy vive DENTRO de la pestaña Estrategias** (sub-vista del
detalle de estrategia). **Flujo unificado:** crear estrategia → subir listado de operaciones → se integra
el master + corre el estudio Luxy → salen los parámetros en las **Tablas A/B**. Objetivo: **una sola fuente
de verdad por estrategia** (config + lista + estudio + descripción en un solo lugar; cero duplicación).

**Puntos 1–6 (RESUELTOS 2026-07-10):**
- **P1 alta de estrategia:** se parte de **crear una nueva estrategia** (la estrategia es el contenedor).
- **P2 selector:** se corrige solo al trabajar por estrategia (ves una a la vez). Refuerza el valor de las
  equivalencias por instrumento (6E/6J son FX: "punto" engañoso).
- **P3 sesiones:** mostrar el **rango horario ET** junto a cada nombre.
- **P4 ventana de operación:** Luxy la arroja, y **SÍ se usan las recomendaciones de Luxy (a criterio del
  operador)**.
- **P5 (ubicación) y P6 (equivalencias puntos/USD/X×ATR):** ya cubiertos arriba.

**CONSOLIDACIÓN EN ESTRATEGIAS (RESUELTO 2026-07-10):**
- **Retiro de Riesgo v1:** se deprecará **solo cuando Luxy esté terminado** (nada se rompe en la transición).
  - **CHECKLIST DE PARIDAD PARA L7 (notas de la revisión de L3, 2026-07-11):** antes de
    retirar la v1, dos ítems que aún viven en Riesgo v1 y deben quedar en Luxy:
    (a) **la ventana de operación derivada** (rango por lado / cobertura, helpers RIES-W)
    debe vivir en Luxy — hoy el dashboard L3 enlaza a la v1, no la duplica;
    (b) al retirar la v1, **unificar las particiones de sesiones** (`sesion_et` grueso del
    motor ↔ `LUXY_ZONES` de 6 zonas) en UNA sola fuente compartida (R-T7) para que motor,
    Lab y front no diverjan.
    (c) **(nota de L6, 2026-07-11) Decidir el acceso al Lab:** hoy hay DOBLE acceso — el Lab
    global sigue en `/ui/lab` (con su entrada en el nav) Y embebido en la sub-pestaña Lab del
    detalle. Se conservó así porque un 302 en `/ui/lab` + quitar la entrada del nav rompería
    ~13 tests que asertan `/ui/lab == 200` y `>Lab<` en el nav (contradice "suite intacta").
    A decidir para L7: (i) retirar la entrada del nav + 302 de `/ui/lab` → migrar esos ~13
    tests; o (ii) conservar el doble acceso. Es cosmético/UX, no de seguridad.
- **Lab también migra a Estrategias** como segundo tipo de estudio. Reparto: **Luxy = estudio de riesgo
  sobre listas de LuxAlgo**; **Lab = análisis técnico + HMM** (para otro tipo de estrategia, rol aún por
  definir). Detalle de estrategia = config + estudio Luxy + estudio Lab. **Juicio del arquitecto:** reubicar
  el Lab tal cual (mover UI) **sin reinvertir en rediseñarlo** hasta que su rol esté claro.
- **Fontanería / motor (juicio del arquitecto):** `nt_riesgo`/`mr_sims` (cuadre al dólar, sha256, intrabar)
  **NO se mudan** — se quedan como backend compartido; el flujo de alta en Estrategias los **llama**. Migra
  el **punto de entrada/UI** (subir lista → integrar → estudio dentro de Estrategias), no el código (P7).
- **Provisión de HOLC:** 8 activos actuales ya con HOLC (`NINJATRADER\HOLC`). Para un activo nuevo sin HOLC,
  NTEXECG da un **mecanismo de provisión** (el operador sube el HOLC como sube la lista; NTEXECG lo guarda y
  usa). Sin HOLC → estudio **degradado (sin intrabar) + aviso** + botón para proveerlo. Ítem del spec.
- **⚠ Aplicar reco → RESUELTO: "aplicar supervisado".** Reusa el Puente (diff + confirmar + AuditLog); NO
  amarra Estrategias a Luxy (el campo sigue aceptando manual/otra fuente; Luxy es un botón más).

**UX FINAL (RESUELTO 2026-07-10):**
- **A — Alta de estrategia: desde cero** (nombre + activo + subir lista → Luxy deriva el resto). Sin
  plantillas (Templates ya deprecado en P3).
- **B — Detalle de estrategia: sub-pestañas** `Config · Luxy · Lab · Perfiles` (no un scroll infinito).
- **C — Selector: desplegable** para elegir la estrategia en curso (o entrar al detalle desde Estrategias).

Estado: ✅ **CRITERIO DE DISEÑO DE LUXY COMPLETO** (P1–P7 + datos Crudo/In-sample/OOS + tablas A/B +
gráficos reactivos + botones + sesiones ET + equivalencias + ventana de operación + consolidación en
Estrategias + alta/detalle/selector + aplicar supervisado + provisión HOLC + retiro Riesgo v1 al terminar).
**PRÓXIMO PASO: escribir el spec de integración por lotes** sobre el motor de NTEXECG.

## 8-bis. RETOMAR AQUÍ — estado al 2026-07-13 (tras la etapa Fable 5)

**Lo construido (desplegado, suite 1004 verdes):** Luxy COMPLETO dentro de Estrategias (L1–L7b: alta,
estudio con Tablas A/B, dashboard dark, Perfiles, Aplicar vía Puente con R-T10, Lab embebido, retiro de
Riesgo v1) + **10 lotes LX** de refinamiento del operador + Portafolio (regla 1 ON, 2–8 inertes) + Parte C
+ Seguridad SEC-1/1b/1c. Docs: `HANDOFF_Contexto_2026-07-12.md`, `AUDITORIA_Fable_Cierre_2026-07-12.md`,
`AUDITORIA_TOTAL_Fable5_2026-07-12.md`, `SPEC_Luxy_Integracion_Lotes_2026-07-10.md`.

**Buena noticia (auditoría total):** los **8 invariantes rojos verificados en código y adversarialmente —
se sostienen**. No hay vía que despache sin stop, ni que aplique la fila OOS, ni que un puente toque el
kill-switch. **El motor de decisión está sano.** La deuda está en los bordes de datos y operación.

### 🔴 LO URGENTE (operativo, no de código)
**La secuencia de datos del server nunca se cerró, y la config del ES EN PRODUCCIÓN salió del estudio
envenenado** (bug de TZ pre-LX-6, el del PF 184). La demo despacha un bracket de ES derivado de datos
corruptos. Secuencia: `audit_ohlcv_tz` por activo → `--fix` dry-run → `--apply` → reintegrar ES →
Calcular → verificar (PF~2, participación ~100%, sin banner rojo) → **re-aplicar la config del ES** desde
el estudio sano (Puente). **Confirmar primero con el operador si ya la corrió.**

### Orden propuesto por el arquitecto (a validar mañana)
1. **FIX-1 (P1-1)** — `bar_store.parse_bar_time` trunca el offset TZ en silencio (**el "hermano" del bug
   LX-6, pero en el ESCRITOR**). Latente hoy (bridge = ET-naive) pero fail-open silencioso. Fix 1–3 líneas.
   ⚠ **Pregunta abierta:** ¿el `--apply` de `audit_ohlcv_tz` escribe pasando por `parse_bar_time`? Si sí,
   este fix va ANTES de la secuencia P0 (no re-envenenar mientras se corrige).
2. **P0 — secuencia del server** + re-aplicar config sana del ES.
3. **FIX-2 (P1-2)** — jobs/estado en memoria asumen 1 worker sin candado ni aviso; con `--workers>1` habría
   **doble cierre forzado**. Fix: forzar/documentar 1 worker o advisory-lock de Postgres.
4. **FIX-3 (P1-3)** — el webhook escribe+commitea `RawSignal` ANTES de validar el token → escritura DB no
   autenticada y sin cota.
5. **P2** — fricción para APAGAR la regla 1 del Portafolio; día ET (no UTC) en pérdida diaria; auditoría en
   savepoint; runs por mtime; offset del manifest; limpieza de código muerto (`tabla_a`, `riesgo.html` con
   fecha de caducidad, llaves huérfanas, `regime_now` sin UI).

**Backlog posterior:** observación en demo (ExecutionResult vs estudio), encender reglas 2–8 del Portafolio
una a una, SEC-2 (replay TOTP + bcrypt dummy), tareas del operador ({{interval}} en alertas LuxAlgo,
firewall a IPs Cloudflare, 2FA, TZ del bridge), backups de Postgres.

## 8. Log de cambios / hallazgos (append-only)

> Aquí registramos nuevas desviaciones, decisiones y avances. Fecha · qué · efecto en el backlog.

- **2026-07-07** — Contrato abierto. Se adopta Claude Code + Opus 4.8 como implementador (Fable en
  reserva). Foco inmediato: checkpoint demo (#0), luego RIES-W (#1).
- **2026-07-07** — Checkpoint #0: auditoría read-only corrida en el server. **6 aplicaciones del Puente
  registradas OK** con sus brackets (ES 90/11.5/8, RTY 110/29/26, GC 30/35/28.5, NQ 375/16/15.5,
  ES_CS 60/8/8.5, 6E 0.02/16.5/7.5). **Sin entradas aprobadas post-aplicación todavía** → #0 queda EN
  GUARDIA; re-correr el audit cuando dispare la 1ª entrada real. Se avanza a RIES-W.
- **2026-07-07** — RIES-W desplegado (`5afac92`) + **R1 CERRADO**: mapa de cobertura de las 7 (RTY 89.7%,
  6E 81.2%, GC 69.0%, ES 65.3% dejaban fuera; 3 ya al 100%). Ampliadas las 4 ventanas a Dom–Vie
  00:00–23:59 → las 7 cubren el 100%. Cambio en vivo vía config de estrategia.
- **2026-07-08** — **Checkpoint #0 VALIDADO en sustancia** ✅: con las ventanas abiertas dispararon
  entradas reales. **NQ short y RTY long salieron con el bracket NUEVO correcto** (stop pts fijos exacto
  + TP nominal ×ATR, modos backstop_fixed/nominal_atr). Los "FAIL" del audit son **falsos positivos**:
  (a) 6 eran señales de `exit` (no llevan bracket por diseño); (b) el TP de RTY difería en el 5º decimal
  por recomputar con ATR redondeado vs el de plena precisión del despacho. **Producción OK; el audit
  necesita 2 fixes** (saltar exits + tolerancia por tick/ATR real) — prompt dado a Claude Code.
- **2026-07-08** — **Checkpoint #0 CERRADO** ✅. Fix del audit (saltar exits + ATR de `extras` + tolerancia
  por tick) commiteado. Re-corrida en el server: **NQ short y RTY long = PASS a plena precisión (6
  deliveries), 0 FAIL, 0 bracket viejo, 3 exits aparte**. La demo despacha el bracket del estudio tal
  cual. Siguiente: **LAB-1**.
- **2026-07-08** — **PRIMERA PASADA DEL BACKLOG COMPLETA Y DESPLEGADA**: LAB-1 (datos conectados + 7
  cachés recalculadas), DASH-1 (tabla del bridge sana; **bridge exporta 1h confirmado**), EST-1 (evidencia
  L4 en vivo; wrappers byte-a-byte), ACT-1 (Activos conectada). Suite 820 verde. Verificado c/u contra el
  código. **Pendiente 2ª pasada:** LAB-2, DASH-2, EST-2, LAB-3 + módulo de portafolio (R4, §9.1).
- **2026-07-09** — **SEGUNDA PASADA COMPLETA Y DESPLEGADA**: LAB-2 (gestión de datos), DASH-2 (vigilancia
  de la demo), EST-2 (veredicto del Lab por filtro), LAB-3 (sanidad; **reconcile en server 9/9 coinciden**
  → Lab↔Motor alineados en producción). Suite 848 verde. **TODO EL BACKLOG DE OBSERVACIÓN CERRADO.**
  Quedan solo: módulo de portafolio (R4, §9.1, workstream aparte), y pendientes menores (DASH-2 `updated_at`
  cosmético; R5 export de RTY; opcional retirar tarjeta "Live 0").
- **2026-07-09** — Revisado a fondo **Luxy = Riesgo v2** (`C:\NTEXECG\LUXY\`), registrado como workstream
  §9.2 con 7 puntos a validar (3 de correctitud: OOS, motor intrabar único, corte cancel_after). Próximo:
  sesión de decisiones de arquitectura → spec de integración por lotes. Se trabaja "mañana o más tarde".
- **2026-07-11** — **SEC-1 (núcleo 1,2,3,4,6) desplegado** (lockout+backoff del login, 2FA TOTP opcional
  cero-dep, fail-fast del SESSION_SECRET, headers+CSP+SRI real de Alpine, revocación de sesiones). **SEC-1b
  (Tarea 5) desplegado**: token del webhook fuera del query string (store efímero one-time TTL 60s +
  `/ui/strategies/token-once/<id>`; alta/rotación/clone/Puente migrados). **Observaciones menores de la
  revisión de SEC-1 (mejoras opcionales, NO bloqueantes):**
  - (a) **TOTP sin guard de replay** — un código de 6 dígitos válido puede reusarse dentro de su ventana de
    30s. Mitigable con un set en memoria de (secreto,código,periodo) ya usados. Riesgo bajo (single-admin,
    ventana corta).
  - (b) **Timing del username NO constante** — `authenticate` retorna antes de correr bcrypt cuando el
    usuario no coincide → un atacante podría distinguir "usuario válido" por el tiempo de respuesta. Fix:
    correr siempre un bcrypt dummy. Riesgo bajo (un solo usuario conocido: "admin").
  - (c) **Confianza en `CF-Connecting-IP` sin validar origen** — si el server fuera accesible saltándose
    Cloudflare, un atacante podría falsificar la IP y evadir el lockout por-IP (el por-usuario sigue).
    **Mitigación operativa (recomendada): firewall del server a las IPs de Cloudflare** (allowlist del
    rango publicado). Anotado para el operador.
- **2026-07-11** — **PARTE C (limpieza del Config, UI-only) ejecutada.** Filtros de calidad (Fase 5) y
  régimen (Fase 6) salieron del form de Config; EST-1/EST-2 (régimen actual, última eval, veredicto del
  Lab) ya no se ven en la ficha. Los **servicios se conservan** (`quality_scorer.py`, `hmm_service.py`) —
  el Lab los importa (banner informativo intacto). Sin migración destructiva: las llaves JSON viejas
  (`filters`, `regime`, `guardrails.enforce_*`) **quedan huérfanas de UI** pero persisten en
  `pipeline_config_json` (documentado; round-trip bajo prueba).
  - **Guardarraíles de ruteo SIEMPRE-ON** (Anexo 08 #2): se quitó el toggle de la UI y el valor persistido
    se **ignora** — el chequeo se fuerza en `ConfigResolver` (`enforce_symbol_match` +
    `enforce_timeframe_match = True`), no en el pipeline (los tests opt-in de `filter_pipeline` quedan
    intactos).
  - **Decisión del arquitecto (opción 3, fail-honest) sobre el guardarraíl de timeframe:** forzar
    `enforce_timeframe_match` chocaba con participación 100% — una alerta de LuxAlgo **sin `interval`**
    normaliza a timeframe vacío y el gate la bloquearía (`interval_mismatch`). Solución: el chequeo 1.8
    **se salta SOLO cuando la señal no trae timeframe** (vacío/ausente → no bloquea) y anota
    **`tf_not_verified=true`** en `pipeline_execution_json.level_1` (visible en el detalle/logs). Un
    `interval` **presente que no coincide** sí bloquea, como siempre. (Único cambio de lógica del pipeline
    en esta parte, autorizado explícitamente.)
  - 🧑‍⚖️ **Recomendación al operador:** **añadir `{{interval}}` a las alertas de LuxAlgo** (campo
    `interval` del webhook) para que el guardarraíl de temporalidad aplique **completo**; mientras falte,
    esas señales pasan con `tf_not_verified` (la verificación de timeframe NO ocurrió).
  - Deuda menor anotada: el detalle aún **computa** `regime_now`/`ultima_eval`/`lab_evidence` en la ruta
    (lectura, sin escrituras) aunque la plantilla ya no los use — candidato a limpieza de ruta en un lote
    aparte (esta parte fue UI-only).
- **2026-07-11** — **L7a (paridad Luxy↔Riesgo v1, pre-retiro) ejecutada.** Cierra los huecos de paridad
  del checklist antes del retiro (L7b). Dos ítems que solo vivían en v1 ahora son NATIVOS en Luxy:
  - **(a) Ventana de operación** — el dashboard Luxy ya no enlaza a v1 para esto: el estudio adjunta
    `ventana_operacion` (por_sesión, rango horario ET por lado/total, ventana mínima de cobertura,
    muestras) y `duracion_h_por_lado` al payload del dashboard, **reusando el helper RIES-W de v1**
    (`nt_riesgo._listado_crudo`) sobre el listado crudo + offset ET — **cero duplicación, paridad numérica
    exacta** (test gated con ES real: `dash["ventana_operacion"] == _listado_crudo(trades, off)`). El front
    tiene panel nativo (`#lx-window`) con rango por lado + cobertura + comparación con la ventana L2 vigente
    (reusa `routes_riesgo._pct_trades_fuera`). `_load_master` ahora devuelve el `off` ET.
  - **(b) Partición de sesiones unificada (R-T7)** — extraído `scripts/sesiones_et.py` como FUENTE
    canónica de las 6 zonas ET (`LUXY_ZONES`/`zone_of_hour`/`_DAY_ES`); `mr_luxy` las **consume**
    (re-export, mismos objetos). **v1 NO se toca** (su `sesion_et` grueso muere en L7b; su recrear
    bit-a-bit sigue verde).
  - **Checklist de paridad (ítem → ubicación en Luxy → test → estado):**
    | Ítem | Ubicación en Luxy | Test | Estado |
    |---|---|---|---|
    | aplicar (L5) | `mr_luxy.activacion_from_study` + `/luxy/aplicar[/preview]` (reusa el Puente) | `test_luxy_aplicar_l5.py` | ✅ |
    | deriva (L5) | badge `luxy_deriva` (`rr.deriva_estudio`) en el detalle | `test_puente_riesgo.py` · `test_estrategias_l1.py` | ✅ |
    | cuenta por estrategia | Perfiles L4 (`_perfiles_panel`: sizing por cuenta/perfil + `target_account`) | `test_perfiles_l4.py` | ✅ |
    | protección para la cuenta | Perfiles L4 (`worst_case`, `micros_that_fit`, `limited_by` por cuenta) | `test_perfiles_l4.py` | ✅ |
    | rango/duración por lado | `dashboard.duracion_h_por_lado` (helper RIES-W) | `test_estrategias_l1::test_luxy_ventana_paridad_v1_real` | ✅ (L7a) |
    | ventana de operación | `dashboard.ventana_operacion` + panel `#lx-window` | `test_estrategias_l1::test_luxy_ventana_paridad_v1_real` | ✅ (L7a) |
    | zonas ET (R-T7) | `scripts/sesiones_et.py` (canónico) → `mr_luxy` | `test_sesiones_et.py` · `test_mr_luxy_l2::test_zones_particion_unica_rt7` | ✅ (L7a) |
  - Nota: `cuenta`/`protección` se consideran cubiertas por la analítica de Perfiles L4 (sizing por cuenta
    con peor-caso), no por un port literal de `proteccion_para_cuenta`; si el arquitecto quiere el estudio
    de supervivencia v1 tal cual, sería un ítem extra. **Pendiente para L7b:** decisión (c) del doble acceso
    al Lab (nav `/ui/lab` + iframe) y el retiro no destructivo de la UI de v1.
- **2026-07-11** — **L7b (retiro no destructivo de Riesgo v1 + Lab fuera del nav) ejecutada** (patrón P3,
  con el checklist de paridad de L7a en verde). **Rollback trivial: `git revert` del commit restaura la
  página v1** (la plantilla `riesgo.html` y toda la lógica de contexto siguen en el repo).
  - **Riesgo v1 UI fuera:** `GET /ui/riesgo` ya no renderiza — **redirige (302)**: con `?strategy=X`
    (clave/id válido) → `/ui/strategies/X` (sub-pestaña Luxy); sin parámetro → `/ui/strategies`. Entrada
    del nav retirada. **Motor/datos/estudios INTACTOS**: todos los endpoints operativos
    (`/ui/riesgo/upload|calcular|cuenta|estrategia/renombrar|estrategia|datos|heatmap|reporte|aplicar[/preview]`)
    y **todos los helpers** (`deriva_estudio`, `_merge_activacion`, `_diff_aplicar`, `_activacion_json`,
    `_leer_cuenta`, `_pct_trades_fuera`, `clave_de`, `_motor_manifest`, `holc_disponible`, `integrar_lista`,
    `_latest_estudio`) **siguen vivos** — los reusan L5/L7a y Estrategias (criterio 2, con test explícito).
  - **Lab fuera del nav** (decisión (c) del operador — RESUELTA): la entrada del nav se retiró; `/ui/lab`
    **sigue respondiendo 200** (el iframe de L6 y los bookmarks lo necesitan). La navegación canónica es la
    sub-pestaña Lab del detalle.
  - **Promoción estudio→viva** (`/ui/strategies/new` con `from_estudio`): ahora encadena al **detalle de
    Estrategias** (`/ui/strategies/{id}?token_id=…&aplicar=1`), no a la página v1; el token efímero (SEC-1b)
    intacto.
  - **Tests migrados (P3):** nav/acceso — `test_p3_limpieza` (nav vía `/ui/strategies`, sin Riesgo/Lab),
    `test_lab_migracion_l6::test_lab_global_sigue_vivo` (`>Lab<` fuera del nav), `test_dashboard_unificado`
    (partial app-wide vía `/ui/positions`), `test_estrategias_l1::test_riesgo_v1_redirige_al_detalle`,
    `test_puente_riesgo` ×3 (badge/CTA/encadenado → redirect + detalle). Fichas v1 retiradas con la página,
    lógica preservada en unidad — `test_robs2`: `test_cuenta_por_estrategia` (endpoint+`_leer_cuenta`),
    render de rango/protección **eliminados** (cubiertos por `test_listado_crudo_rango_por_lado`,
    `test_proteccion_*`, y en el DETALLE por L7a/L4), ventana v1 ×3 → **`test_pct_trades_fuera_helper_intacto`**
    (unidad, incluida la arista "% fuera"). Suite `test_riesgo_ui` (front v1): 9 tests de render **retirados**,
    4 de endpoint **convertidos** (cuenta/renombrar/borrar/aceptación → estado persistido y helpers, no HTML)
    + `test_riesgo_v1_redirect_p3`. **Nuevo `tests/test_l7b_retiro.py`** (7): redirects, nav sin Riesgo/Lab,
    `/ui/lab` 200, supervivencia de helpers y endpoints. `aplicar-desde-Luxy` e2e queda en `test_luxy_aplicar_l5`.
  - **Suites v1 de MOTOR intactas** (recrear bit-a-bit): no se tocó `nt_riesgo`/`mr_sims`.
  - Deuda menor: el Lab enlaza "nueva estrategia" a `/ui/riesgo` (hoy un hop de redirect a `/ui/strategies`);
    apuntarlo directo es cosmético — candidato a limpieza suelta.
- **2026-07-12** — **LX-1 (correcciones + mejora del dashboard Luxy, parte 1/2) ejecutada.** JS-pesado:
  **⚠ requiere smoke de navegador del OPERADOR antes del sign-off del arquitecto** (la suite no ejecuta JS).
  1. **2FA honesto en login:** el campo TOTP de `login.html` solo se pinta si `settings.UI_TOTP_SECRET` está
     configurado (GET `/ui/login` pasa `totp_enabled`). Off → form usuario+contraseña limpio. Tests
     `test_security_sec1`: `test_login_sin_2fa_no_pinta_campo_totp` / `..._con_2fa_pinta_campo_totp`.
  2. **Restablecer (bug) arreglado:** `pollEval` ya NO hace `Object.assign(RECO,S)` — se congela `RECO0`
     (copia inmutable de las palancas del estudio) al cargar; **Restablecer SIEMPRE vuelve a `RECO0`** +
     los números validados del estudio (`V0`). Recalcular actualiza `VLAST` ("último validado") pero **jamás
     pisa `RECO0`**. `initControls` se separó en **`bindOnce()`** (listeners una vez) + **`setValues()`**
     (valores en cada reset) → sin listeners duplicados.
  3. **Un solo diagrama a todo el ancho:** los dos canvas (`lx-chart-in`/`lx-chart-oos`) se reemplazan por
     `#lx-chart` con el 100% de trades en orden cronológico, **línea vertical de corte** en `cutoff_i` (del
     split), barras OOS en tono tenue, rótulo "← in-sample · OOS →". Un solo juego de líneas de palanca cruza
     todo el ancho.
  4. **Tabla reactiva de 3 filas** (`#lx-table3`) reemplaza las teselas (`#lx-tiles`), formato Tabla A
     (Crudo·In-sample·OOS × Neto·PF·MaxDD·Peor·Particip.·WR·N): **Crudo fija**; **In-sample y OOS
     recalculadas cada una sobre SU subconjunto por separado** (`d.in`/`!d.in`, R-T10 — jamás mezclados);
     OOS rotulada "espejo — no es la config a usar". Estimación client-side por ventana + chip "estimación"
     (el BE sigue sin acreditarse). **Motor corregido:** `evaluate_overrides.config` ahora evalúa **solo
     sobre in-sample** (antes corría sobre todos los `sts`) + OOS-espejo con las mismas palancas; el payload
     del dashboard añade `table3` (crudo/in/oos por subconjunto) y `cutoff_i`. **Decisión: la Tabla A
     estática de abajo se RETIRÓ** (redundante con la reactiva; el payload conserva `luxy.tabla_a`). La
     **Tabla B NO se tocó.**
  - Tests motor (gated ES): `test_luxy_evaluar_parity_real` reescrito — `config == table3.in` (subconjunto),
    `oos == table3.oos`, `in.n+oos.n == n_total`, `cutoff_i == n_in_sample` y coincide con la frontera in→oos
    de la nube. Determinismo del estudio intacto (`test_mr_luxy_l2`). **Pendiente: LX-1 parte 2/2** + el smoke
    de navegador del operador.
- **2026-07-12** — **LX-2 (toggles por sesión y por día en el dashboard Luxy, parte 2/2) ejecutada.**
  Port de los switches del prototipo (`ZBLK/ZBLKD/zoneRows`), semántica **ON = incluida** (nacen todas ON).
  **⚠ smoke de navegador del operador OBLIGATORIO** (JS-pesado; la suite no ejecuta JS).
  1. **UI:** switch dark (patrón sw/track/knob → `.lx-sw/.lx-tr/.lx-kn`) delante de cada una de las **6 zonas
     ET** (fichas "Sesiones (ET)", del módulo canónico `sesiones_et.py`) y de cada **día** ("Días").
  2. **Estimación:** apagar una sesión/día excluye esos trades (por `zone_of_hour(hr)` ET y por `dow`) del
     cálculo client-side **en cada ventana por separado** (tabla de 3 filas: In-sample y OOS caen cada una con
     lo suyo, Crudo fija); **la participación CAE** (ntot = subconjunto completo). En el diagrama las barras
     excluidas se **atenúan** (`globalAlpha`), no desaparecen. Chip "estimación" mientras no valide.
  3. **Recalcular (motor):** `evaluate_overrides` acepta `zones_off` (nombres de zona canónica) y `days_off`
     (dow 0-6) dentro del dict de overrides; excluye por zona/día **antes** de evaluar cada ventana con el
     **mismo `zone_of_hour` de `sesiones_et`** (una sola fuente, R-T7). El crudo (base) NUNCA se filtra.
     Determinismo bit-a-bit del estudio base intacto.
  4. **Restablecer** integrado al `RECO0` de LX-1: restaura palancas **y** toggles (todo ON, estado del
     estudio `ZON0/DON0`) + los números validados del estudio.
  5. **No persiste / no entra en Aplicar:** se conserva el aviso "bloquear solo con razón real — no persiste";
     `activacion_from_study` sale SOLO de `levers_in_sample` → **aplicar-desde-Luxy con toggles apagados no
     escribe nada de sesiones/días** (test explícito `test_activacion_from_study_ignora_toggles`).
  6. **Tests:** motor gated ES — `days_off=[4]`/`zones_off=["Asia"]` bajan n solo en las filas por ventana
     (crudo intacto), sin toggles = byte-igual (no regresión LX-1) y determinista
     (`test_luxy_toggles_motor_real`); `zone_of_hour` como fuente única (`test_luxy_toggles_lx2`); render de
     switches (CSS + binding + payload) en `test_luxy_e2e_real`. **Pendiente: smoke de navegador del operador.**
   - **Pre-sign-off (verificación LX-2, 2026-07-12):** confirmado que `_passes` usa la **hora ET** (`tr.hour =
     (entry_ts+offset).hour` del enriched); el fallback `entry_ts.hour` (hora cruda del CSV) era una trampa
     latente → endurecido con el helper **`_entry_hour_et(tr, off)`** (fuente ET única, usa el `off` de
     `_load_master`, antes descartado), aplicado también a la nube. Test `test_entry_hour_et_usa_offset_no_hora_cruda`.
- **2026-07-12** — **LX-3 (resemántica de la tabla reactiva del dashboard Luxy) ejecutada** (decisión del
  operador). **⚠ smoke de navegador del operador OBLIGATORIO** (JS-pesado). Filas nuevas:
  - **Crudo** — lista de operaciones base, SIN palancas, n = TODOS los trades (fija).
  - **Crudo+** (tooltip "Crudo + palancas") — TODAS las palancas actuales sobre el **100% de la muestra
    SIMULABLE** (todos los `sts`, viejos+recientes) = la semántica de la vieja fila In-sample de la Tabla A
    (`fila_in`).
  - **OOS espejo** — sin cambios: palancas del in-sample SOLO sobre la muestra apartada (R-T10).
  - **Aislamiento (criterio 2):** cada fila se computa por separado compartiendo solo la lista base; mover una
    palanca no altera Crudo, y Crudo+/OOS son `eval_levers` independientes.
  - **Nota honesta de muestra (criterio 3):** el payload expone `n_total`/`n_simulable`; si `n_simulable <
    n_total` (trades sin intrabar) la tabla pinta "N de Crudo+ = X de Y — Z trades sin datos intrabar no son
    simulables con palancas". (El export de ES del repo está 100% cubierto → no dispara la nota, pero el
    texto/lógica viajan y se testean.)
  - **Motor:** `evaluate_overrides.config` = Crudo+ (eval sobre TODOS los `sts` + toggles LX-2); `oos` =
    espejo sobre su subconjunto. `luxy_study.table3` = {crudo, crudo_plus=`fila_in`, oos=`fila_oos`} +
    `n_total`/`n_simulable`. **Estimación client-side alineada:** `estimateRows('all')` sobre el 100% de la
    nube (consistente con el diagrama), `estimateRows('oos')` sobre su subconjunto. El corte visual del
    diagrama se conserva. Determinismo del estudio base intacto.
  - **Tests (gated ES):** `crudo n`=lista base, `crudo_plus n`=simulable y == vieja `fila_in` de `tabla_a`
    con mismas palancas, `oos n`=subconjunto; aislamiento (crudo fijo al mover palanca, config reacciona);
    toggles LX-2 aplican a Crudo+ y OOS; nota de muestra (texto + payload); recrear bit-a-bit intacto.
    **Pendiente: smoke de navegador del operador.**
- **2026-07-12** — **LX-3b (adendas de la tabla reactiva, cierre de LX-3) ejecutada.** **⚠ smoke de
  navegador del operador cubre LX-3+LX-3b juntos.** Lógica testable movida a Python (payload), el front la
  muestra:
  1. **Semáforo de robustez** junto a la tabla, SOLO de la fila OOS **validada** (la estimación lo atenúa +
     "pendiente de Recalcular"): 🟢 neto>0 y PF≥1.3 · 🟡 neto>0 y PF 1.0–1.3 · 🔴 neto≤0 o PF<1.0. Umbrales
     como constantes nombradas (`ROBUSTEZ_PF_VERDE=1.3`, `ROBUSTEZ_PF_MIN=1.0`). Tooltip de degradación OOS.
     `dashboard["robustez"]` + `evaluate_overrides` lo devuelve (Recalcular lo refresca; Restablecer vuelve al
     del estudio).
  2. **Columna "$/trade"** (neto ÷ n, con guarda) en las 3 filas; en OOS, **retención** "retiene X% del
     $/trade de Crudo+" (guarda de división; "muestra chica (n=X)" si n_oos<10, `RETENCION_N_MIN=10`). Vive
     en estimación (client-side) y en validado (`dashboard["retencion"]`).
  3. **Banner ámbar de muestra** (sube desde la nota de LX-3) cuando `n_simulable < n_total`, **texto
     corregido** (el HOLC vive en NTEXECG, no viaja en la lista): "X de Y trades fuera de la cobertura HOLC
     almacenada en NTEXECG (cola posterior a la última barra, o previos al inicio) — Crudo+ los excluye de la
     simulación". None cuando `n_simulable == n_total` (`muestra_banner()`). *(La distinción fina cola/inicio
     con el manifest llega en LX-4.)*
  - **Tests:** semáforo 3 estados + fail-honest + umbrales (sintético) y gated ES (verdict coherente con los
    umbrales del OOS real); `$/trade`/retención con guardas (n<10, división por cero); banner on/off + texto
    (`test_luxy_toggles_lx2`); render del semáforo/columna/retención en `test_luxy_e2e_real`. Determinismo
    intacto. **Pendiente: smoke de navegador del operador (LX-3 + LX-3b).**
- **2026-07-12** — **LX-4 (costura HOLC por default en producción) ejecutada.**
  1. **Stitch por default en el flujo web:** `routes_riesgo._stitch()` ahora devuelve True por default
     (integrar/calcular del panel cosen la cola de `ohlcv_bars`); **apagada en APP_ENV=test** (la suite no
     exige Postgres); la env var sigue como override y el CLI conserva `--stitch-db`.
  2. **Luxy hereda la cobertura por R-T2:** al integrar se escribe un **snapshot HOLC por-clave**
     (`MotorRiesgo/<clave>/holc_5m.csv`, cosido si hubo stitch) y `mr_luxy._load_master` lo lee con prioridad
     sobre el HOLC global (fallback al global para masters viejos). Así reintegrar-con-costura sube
     `n_simulable` (gated: truncar el snapshot baja n / restaurarlo lo sube). `load_holc` se refactorizó a
     `load_holc_from_path`.
  3. **Fail-honest con umbral REAL del solape CSV↔DB:** `STITCH_MAX_INCONSISTENTES_PCT = 0.01%` (constante
     nombrada; prod: 1/387.011 = 0.00026% << umbral). `stitch_from_db` devuelve `(bars, stats)`; por debajo
     procede y reporta en el manifest (`holc.stitch`), por encima **ABORTA** con mensaje claro; DB vacía /
     cola corta → procede (added 0, no inventa datos).
  4. **Banner con datos del manifest:** `muestra_banner(n_total, n_simulable, holc_meta)` distingue "N en la
     cola descubierta desde <última barra cosida>" vs "N previos al inicio del almacén" e indica "Reintegra
     la lista para actualizar la cobertura". (Distinción fina cola/inicio con el manifest = este lote; el
     front ya pintaba `D.muestra_banner`.)
  5. **Recrear bit-a-bit intacto** (`test_recrear_bit_a_bit` verde; el snapshot por-clave no toca el sha256
     del master ni la meta del estudio).
  6. **Verificación del updater (sin arreglar):** `MarketBarsUpdater` itera SOLO los símbolos **activos** del
     catálogo (`SymbolMap.active`) resolviendo su símbolo de datos, × 4 timeframes, y **reporta** (log
     `market_bars_fetch_failed`) los que fallan sin dropearlos. Cobertura = catálogo activo con símbolo de
     datos resoluble; huecos posibles: símbolos activos sin `resolve_market_data_symbol` o sin archivo del
     bridge. **Decisión del operador si algún activo del catálogo no se está actualizando.**
  - **Tests:** umbral debajo/encima/DB-vacía (parcheando `AsyncSessionLocal`, sin Postgres); gate `_stitch()`
     (test/prod/override) + `--stitch-db` en los cmd; banner cola/inicio; Luxy hereda cobertura vía snapshot
     (gated); updater estructural; recrear intacto (`tests/test_stitch_lx4.py` + `test_estrategias_l1`).
- **2026-07-12** — **LX-5 (una sola definición de "simulable") ejecutada** — cierra el hallazgo del
  diagnóstico (ATR-estimados inflaban `len(sts)` → banner apagado; `recon_ok` placeholder engañoso).
  1. **Definición ÚNICA:** para Luxy, SIMULABLE = trade con **ATR intrabar REAL** (los que entran a `sts`;
     `mr_luxy` no estima cola, a diferencia de v1 §9.2 que NO se toca). Los no-simulables se cuentan aparte
     (`n_no_simulable`, `n_estimados`=cola, `n_inicio`) clasificados desde los datos del propio estudio.
  2. **Consistencia total de Ns** (tested gated ES): `n_simulable == Crudo+ n == split.n_in_sample +
     split.n_oos == n_total − n_no_simulable`. La cabecera del split muestra **doble universo**: "in-sample
     85 (76 simulables) · OOS 37 (28 simulables)" (`split.n_trades_in/n_trades_oos` + `n_in_sample/n_oos`).
  3. **Banner** enciende SIEMPRE que `n_simulable < n_total`, con desglose por causa: "N en la cola posterior
     a la última barra cosida (‹ts›) — reintegra cuando el updater alcance" · "Y previos al inicio del
     almacén"; nunca vuelve a contar estimados como simulables. `muestra_banner()` reescrita (5 args, sin el
     `holc_meta` de LX-4 — el desglose sale del estudio, no del manifest v1).
  4. **Chip recon → opción (b):** re-rotulado a **"intrabar {n_simulable}/{n_total}"** (con tooltip), se
     eliminó la palabra "recon" y el placeholder `len(sts)/len(sts)`. (La reconciliación real luxy↔v1 vive en
     la sección C; no se computa en cada estudio por costo.)
  5. **Tests:** consistencia de los tres Ns + doble universo (gated ES, full y con cobertura truncada);
     banner ON con desglose "cola posterior" (gated); `muestra_banner` breakdown/guardas (unit); determinismo
     y recrear intactos; **v1 sin cambios** (sus tests byte-iguales). **Pendiente: smoke de navegador** (toca
     números que el operador ve: split doble universo, chip intrabar, banner con desglose).
- **2026-07-12** — **LX-6 (fix de la cola cosida TZ + endurecimiento del stitch) — REVISIÓN OBLIGATORIA**
  (toca datos de mercado que alimentan TODAS las decisiones). Cierra el bug diagnosticado (cola del bridge
  con TZ distinta al CSV ET-naive envenenaba el intrabar → PF 184, participación 52%, peor −$160).
  0. **Empírico:** `backfill` escribe ET-naive (del CSV NinjaTrader) ✓; `MarketBarsUpdater` guarda el `time`
     del JSON del bridge .NET — TZ a verificar EN EL SERVER con **`scripts/audit_ohlcv_tz.py`** (nuevo:
     reporta por proveniencia/rango qué desplazamiento alinea con el CSV → homogéneo vs heterogéneo; `--fix`
     dry-run, `--apply` gated y auto-verificable — no migración a ciegas).
  1. **Convención canónica ET-naive:** LECTURA con `_et_naive` (tz-aware → America/New_York → naive; naive →
     ET; NUNCA `.replace(tzinfo=None)` a ciegas). ESCRITURA documentada en `bar_store` (ambos escritores
     garantizan ET; el bridge debe entregar ET).
  2. **Stitch FAIL-CLOSED:** (a) solape mínimo `STITCH_MIN_OVERLAP_BARS=12` → si `checked < mín` (una cola
     mal-TZ solapa ~0 keys) **ABORTA** "no puedo verificar la alineación"; (b) **continuidad** — la cola no
     puede saltar más que la rejilla de sesión del propio HOLC → ABORTA; (c) umbral de inconsistentes (LX-4)
     conservado. DB vacía/sin cola → procede (no inventa).
  3. **Tripwire de plausibilidad** (`tripwire_implausible`): con C1 al mercado y sin corte de lado,
     participación <90% o PF >`PF_ABSURDO=50` → `dashboard.implausible` + **banner ROJO** "números
     implausibles: revisa alineación/cobertura intrabar" y el **semáforo NO se enciende**. "Este bug se vio a
     ojo; el próximo se ve solo."
  4. **Tests:** `_et_naive` aware→ET; cola UTC-aware normalizada cose sano; **solape vacío / < mínimo / salto
     → abortan**; DB vacía no aborta; tripwire on/off; round-trip snapshot↔global byte-perfecto (LX-4);
     determinismo/recrear/v1 intactos; ES limpio NO implausible (gated).
  5. **Verificación server (harness):** correr `audit_ohlcv_tz.py` para ver la convención real; luego el
     harness SNAPSHOT-vs-GLOBAL del diagnóstico debe converger (PF ~2) y el estudio nuevo del ES con cola
     cosida debe dar participación ~100% (o abortar la costura si la cola no es verificable). **Pendiente:
     smoke de navegador + correr el audit en el server.**
- **2026-07-12** — **LX-7 (PF honesto en muestras filtradas) ejecutada.** Hallazgo del arquitecto (datos
  limpios, NO bug de cálculo): dir=largos + viernes/asia OFF deja 1 solo perdedor → PF 182 aritméticamente
  correcto pero **estadísticamente vacío**.
  1. Front: si la fila tiene < `MIN_PERDEDORES_PF=3` perdedores, el PF se muestra **"n/s (N perdedor[es])"**
     en ámbar con tooltip "PF sin significado: los filtros dejaron casi sin perdedores la muestra (posible
     cherry-picking)" — nunca el número crudo. Aplica a Crudo+ y OOS, en estimación Y validado.
  2. Motor: `metrics_usd` expone **`n_perdedores`**, propagado por `_card`/`_rowA` → `evaluate_overrides` y
     `table3`; el front decide el rotulado con la misma constante.
  3. La nota "muestra chica (n<10)" se **generalizó** a cualquier fila (no solo OOS).
  4. Tests: `n_perdedores` (0/1/3) en `metrics_usd`+`_card`; motor lo expone (gated ES: crudo_plus ≥3);
     render "n/s"/cherry-picking en el JS (`test_luxy_pf_lx7` + `test_estrategias_l1`). (Complementa el
     tripwire LX-6: PF>50 → rojo/implausible; LX-7 cubre el PF sub-50 pero sin perdedores.) **Pendiente:
     smoke de navegador.**
- **2026-07-12** — **LX-8 (Puente de ventanas: toggles de Luxy → ventanas L2, supervisado) ejecutada.**
  1. **Compilador PURO** `scripts.sesiones_et.compilar_ventanas_l2(zonas_on, dias_on_w)` → ventanas L2 mínimas
     `[{days(%w), start, end, next_day_end?}]`. Maneja medianoche (Asia 19:00–01:59 → `next_day_end`), huecos
     (→ varias ventanas), todo-ON (→ 00:00–23:59, 7/7), todo-OFF (→ None, inválido). Determinista. Verificado
     el caso del arquitecto: Asia+Viernes OFF → `[{days:[0,1,2,3,4], 02:00–18:59}]` (L-X,J,D).
  2. **Botón "Proponer ventanas L2 desde estos filtros…"** + modal de preview: (a) actuales vs propuestas;
     (b) **el costo en la cara** — `_pct_trades_fuera` (helper real de L7a) actual→propuesta + conteo por lado
     (desde la nube); (c) los dos avisos (el filtro de sesión NO aporta edge, 2026-07-04, es decisión de
     riesgo; PF alto en muestra filtrada NO es evidencia, LX-7).
  3. **Confirmar** escribe en el MISMO store que la pestaña Ventanas (`pipeline_config_json.windows`,
     **reemplaza el set completo**) + **AuditLog `action=APPLY_LUXY_VENTANAS`** con `old/new_value` de las
     ventanas. Rutas: `POST /luxy/ventanas/preview` y `/aplicar`.
  4. **No toca:** dirección (fuera de alcance), toggles no persisten en Luxy (siguen diagnóstico),
     mode/dry_run/kill-switch jamás (test: `set(cfg)=={backstop_points,windows}`).
  5. **Tests:** compilador (medianoche, huecos, todo-ON/OFF, forma exacta Asia+Viernes, determinismo);
     preview con %fuera+por-lado+avisos; aplicar escribe ventanas + audit antes/después; formato round-trip
     por el endpoint canónico `/windows`; todo-OFF → 400. **Pendiente: smoke de navegador (modal preview).**
- **2026-07-12** — **LX-9 (persistencia de la exploración Luxy — solo navegador) ejecutada.**
  1. El estado de exploración (`S` palancas, dir, `ZON`/`DON` toggles) se guarda en `localStorage`
     (`ntexecg_luxy_explore_<strategy_id>`) en cada `refresh()` con **debounce** (400ms).
  2. Al cargar: si el estado guardado corresponde al estudio vigente (`estudio_id = fecha:sha_master`, nuevo
     campo del payload), se restaura; si el estudio cambió → se descarta y limpia. **`VLAST` (validados del
     motor) JAMÁS se restaura** → si difiere de la reco, el chip queda en "estimación · aprox" (sale de
     `dirty()`); la restauración nunca se presenta como validada.
  3. **Restablecer** también borra el estado guardado (`clearExplore`) → vuelve a `RECO0` limpio.
  4. **NADA viaja al server:** cero endpoints nuevos, cero escrituras de config; el único cambio de motor es
     exponer `dashboard.estudio_id` para la invalidación. Los puentes (Aplicar, Proponer ventanas) siguen
     siendo el único camino a producción.
  5. Test server-side: el payload incluye `estudio_id` (`fecha:sha`) — gated ES. Smoke JS (pendiente):
     configurar palancas/toggles → ir a Config → volver (estado restaurado, chip "estimación"); Restablecer
     (limpia UI + borra storage); recalcular estudio nuevo (`estudio_id` cambia → exploración vieja
     descartada). **Pendiente: smoke de navegador OBLIGATORIO.**
- **2026-07-12** — **LX-10 (guardar exploración Luxy en el server — botón explícito) ejecutada.**
  Complementa LX-9: localStorage = auto-guardado de sesión; esto añade un snapshot server-side que
  sobrevive cambios de navegador/equipo.
  1. **Almacén PROPIO, JAMÁS en la config:** tabla nueva `luxy_exploracion` (`strategy_id` PK/FK →
     `strategies`, `ondelete=CASCADE`; `estado_json {S,dir,ZON,DON}`, `estudio_id`, `updated_at`) +
     migración `a7b8c9d0e1f2` (aplica y revierte, verificado). `pipeline_config_json` NO se toca — esto es
     diagnóstico con otra vida.
  2. **UI:** botón "Guardar exploración" junto a Restablecer; al guardar muestra el sello "guardada · <fecha
     hora>", que queda visible siempre que exista snapshot. Guardar **sobreescribe** (uno por estrategia).
     Botón secundario "borrar guardada" (DELETE con `confirm()`) para quitarlo. Restablecer (LX-9) NO borra
     el snapshot del server, solo lo local.
  3. **Carga con precedencia:** `localStorage` (LX-9, actividad más reciente) → snapshot del server. Ambos
     con la **misma invalidación** por `estudio_id` (estudio nuevo → nota discreta "de un estudio anterior",
     NO restaura). La restauración SIEMPRE cae en "estimación · aprox" — `VLAST` jamás se restaura (regla
     LX-9 intacta). El sello del server se pinta aunque gane lo local (para no ocultar que hay snapshot).
  4. **Endpoints** en el router protegido (`require_auth`): `GET/PUT/DELETE
     /ui/strategies/<id>/luxy/exploracion`. PUT valida shape (llaves conocidas `{S,dir,ZON,DON}`) y tamaño
     (**rechaza >8KB** → 400); estrategia inexistente → 404.
  5. **Tests** (`test_luxy_exploracion_lx10.py`, 9): PUT/GET round-trip + sobreescritura; shape inválido y
     oversize → 400; sin sesión → 303 `/ui/login`; `estudio_id` viejo → el GET lo entrega (front descarta);
     DELETE idempotente; **adversarial: nada escribe en `pipeline_config` ni fabrica `StrategyProfile`**;
     migración aplica y revierte (subprocess alembic sobre sqlite). Smoke JS (pendiente): guardar → cambiar de
     equipo/navegador → abrir ficha (snapshot cargado, chip "estimación", sello visible); precedencia (local
     gana sobre server); recalcular estudio → snapshot viejo NO se restaura (nota discreta); borrar guardada.
     **Pendiente: smoke de navegador OBLIGATORIO.**
