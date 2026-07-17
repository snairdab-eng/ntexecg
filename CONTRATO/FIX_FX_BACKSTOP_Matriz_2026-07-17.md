# FIX-FX-BACKSTOP — matriz de representabilidad y fix único (2026-07-17)

**P1 — palanca espejismo en la conversión USD→puntos.** Evidencia del operador
(6J, apply real 2026-07-17): slider **SL −$570** → config viva
**`backstop_points = 0.0`**. La conversión `$570 / ppt 12.5M ≈ 4.56e-5 pts` se
colapsaba por un `round(_,2)` fijo pensado para índices, en el camino
palancas→aplicable.

## Catálogo (Symbol Mapper / `mr_report.TICK_SIZE`)

| Instr | tick_size | tick_value | ppt = tv/ts |
|-------|-----------|------------|-------------|
| ES | 0.25 | 12.50 | 50 |
| NQ | 0.25 | 5.00 | 20 |
| RTY | 0.10 | 5.00 | 50 |
| YM | 1.00 | 5.00 | 5 |
| GC | 0.10 | 10.00 | 100 |
| CL | 0.01 | 10.00 | 1 000 |
| 6E | 0.00005 | 6.25 | 125 000 |
| 6J | 0.0000005 | 6.25 | 12 500 000 |

## Matriz: palanca × instrumento → ¿dónde redondea, a qué rejilla, colapsa?

Dos clases de palanca en el camino de aplicar:

- **`backstop_points`** está en **PUNTOS DE PRECIO** → rejilla natural = **tick**.
  `pts = usd / ppt`. El `round(_,2)` (rejilla fija 0.01) colapsa a 0 cuando
  `usd/ppt < 0.005`, i.e. `ppt > 200·usd`. Para $570 eso es **6E y 6J**.
- **`tp_nominal_*`, `levels` (C2/C3), `c1_depth`** son **múltiplos ×ATR
  ADIMENSIONALES** (`usd / ppt / atr_med`). Como `ATR_usd = ppt·atr_med`, el
  cociente = `usd / ATR_usd`: **independiente del ppt**. Por eso los `[8.0, 3.6]`
  de 6J son idénticos a los de ES y **nunca colapsan** con `round(_,2/4)` para
  valores de orden ~1..16.

`backstop_points = usd/ppt` con **$570** de referencia, `round(_,2)` (VIEJO):

| Instr | usd/ppt | round(_,2) VIEJO | ¿colapsa? | en ticks (tick real) |
|-------|---------|------------------|-----------|----------------------|
| ES | 11.4 | 11.4 | no | ~46 ticks |
| NQ | 28.5 | 28.5 | no | ~114 ticks |
| RTY | 11.4 | 11.4 | no | ~114 ticks |
| YM | 114.0 | 114.0 | no | ~114 ticks |
| GC | 5.7 | 5.7 | no | ~57 ticks |
| CL | 0.57 | 0.57 | no | ~57 ticks |
| **6E** | **0.00456** | **0.00** | **SÍ ⛔** | ~91 ticks |
| **6J** | **0.0000456** | **0.00** | **SÍ ⛔** | ~91 ticks |

**Por qué unos caminos sí y otros no:** el colapso ocurre SOLO donde la cantidad
es **precio** (backstop) y el instrumento es **FX** (tick minúsculo). Los ×ATR son
adimensionales y de orden ~1, así que la rejilla 0.01/0.0001 nunca los aplasta —
ése es el motivo exacto de que los `levels [8.0, 3.6]` de 6J sobrevivieran mientras
el backstop moría.

## Fix (una sola función, rejilla del tick — patrón FIX-D2)

`scripts/fx_levers.py` — núcleo ÚNICO, reusa `round_to_tick` y `TICK_SIZE`:

- `snap_puntos(pts, tick)` / `usd_a_puntos(usd, ppt, tick)` → `(pts_en_rejilla,
  representable, crudo)`. Snap al tick; `representable = |crudo| ≥ 1 tick`.
- `usd_a_mult_atr(usd, ppt, atr_med, tick)` → `(mult, representable)` (misma
  prueba, `mult·ATR = usd/ppt`).
- `fmt_pts(activo, pts)` → display FX en ticks.

**Caminos migrados a la función única (se eliminó el `round(_,2)` disperso):**

| Sitio | Antes | Ahora |
|-------|-------|-------|
| `config_from_overrides` (palancas operador) | `round(usd/ppt,2)` etc. | `usd_a_puntos`/`usd_a_mult_atr` + fail-honest |
| `activacion_from_study` (reco estudio Luxy) | `round(b_pts,2)` | `snap_puntos` + fail-honest |
| `derive_levers` (b_pts) | `round(b_pts,2)` (colapsaba en origen) | precisión plena; snap en aplicar |
| `mr_sims` reco (motor offline, R-T9 sin tick) | `round(b_opt/ppt,2)` | precisión plena; el tick lo pone el despacho (`sl_tp_calculator.round_to_tick`) |

## Fail-honest en el borde (< 1 tick)

Conversión que produce `< 1 tick` → **no se escribe** (jamás un 0 colapsado en
silencio): la llave se OMITE y viaja como aviso en `_no_representable` (clave
privada — el merge solo copia llaves conocidas → nunca persiste). El preview y el
apply lo suben ruidoso (`no_representable` + aviso ⛔).

## Display FX

`_diff_aplicar` muestra el backstop con `fmt_pts(activo, ·)`: FX en **ticks**
(`91 ticks (4.55e-05 en precio)`), índices en `pts` — nunca "0 pts" ni notación
científica cruda.

## Verificación en el server

`scripts/audita_palancas_fx.py` (read-only) recorre las configs VIVAS y marca
cualquier `backstop_points` colapsado (==0 / sub-tick / fuera de rejilla) o
`levels`/`c1_depth` a 0 con cantidad>0 — para detectar si 6J no es la única
envenenada hoy. El operador lo corre y decide correcciones (no escribe nada).

## Tests

`tests/test_fix_fx_backstop.py` — parametrizado ES/GC/6E/6J con ppt/tick reales:
núcleo `fx_levers`, `config_from_overrides` por instrumento, colapso→omite+aviso,
USD→pts→config→payload (SL final en rejilla, bracket P0 válido), display FX, y la
**regresión con el número exacto del hallazgo (570 / 12.5M → ~91 ticks, jamás 0)**.
