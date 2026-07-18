# AUDITORÍA TOTAL DE LUXY — Fixture de Oro + verificación matemática E2E

> Fecha: 2026-07-18 · Rol: auditor adversarial (read-only) · Entregable de
> código: `tests/test_luxy_golden.py` (26 tests) + etiqueta de universo en la
> línea CRUDO de `nt_riesgo` (cambio aprobado). Los fixes de los hallazgos NO
> se implementan aquí — van a triage del arquitecto.

## Veredicto en una línea

**La aritmética del motor es sana y el pipeline completo reproduce números
calculados a mano en cada eslabón** (integrar → estudio → recalcular →
aplicar → determinismo), **PERO el modelo de la pierna-más-profunda-que-el-
stop es OPTIMISTA en las 4 rutas de simulación** (hallazgo del operador,
confirmado y cuantificado: GC +$5,167 fantasma; NQ +$5,901 — su OOS honesto
es NEGATIVO), y hay hallazgos menores de presentación/bordes.

---

## A. FIXTURE DE ORO — `tests/test_luxy_golden.py` (permanente)

CSV LuxAlgo sintético de 34 trades (2 filas/trade, largos y cortos,
ganadores/perdedores, 1 outlier de frontera de roll, 1 trade ABIERTO al
final) + HOLC 5m construido para que **ATR(14) = 5.0 exacto** (TR constante),
**offset TZ = +60 min detectable** (escalera de nivel + micro-onda de
cierres) y **todas las derivaciones caigan en números verificables con
lápiz** (tablas a mano en el propio archivo). Todo por el PIPELINE REAL en un
`MOTOR_RIESGO_DIR` temporal:

| Eslabón | Asserts (todos con valores a mano) | Estado |
|---|---|---|
| `nt_riesgo integrar` | cuadre al dólar (+2550.00 exacto), sha256 export==master, TZ +60, sanity 33/34, $/punto inferido==50 ok, contención LX-12 = 97.1%, outlier LX-13 = exactamente #7 con gap −200.0 ticks, ATR 5.0 en las 34 entradas | ✅ |
| `mr_luxy` estudio | fila Crudo (n34, net 2550, PF 1.45, WR 67.6, DD 4000, peor −4000, 11 perdedores); palancas in-sample (backstop $2000=40pts, TP fallback 15×/15×, escalera [5,3,2]@0/2/4×, suelo 2.86, BE None, lado None); **Crudo+ simulado a mano trade a trade** (n33, net 6331, PF 4.49, WR 87.9, DD 1650); fila OOS espejo (n10, net 2017, PF 41.34, DD 50); split exacto 24/10 (23/10 simulables) | ✅ |
| LX-7 / LX-14 | OOS n=10 con 1 perdedor → semáforo **⚪ sin_veredicto / pocos_perdedores** (jamás 🟢); n<10 → ⚪ muestra_chica; gate LX-11 hereda → ÁMBAR | ✅ |
| `evaluate_overrides` | USD→pts/×ATR a mano ($3000→60pts→12×, $2500→10×, $750→3×, $1250→5×); teselas base/config/oos simuladas a mano (2550 / 2092 / 1031); señales sin flip ni mejora>3× | ✅ |
| `config_from_overrides` | aplicable llave por llave (exactamente {backstop_points, tp_nominal_*, entry_reserve, scale_entry}); **jamás llaves de kill-switch** (mode/dry_run/traderspost/status); variante FX 6J: $570→snap 91 ticks, $4→sub-tick→`_no_representable` (jamás 0) | ✅ |
| R-T10 | la fila OOS deriva DISTINTO (backstop OOS = None) y el aplicable sale SIEMPRE de la in-sample; mutilando la in-sample, ninguna palanca cae en silencio a la OOS | ✅ |
| Determinismo | dos corridas de `run_for_clave` → JSON idéntico byte a byte | ✅ |
| Hallazgo D | PIN del modelo vigente (−$1100 con +$100 fantasma) + **xfail estricto** del contrato correcto (−$1200, exit≈fill) — al aplicar el fix, el xfail pasa y el PIN se borra | ✅ (1 xfail esperado) |

