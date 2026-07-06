# Auditoría Fase A — UI, código muerto y seguridad MR-5/Riesgo · 2026-07-06

> Solo reporte + backlog (Fase A). La ejecución es Fase B, por lotes
> P0→P1→P2→P3, deteniéndose entre lotes. **Candados de la Fase B:**
> fail-closed siempre; kill-switch (L1 `global_mode`, NX-01) y `symbol_busy`
> (L3) intocables; el ENGINE del Lab (`lab_metrics`/`lab_analyze`/
> `lab_manifest`) NO se borra — solo su UI; `ConflictLog` se conserva.

---

## 1. Mapa de uso pestaña por pestaña

| Pestaña | Ruta / archivo | Veredicto | Evidencia |
|---|---|---|---|
| Dashboard | `app/web/routes_dashboard.py` (217) | **CONSOLIDAR** con Analítica | Contadores de HOY (recibidas/aprobadas/bloqueadas/enviadas, `routes_dashboard.py:36-59`) + decisiones recientes (`:74-92`) — el mismo universo de datos que Analítica con otra ventana. ⚠ Sus **partials son load-bearing app-wide**: `base.html:72` consume `/ui/partials/bridge-badge` en el navbar — conservar partials aunque se fusione la página. |
| Estrategias | `routes_strategies.py` (1308) | **MANTENER** (core) | CRUD + config completa (guardrails/windows/filters/regime/sltp/scale-entry/profiles/status). Puente a Riesgo añadido (`strategy_detail.html:17-24`). |
| Señales | `routes_signals.py` (295) | **MANTENER** | Navegación de señales + detalle con `pipeline_execution_json` (audit operativo). |
| Analítica | `routes_analytics.py` (211) | **CONSOLIDAR** (absorbe Dashboard) | Agregados N días: outcomes, block reasons, bloqueos por nivel, por-estrategia con alias canónico NX-24 (`routes_analytics.py:38-120`). Dashboard = caso particular (days=1) + operacional. Unificar en UNA página con selector de rango + fila operacional (bridge/entregas). |
| Lab | `routes_lab.py` (562) + `lab.html` (768) | **MANTENER** (ajuste 2026-07-06: la UI se conserva y se mejorará; veredicto original era deprecar-UI) | La media de filtros no dio valor (Directiva 2 del Motor de Riesgo). El ENGINE es load-bearing para Riesgo: `nt_riesgo.py:60-77` importa `lab_metrics` + `lab_analyze` (parser/HOLC/TZ/pullback), `mr_sims.py:48-49` (`aggregate`, `pctl`), `routes_riesgo.py:43-44` (`routes_lab.load_manifest`, `lab_manifest.csv_instrument`). ⚠ **Orden de retiro obligatorio**: mover `load_manifest`/`LAB_DIR` (`routes_lab.py:53,66-74`) y el upload compartido a un módulo común ANTES de borrar `routes_lab`; si no, Riesgo se cae. La caché `lab_features_*.json` la regenera `lab_analyze` que también alimenta el visor — el CLI del Lab (`--all-summary`) se conserva. |
| Riesgo | `routes_riesgo.py` (485) | **MANTENER** (nuevo) + P1 abajo | Front-end del motor; cero segundo cálculo (subprocesos `nt_riesgo`). |
| Posiciones | `routes_positions.py` (116) | **MANTENER** | Visibilidad del estado que alimenta `symbol_busy` (L3). |
| Activos | `routes_assets.py` (243) | **MANTENER** | AssetProfile es load-bearing: ConfigResolver funde sesiones L2, atr_period/timeframe, max_quantity (`config_resolver.py:138-156`). Nota: sus `sl_atr_multiplier`/`tp_atr_multiplier` pierden peso donde haya backstop (el backstop los reemplaza en L5) — no borrar, documentar. |
| Symbol Mapper | `routes_symbol_map.py` (104) | **MANTENER** | Regla crítica #3 (lookup directo en DB). |
| Templates | `routes_strategy_templates.py` (68) | **DEPRECAR** — se arrastra, confirmado | Solo list+create (`:17,:29`). **Nada lo consume**: `/ui/strategies/new` carga `templates_list` al contexto (`routes_strategies.py:147-150`) pero `strategy_form.html` tiene **0 referencias** a template (grep vacío); `template_id` solo se propaga en clone (`routes_strategies.py:1216`). Crear plantillas que nadie usa = deuda. Retiro: quitar nav + rutas + template; conservar el modelo `StrategyTemplate` y la columna (datos históricos, sin migración destructiva). |
| Settings | `routes_settings.py` (125) | **MANTENER** | GlobalProfile = kill-switch UI. |
| Audit | `routes_audit.py` (50) | **MANTENER** | Trazabilidad. |
| (API JSON) | `routes_api.py` (331) | **MANTENER** | La consumen los CLIs de calibración (`routes_api.py:1-15`). |

## 2. Barrido de código muerto

**Servicios**: todos con consumidores vivos (verificado por imports:
`performance_tracker`→webhook `:219,:313`; `results_import`→`import_results`;
`regime_features`→`hmm_trainer`; `deduplicator`/`exit_manager`/`forced_exit`/
`bar_store`/`dispatch_profiles` en uso). **Sin huérfanos.**

