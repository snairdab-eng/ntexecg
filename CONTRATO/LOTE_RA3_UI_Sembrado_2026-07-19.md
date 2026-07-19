# LOTE RA-3 — UI + sembrado del re-armado (el interruptor supervisado)

> 2026-07-19 · El RearmJob existe e inerte (RA-2b/5-6); RA-3 es la única
> puerta de encendido. Lote JS: **smoke del operador ANTES de commit**
> (pasos abajo). Protocolo §0.

## 1. Sembrado vía Aplicar (Luxy)

**Server-side, jamás confiar el guard al front**
(`routes_strategies._rearm_desde_veredicto`, cableado en
`_luxy_palancas_ctx` — preview y aplicar pasan por la misma puerta):

- Cualquier bloque `rearm` que venga del cliente se **DESCARTA** y se
  reconstruye del **veredicto RA-0v3** del panel Piernas (fuente única). El
  front solo manda la intención: `rearm_incluir` (checkbox).
- **🟢 recomendado** ⇒ checkbox "incluir re-armado (max_ciclos N del
  veredicto)" — **DEFAULT DESMARCADO**. Marcado ⇒ el aplicable lleva
  `scale_entry.rearm{enabled:true, max_ciclos/k_sobre_c0/umbral_atr del
  veredicto, min_antes_cierre_min:30, timeframe:"5m"}` y el gate LX-11 lo
  marca **ÁMBAR** ("re-armado de piernas ON" — ya existía del sub-paso 1;
  jamás verde con re-armado). Marcar el checkbox **re-pide el preview** (el
  aplicable y el gate cambian).
- **🔴/⚪/ausente** ⇒ la sección aparece **DESHABILITADA** con el motivo, y
  `rearm_incluir:true` recibe **409** server-side en preview Y en aplicar.
- Constantes `n/s` del veredicto ⇒ defaults conservadores P2 (max_ciclos 1 =
  OFF efectivo).

**Decisión pedida (sin checkbox): se siembra con `enabled:false`** —
sembrar ≠ encender. Justificación: la config aplicada registra QUÉ recomendó
el estudio en el momento de aplicar (reproducibilidad y deriva visibles en
Config) sin encender nada; no abre atajo alguno porque encender exige
checkbox+gate (única puerta) y Config solo APAGA. Con veredicto no-🟢 no se
siembra nada (constantes de un veredicto no recomendado no tienen lugar en
la config).

Nota de alcance: el CLI directo (`mr_luxy --evaluar` con un `rearm` a mano)
no pasa por este guard — es shell del operador en el server, fuera del
modelo de amenaza del front; la ruta WEB (la única expuesta) descarta y
reconstruye siempre.

## 2. Apagado sin fricción (Config)

`POST /ui/strategies/{id}/rearm/off` (form de un clic junto a la sección
Scale Entry / C1 read-only): `enabled:false` conservando las constantes +
`AuditLog REARM_DISABLED{actor:"operador", old/new}` + flash. **Encender
desde Config NO existe** (probado: `/rearm/on` ⇒ 404/405; guarda de render:
el template no contiene esa ruta). Una puerta de entrada (Aplicar+gate), dos
de salida (este toggle y el propio Aplicar re-sembrando en false).

El bloque de Config muestra el estado: `● ON — constantes + botón Apagar` /
`○ OFF — constantes sembradas; se enciende solo vía Aplicar` / `sin sembrar`.

## 3. Visibilidad de ciclos en Posiciones (/ui)

Para posiciones con rearm sembrado, fila secundaria read-only:
`re-armado: C2·working·c2 · C3·dead·c1 · última: REARM_KILL · R-RA6` —
piernas de `risk_plan_json["rearm"]` (estado ilegible ⇒ se dice) + la última
acción `REARM_*` del AuditLog por `account:symbol`. Sin rearm sembrado, la
fila no existe.

## 4. Tests (`tests/test_ra3_ui.py`, 17) + guardas

- Unidad del guard: sembrado exacto desde veredicto (con inyección de
  `rearm` del cliente descartada) · sin checkbox ⇒ enabled:false · n/s ⇒
  409 + sección deshabilitada · veredicto ausente ⇒ 409 · constantes n/s ⇒
  defaults P2.