---

## B. HALLAZGO D (del operador) — pierna más profunda que el stop

### Confirmación (evidencia línea por línea)

En un trade stoppeado, toda pierna llenada a `d` con `d·ATR_t > b_pts` se
valora saliendo AL PRECIO DEL STOP → pnl = +(d·ATR_t − b_pts) pts POSITIVO.
En ejecución real el stop ya reventó cuando la pierna llena → salida ≈ al
precio del fill → pnl ≈ 0 − gap/slippage. **Cuatro rutas comparten la
fórmula optimista:**

1. `scripts/mr_sims.py:409` — `ladder_outcome`: `pnl_pts = -(b_pts + hc.gap_pts - d * st.atr_pts)` (positivo si `d·atr > b_pts + gap`).
2. `scripts/mr_sims.py:867` — `_eval_proteccion` (estudio de protección): misma fórmula con `stop_pts`.
3. `scripts/mr_sims.py:1265` — `deep_leg_stress` (¡el estrés de la pierna profunda mide su contribución con el MISMO sesgo que pretende auditar!).
4. `scripts/mr_luxy.py:180 + 223` — `_luxy_exit_atr` resuelve el stop como `ex = −sl_atr` y `luxy_outcome` paga a cada pierna `(ex + d)·ATR` → idéntico `+(d−sl_atr)·ATR`. **Esta ruta alimenta Crudo+/OOS/semáforo/gate de TODA la flota.**