**Templates HTML**: todos renderizados por alguna ruta (barrido completo;
`base.html`/`_macros.html` son includes). **Sin huérfanos.**

**Modelos**: `ConflictLog` (`app/models/conflict_log.py:15`) — DORMIDO (solo
migraciones + `__init__`; ningún writer/reader). **CONSERVAR** (candado del
operador; es el hueco previsto para conflictos de símbolo).

**Scripts** (`scripts/`, 45): clasificación de los sospechosos —

| Grupo | Scripts | Veredicto |
|---|---|---|
| Sims pre-motor (superseded por `mr_sims`/`nt_riesgo`) | `sim_sl_matrix`, `sim_scaled_entry`, `sim_sizing`, `sweep_matrix`, `calibrate_sl_from_trades` | **DEPRECAR** (mover a `A REVISAR/` o borrar tras confirmar; el Motor de Riesgo cubre SL/escalera/sizing con OOS y corte de cancel_after, que estos no tienen) |
| Era Anexo 21 / política v1 (one-shot ya aplicados) | `apply_anexo21_demo`, `apply_profile_policy_v1`, `revert_asset_profiles_v1`, `diag_profiles`, `compare_filter_decisions`, `eval_quality_filters` | **DEPRECAR/archivar** (históricos; su efecto vive en la DB) |
| Appliers vivos (features L4/L5 activables) | `apply_quality_filter`, `apply_regime_gate`, `apply_cancel_after`, `set_scale_execution`, `apply_scale_entry_design_v1` | **MANTENER** (los CLI auditados de aplicar) |
| Ops/diagnóstico | `test_buy_all`, `preview_scaled`, `trade_forensics`, `audit_signal_flow`, `eval_strategy_battery`, `rollover_alert`, `backup_db`, etc. | **MANTENER** |
| Motor + Lab engine | `nt_riesgo`, `mr_sims`, `mr_report`, `lab_analyze`, `lab_manifest`, `pullback_timing` | **MANTENER** (producto) |

**Working tree** (no código): `es_server_tmp.json` (temporal en raíz),
`ClaudeCodeTV_290626/`, `AnalisisClaudeTV/`, `A REVISAR/` — sin trackear;
limpieza del operador, no de código.

## 3. Seguridad MR-5 + pestaña Riesgo (pasada adversarial)

### 🔴 P0-1 — SL/TP pueden cruzar CERO con backstop mal escalado (misma clase que NX-05)
`backstop_points` es "unidad de precio natural" y NO se valida contra la
escala del instrumento. **Demostrado en vivo**: 6E a 1.083 con
`backstop_points: 90` (el valor de ES pegado por error) →
`passed=True, sl_price = −88.917`; y el fallback de TP sin ATR en corto da
`tp_price = −88.917`. Una orden desnuda disfrazada llega al payload
(`sl_tp_calculator.py:114-118` calcula sin cota inferior; `:81-97` solo
valida `entry_price`). El mismo riesgo aplica al corto: `SL = señal +
backstop` no revienta, pero `TP = señal − backstop` sí.
**Fix (fail-closed)**: en `SLTPCalculator`, `sl_price <= 0` o `tp_price <= 0`
→ `passed=False, reason="bracket_price_invalid"`; más guarda de proporción
(`backstop >= entry_price` → BLOCK: un stop que cruza cero nunca es válido).
Tests adversariales con el caso 6E real.

### 🟠 P1-SEC-1 — `integrar` desde la pestaña sin lock por clave
`riesgo_upload` corre el subproceso awaited sin serializar
(`routes_riesgo.py:334-341`): dos subidas simultáneas de la misma estrategia
compiten por `master.csv`/`manifest.json` (last-writer-wins silencioso).
Un solo operador lo hace improbable, pero el lock es barato (patrón JOBS).

### 🟠 P1-SEC-2 — deriva backstop ↔ `max_quantity`/perfiles
`short_size_factor` y la escalera reparten cantidades en el builder
(`payload_builder.py`), DESPUÉS de las validaciones de `scale_entry`; el
tope `max_micro_contracts` se respeta (reducción nunca sube), y la posición
registra la cantidad REAL enviada (`webhooks_luxalgo.py:452,475`) ✓. Sin
hueco encontrado — dejar test de regresión que lo fije (ya existe parcial).

### ✅ Verificado sin hallazgos (el rigor del kill-switch, aplicado)
- **Kill-switch intacto**: L1 `global_mode` corre antes de L5
  (`filter_pipeline.py:332-335`); test explícito con backstop
  (`test_backstop_mr5a.py::test_kill_switch_intacto_con_backstop`).
  `dry_run`/`traderspost_enabled` (semántica restrictiva) sin tocar
  (`config_resolver.py:250-260`).
- **symbol_busy intacto** (`filter_pipeline.py:454-471`).
- **Fail-closed L5**: sin backstop → ATR caído bloquea como siempre; con
  backstop → único bloqueo `entry_price_missing`; `build_scaled` sin
  `sl_price` → `ValueError` (`payload_builder.py:130-134`); escalera sin ATR
  → entrada única CON stop (test `test_escalera_sin_atr_cae_a_entrada_unica`).