- Endpoints (harness LX-15, aplicable REAL vía config_from_overrides):
  preview 🟢+checkbox ⇒ rearm en aplicable + gate ÁMBAR con trigger
  re-armado · 🟢 sin checkbox ⇒ enabled:false y gate VERDE limpio · n/s ⇒
  deshabilitado + 409 en preview y aplicar · inyección directa descartada ·
  **aplicar escribe `scale_entry.rearm` sin tocar kill-switch**
  (mode/dry_run/traderspost_enabled intactos — R-T10/NX-11).
- Apagado: un clic conserva constantes + REARM_DISABLED con old/new · sin
  sembrar ⇒ no-op honesto · `/rearm/on` no existe.
- Dashboard: ciclos + última acción renderizados · sin rearm ⇒ sin fila ·
  legs ilegibles ⇒ "estado ilegible".
- Guardas de render: `rearmOn:false` (default desmarcado), `rearm_incluir`
  en ambos fetches, `load(true)` al marcar, sección deshabilitada, botón
  Apagar sin ruta de encendido, fila del dashboard.

Suites adyacentes verdes: 144 (lx15 JS+render, dashboards, rearm 5/6,
visual-exclusiones, web).

## SMOKE del operador (pasos exactos, antes del commit)

1. **Estrategia con veredicto 🟢** (panel Piernas): Luxy → Recalcular →
   "Aplicar estas palancas…" → el preview muestra la sección Re-armado con
   el checkbox DESMARCADO y la nota "sembrar ≠ encender".
2. Marcar el checkbox → el preview se RE-CARGA solo; el gate pasa a ÁMBAR
   con "re-armado de piernas ON"; el diff muestra `scale_entry` con `rearm`.
3. Aplicar con el checkbox de riesgo → en Config aparece
   "Re-armado de piernas: ● ON — max_ciclos N…" con el botón rojo Apagar.
4. **Apagar re-armado** (un clic) → flash verde, estado "○ OFF — constantes
   sembradas…", y en /ui/audit un `REARM_DISABLED{operador}`.
5. **Estrategia con veredicto ⚪/🔴** (p. ej. GC): el preview muestra
   "Re-armado deshabilitado — veredicto …" SIN checkbox.
6. Con una posición abierta de estrategia con rearm sembrado: /ui →
   fila "re-armado: C2·working·c1…" bajo la posición.
7. `node --check` implícito: las guardas JS de la suite ya verifican
   sintaxis/estructura; visualmente confirmar que el modal abre y cierra
   sin errores de consola.

## Observación de infra (para el arquitecto, no bloquea — diagnóstico afinado)

Flakes intermitentes SOLO en tests pesados gated de datos reales durante los
runs completos de hoy (suite ya en ~1444 tests): `test_ra0_study[6E]` 1×,
`test_lab_consistency` (errors de fixture) 1×, y
`test_riesgo_ui::test_aceptacion_es_end_to_end` 3× — todos VERDES aislados y
la corrida final completa quedó VERDE (1437/7). El de aceptación-ES encaja
con TIMEOUT de presupuesto: su `calcular` REAL corre en subproceso con
polling de 240 s (test_riesgo_ui.py:531-537) y bajo la carga del run
completo en este host (11.7 GB, ~3.7 libres) a veces no llega. NO es lógica
de los lotes nuevos (descartado: subconjunto ordenado con los archivos
nuevos delante = verde; los tests afectados no tocan código RA-*).
Sugerencia FIX-FLAKE-2 (lote aparte, decisión del arquitecto): subir el
presupuesto de polling del test de aceptación y/o agrupar los gated de datos
reales en corrida serial aparte — en la línea del FIX-FLAKE del cuelgue
aiosqlite.

## Pendiente

- Smoke del operador (arriba) → revisión del arquitecto → **commit conjunto
  RA-2b/5-6 + RA-3** (el job y su interruptor viajan juntos).
- Tras el deploy: encender en UNA estrategia 🟢 en demo y observar
  REARM_LEG/KILL/SKIP en el audit (el objetivo declarado del diseño).