Agravante de coherencia interna: el peor-caso del Portafolio **ya modela esto
bien** — `app/services/position_sizing.py:63` (`worst_case_loss`) recorta con
`max(0.0, sl − L)` ("una pierna más profunda que el SL no llena antes de
parar → aporta 0, nunca negativo"). El motor de estudios contradice a la
regla 3 del Portafolio.

### Cuantificación (flota real, estudios del 2026-07-18, server, read-only)

fantasma = Σ sobre trades stoppeados de w·(d·ATR_t − b_pts)·ppt en piernas
llenadas con d·ATR_t > b_pts (cota INFERIOR del sesgo: el modelo honesto
restaría además gap/slippage).

| Clave | b_pts (×ATR med) | piernas>stop | trades stoppeados / piernas fantasma | **$ fantasma total** | $ fantasma OOS | Crudo+ net → honesto | OOS net → honesto |
|---|---|---|---|---|---|---|---|
| **GC_ContraNormal_ST_WeakConf** | 30 pts (5.28×) | C3 7.21× | 20 / 16 | **+$5,167** (peor trade +$1,246) | +$964 | 11,858 → **6,690 (−44%)** | 4,845 → 3,881 |
| **NQ_ConfAny_ST_TC** | 100 pts (3.68×) | C3 6.2× | 14 / 15 | **+$5,901** (peor +$1,780) | **+$4,305** | 10,542 → **4,641 (−56%)** | 2,066 → **−2,239 (¡NEGATIVO!)** |
| ES_ConfNormal (19.3×), ES_CS (11.1×), RTY (52×), 6J (160×) | — | ninguna | 0 fantasma | $0 | $0 | sin sesgo | sin sesgo |
| 6E | — | — | — | estudio sin backstop/escalera | — | — | — |

Lecturas:
- **GC**: confirma el ojo del operador. La condición dura "GC no se
  promociona sin resolver la geometría" queda respaldada con número.
- **NQ**: hallazgo NUEVO y más grave — el PF OOS 2.22 de NQ está inflado por
  $4,305 fantasma; el neto OOS honesto es −$2,239. El semáforo/gate de NQ
  está descansando sobre dinero que no existe. (NQ hoy no está en la flota
  activa de 7 — FILTROS-OFF — pero su estudio persiste y es aplicable.)
- El resto de la flota está limpio porque sus backstops quedan MÁS HONDOS que
  toda pierna (la geometría sana hace el sesgo inalcanzable).

### Fix propuesto (NO implementado — triage del arquitecto)

Opción A (preferida, coherente con `worst_case_loss`): en las 4 rutas,
cuando la pierna llenada queda más allá del stop (`d·atr ≥ b_pts`), modelar
exit≈fill: `pnl_pts = min(-(b_pts + gap - d·atr), 0.0) - gap` — es decir,
recortar a ≤ 0 (la pierna jamás aporta positivo en un trade stoppeado) y
cargarle el gap. Opción B (más dura): excluir del grid/derivación toda config
con pierna > backstop, con aviso. En ambos casos: **re-derivar los estudios
aplicados** (GC y NQ cambian materialmente; el gate ámbar de geometría
stop-en-escalera ya existente pasa de aviso a corrección) y al aplicar el
fix, voltear los dos tests del fixture de oro (borrar el PIN, des-xfail el
contrato correcto — así quedó cableado).

---

## C. AUDITORÍA DE CÓDIGO (read-only)

### C.1 Universos por métrica (patrón del 07-18: número correcto, universo no declarado)

Tres universos: **U1** listado completo · **U2** simulable (ATR real, sin
LX-13) · **U3** participantes/ventana. Hallazgos (todo lo no listado declara
bien — p. ej. banner de muestra LX-5, semáforo "OOS validada", filas
Crudo/Crudo+/OOS de table3):

| # | Superficie | Problema | Evidencia | Severidad |
|---|---|---|---|---|
| U-1 | CLI `nt_riesgo calcular`, línea "CRUDO (señal, sin gestión)" | Es U2 (linea_base sobre sts) pero se llama igual que el "Crudo" U1 de Luxy; sin etiqueta | `nt_riesgo.py:918-925` | **Corregida en este lote** (etiqueta "universo SIMULABLE N trades" — cambio aprobado) |
| U-2 | Tarjetas KPI CRUDO de riesgo.html (v1) y comparación crudo→config | 6 tarjetas sin "N de M trades"; la participación 100%→N% no explica la causa | `app/templates/riesgo.html:174-194, 325`; `routes_riesgo.py:206-214` | Media (v1 muere en L7b — decidir si vale el lote) |
| U-3 | La palabra "Crudo" significa U1 en Luxy (`mr_luxy.py:832`) y U2 en el CLI v1 (`nt_riesgo.py:919`) | mismo nombre, dos universos entre superficies | ambas líneas | Media (mitigada por U-1) |
| U-4 | `mr_report.py` fila "CRUDO (señal, sin gestión)" | mismo texto U2 sin etiqueta en el .md/csv de la corrida | `mr_report.py:382,748` | Baja (entregable offline) |

### C.2 Doble-fuente (post-BUG-HONESTIDAD)

Verificado con fuente única ✅: PF/net/DD/peor/WR (`lab_metrics.aggregate` →
`metrics_usd`), semáforo (`robustez_semaforo`, la lee gate y front),
conversión USD→pts (`fx_levers`), TZ (`detect_tz_offset`), percentiles
(`pctl`), cancel_after (`suggest_cancel_after`), zonas (`sesiones_et`).

| # | Número | Divergencia | Evidencia | Severidad |
|---|---|---|---|---|
| D-1 | **Modelo del stop en pierna profunda** | los sims (4 rutas, sección B) vs `worst_case_loss` del Portafolio (recorta a 0) — dos verdades sobre la MISMA geometría | `mr_sims.py:409,867,1265` · `mr_luxy.py:223` vs `position_sizing.py:63` | **Alta** (es el hallazgo D) |
| D-2 | ATR mediano | `backstop_sweep` lo computa sobre su ventana (`mr_sims.py:229`), el dashboard/evaluate sobre TODOS los sts (`mr_luxy.py:618`, `mr_luxy.py:1480`) — las conversiones $↔×ATR del panel usan una mediana distinta a la de la derivación in-sample | líneas citadas | Baja (mismo estimador; ventanas distintas por diseño, sin declarar) |
| D-3 | Participación % | 3 implementaciones idénticas sin helper común (`mr_sims.py:478,902`, `mr_luxy.py:380`) | líneas citadas | Baja (estilo; hoy idénticas) |
| D-4 | Suelo MAE p95 | `mr_luxy._winners_mae_p95:259` vs inline en `proteccion_study` (`mr_sims.py:949`) — misma fórmula duplicada | líneas citadas | Baja |

### C.3 Bordes

| # | Borde | Comportamiento verificado | Evidencia | Severidad |
|---|---|---|---|---|
| B-1 | **Trade abierto en el export** (el ~$160 del operador) | parsea SIN salida y su PnL PROVISIONAL entra como final en Crudo, en el universo simulable y — por ser el más reciente — en la fila OOS y el semáforo. Sin marca ni etiqueta en ninguna superficie | `lab_analyze.py:189-205` (exit puede faltar); fijado en `test_trade_abierto_parsea_con_pnl_provisional` | **Media** (un trade abierto grande puede teñir la OOS; propuesta: contarlo/etiquetarlo "provisional" o excluirlo del split como la cola estimada) |
| B-2 | Lista con 0 perdedores | PF None; con n≥10 el semáforo da ⚪ pocos_perdedores ✅; PERO en la ruta retrocompat (n_perdedores=None) un 100%-ganador cae a 🔴 (pf None → rojo) | sonda 2026-07-18; `mr_luxy.py:541-544` | Baja (conservador, no miente-verde; incoherencia estética) |
| B-3 | 1 solo trade | sin excepción: split 1/0, OOS n=0 → ⚪; degradado honesto sin barras | sonda 2026-07-18 | OK |
| B-4 | Master re-integrado con MENOS trades | advierte en CLI y persiste `superconjunto_ok=False` en manifest… y ahí muere: **cero referencias en `app/`** — la ficha/estudio Luxy no lo muestran jamás | `nt_riesgo.py:604-614,680-681`; grep app/ sin hits | **Media** (un export parcial re-integrado pasa silencioso en la UI) |
| B-5 | $/punto ausente (instrumento fuera de tabla y export sin `valor`) | `integrar` persiste `usado: None` sin bloquear → `mr_luxy._load_master` revienta en `float(None)` (`mr_luxy.py:1253`) y `calcular` dividiría por None | `nt_riesgo.py:594-602` sin guarda | Baja (irreal con exports LuxAlgo completos; fail-noisy, no silencioso) |
| B-6 | HOLC con hueco interior | trades del hueco quedan sin ATR (excluidos honesto) pero el banner los rotula "previos al inicio del almacén" (el clasificador solo distingue cola/inicio) | `mr_luxy.py:915-919`; `muestra_banner` | Baja (etiqueta imprecisa, conteo correcto) |
| B-7 | `cutoff_ts` del split | se indexa `trades[len(sts_in)]` — len de SIMULABLES contra la lista COMPLETA: con outliers/sin-ATR en el tramo in-sample apunta a un trade AÚN in-sample (en el fixture: al #24, no al #25) | `mr_luxy.py:780-784` | Baja (informativo; `cutoff_i` del dashboard sí es coherente con sts) |
| B-8 | `evaluate_overrides` base card | no fija `participacion_pct=100` en el crudo (la tesela base lleva part=None; el estudio sí lo fija) | `mr_luxy.py:1527` vs `mr_luxy.py:834` | Cosmética |

---

## D. Estado de la suite

`tests/test_luxy_golden.py`: **25 passed + 1 xfailed** (el xfail estricto es
el contrato del hallazgo D, por diseño). Suite completa: verde (ver cierre de
jornada — corrida única con los tests nuevos).

## E. Cola de triage propuesta (orden)

1. **FIX-PIERNA-STOP** (D-1/hallazgo D): recorte exit≈fill en las 4 rutas +
   re-derivación de estudios (GC/NQ cambian) + voltear los 2 tests del oro.
2. **B-1**: política para el trade abierto (etiquetar provisional / excluir
   del split OOS).
3. **B-4**: subir `superconjunto_ok` a la ficha Luxy (banner ámbar).
4. U-2/U-3 (si v1 sobrevive a L7b) · B-6/B-7/B-8 y D-3/D-4 como limpieza
   oportunista en lotes que toquen esos archivos.