- **Salidas**: nunca reducidas por factor (`payload_builder.py` — is_exit
  primero), nunca con SL/TP (`:41-53`); forced_exit/exit_manager usan
  SimpleNamespace y el builder tolera con getattr (`payload_builder.py:78-80`).
- **Pestaña Riesgo**: rutas protegidas (`main.py:81,87`); anti-traversal por
  `_KEY_RE` + membresía del manifest (`routes_riesgo.py:52,404-409`);
  subprocesos con args en lista (sin shell); upload validado con el parser
  real y CSV borrado si integrar falla (`routes_riesgo.py:352-357`); la
  pestaña NO toca dispatch/StrategyProfile/TradersPost (solo archivos del
  motor + manifest del Lab).
- **CSRF**: POSTs con cookie de sesión sin token anti-CSRF — postura
  PREEXISTENTE de toda la app (no introducida por Riesgo). Anotado P3.

## 4. Ítems Riesgo ya detectados

1. **UX "ELEGIDO ninguno"** — cuando el walk-forward no valida nada,
   `recomendacion=None` y la página omite la comparación y la recomendación
   EN SILENCIO (`riesgo.html` — bloques `{% if estudio.comparacion %}` /
   `{% if estudio.reco %}`): el operador ve solo la base sin saber por qué.
   Fix P1: banner explícito "sin recomendación validada — la base manda"
   (+ motivo: `robustez.nota`/flags) y el mismo estado en
   `recomendacion_*.json` ya existe (`sin_recomendacion: true`).
2. **FX en ticks** — `backstop_points` para 6E/6J es DECIMAL en unidad de
   precio (p. ej. 0.036 = $4,500/6E) y la doc dice "entero"; el operador
   puede razonar en ticks. Fix P1: aceptar decimales explícitamente (ya
   funciona), documentar la unidad por instrumento en la pestaña/recomendación
   (mostrar también ticks: `pts / tick_size`, el resolver ya trae `tick_size`,
   `config_resolver.py:170-178`) — y la guarda P0-1 ataja el error de escala.
3. **matplotlib al deploy** — está en el grupo opcional `riesgo`
   (`pyproject.toml:47-50`): un `pip install -e .` del server NO lo trae →
   la pestaña queda sin heatmap en silencio (el motor solo lo avisa en el
   tail del job). Fix P1: moverlo a dependencias principales (el heatmap ya
   es producto vía web) o instruir `pip install -e .[riesgo]` en el deploy +
   banner en la pestaña cuando falte el PNG.

## 5. Backlog Fase B (por lotes, parada entre cada uno)

### P0 — Seguridad (primero, aislado)
- [ ] **P0-1** Bracket con precio ≤ 0 → BLOCK en `SLTPCalculator`
      (`bracket_price_invalid`) + guarda `backstop >= entry_price` → BLOCK.
      Tests adversariales rojo→verde con el caso 6E (SL −88.917) y el TP
      fallback corto. Candado: no tocar rutas ATR ni kill-switch; suite
      completa verde.

### P1 — Bugs/Riesgo
- [ ] **P1-1** Banner "sin recomendación validada" en la pestaña (+ motivo).
- [ ] **P1-2** Unidades FX: decimales documentados + mostrar backstop también
      en ticks en la pestaña/recomendación.
- [ ] **P1-3** matplotlib: a deps principales (o deploy doc + banner sin
      heatmap).
- [ ] **P1-4** Lock por clave en `riesgo_upload` (serializar integrar).

### P2 — Dashboard/Analítica
- [ ] **P2-1** Unificar en UNA página (Analítica absorbe los KPIs de hoy con
      selector de rango + fila operacional bridge/entregas). Conservar
      `/ui/partials/*` (los consume `base.html:72`). Redirect `/ui` → página
      unificada. Quitar una entrada del nav.

### P3 — Limpieza (alcance AJUSTADO por el operador 2026-07-06)
> **La pestaña/UI del Lab NO se retira** — se conserva y se mejorará más
> adelante. El acoplamiento `routes_riesgo → routes_lab.load_manifest` se
> mantiene (la extracción a módulo común era solo para poder borrar el Lab
> y ya no aplica). El engine `lab_metrics`/`lab_analyze` intacto (siempre
> lo estuvo).
- [ ] **P3-1** Templates: deprecar NO destructivo — quitar nav +
      `routes_strategy_templates.py` + template. CONSERVAR modelo/columna
      `template_id` (sin migración destructiva).
- [ ] **P3-2** Borrar los 5 sims pre-motor superseded: `sim_sl_matrix`,
      `sim_scaled_entry`, `sim_sizing`, `sweep_matrix`,
      `calibrate_sl_from_trades`.

**No-tocar en toda la Fase B** (candados): `global_mode` L1 · `symbol_busy`
L3 · fail-closed L5 (los dos caminos) · `ConflictLog` · engine del Lab ·
CLIs de aplicar auditados.
