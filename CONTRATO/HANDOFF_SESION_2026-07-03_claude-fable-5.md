# HANDOFF — Estado de la sesión NTEXECG · 2026-07-03 · `claude-fable-5`

> **Para la próxima sesión de Claude Code (sin contexto previo).** Este documento resume TODO
> lo hecho en la sesión 2026-07-02/03, dónde está cada cosa, qué queda pendiente y cómo se
> trabaja aquí. Léelo completo antes de tocar nada.

---

## 0. LO URGENTE AL RETOMAR (en este orden)

1. **Hay trabajo SIN COMMITEAR en el working tree** (Fase B3 del visor Laboratorio, terminada,
   verificada y con suite verde — solo falta el commit del operador):
   - `M app/services/lab_metrics.py` · `M app/web/routes_lab.py` · `M app/templates/lab.html`
     · `M scripts/lab_analyze.py` · `M tests/test_lab_ui.py`
   - `?? CONTRATO/PROMPT_Laboratorio_CaminoB.md` (el prompt, commitearlo con docs)
   - Mensaje de commit sugerido (ya aprobado el trabajo, pendiente push del operador):
     `feat(lab-ui): Fase B3 — cambia-desenlace + pullback (visor completo)` — el texto
     completo está al final de este documento (§9).
2. **Verificar suite:** `cd C:\NTEXECG && .venv\Scripts\python.exe -m pytest -q`
   → debe dar **563 passed** (~2 min). Si da otra cosa, algo se movió: investigar antes de seguir.
3. **Regla de oro de esta casa:** Claude NUNCA hace `git commit/push` — prepara mensajes y el
   OPERADOR pushea desde NTDEV; el servidor solo hace pull. Claude tampoco dispara envíos
   reales a TradersPost.

---

## 1. QUÉ ES ESTE PROYECTO (30 segundos)

**NTEXECG** = gateway fail-closed de señales de trading: LuxAlgo/TradingView (webhooks) →
pipeline de 5 niveles (validación/temporal/riesgo/calidad/SL-ATR) → TradersPost (paper/demo
únicamente). FastAPI + Postgres + Jinja2/HTMX/Alpine. Repo en `C:\NTEXECG` (esta máquina =
NTDEV, desarrollo); producción en servidor Ubuntu que consume por `git pull`.
**Invariantes:** entrada sin SL → BLOCK; kill-switch por capas (env `TRADERSPOST_ENABLED` +
env `DRY_RUN` + global AND/OR estrategia; un perfil solo restringe); solo paper/demo.

Docs de fondo: `CONTRATO/MEMORIA_TECNICA_NTEXECG_v1_0.md` (especificación viva),
`CONTRATO/03_ARQUITECTURA_ESTRUCTURA_v1_0.md` (actualizado al árbol real 2026-07-03),
`CONTRATO/Anexo_25_Bateria_Pruebas_Filtros.md` (estándar de calidad + batería de pruebas).

## 2. PROTOCOLO DE TRABAJO ACORDADO (cómo quiere trabajar el operador)

- **Por lotes/fases, deteniéndose entre cada uno** a esperar visto bueno. Un lote a la vez.
- **Re-verificar cada hallazgo con `ruta:línea`** contra el código actual antes de tocar.
- **Tests adversariales:** escribir el test ANTES del fix y demostrar que falla (rojo) →
  implementar → verde. Pegar el resultado de `pytest -q`.
- Al cerrar cada lote: diff resumido por ítem, actualizar el estado en el reporte de
  arquitectura, **mensajes de commit sugeridos** (formato `tipo(scope): ...`), y DETENERSE.
- Lo delicado (dispatch, fail-closed, kill-switch, migraciones) va aislado con tests dedicados.
- Modelo: el operador verifica que la sesión corra con **Fable 5** (`/model`).

**Quirk operativo conocido:** en esta máquina, cuando el harness manda `pytest` (suite
completa) a background, a veces se cuelga sin escribir output → matar la tarea y relanzar en
foreground (siempre funcionó al segundo intento). Los runs targeted no sufren esto.

## 3. CRONOLOGÍA DE LA SESIÓN (todo APROBADO y DESPLEGADO salvo B3)

### 3.1 Revisión de arquitectura (2026-07-02)
- Prompt: `CONTRATO/PROMPT_Revision_Arquitectura_NTEXECG.md`.
- **Entregable: `CONTRATO/REVISION_ARQUITECTURA_2026-07-02_claude-fable-5.md`** — ES EL
  DOCUMENTO MAESTRO: hallazgos P0-P3 con evidencia, matriz de trazabilidad de la pestaña
  Config, código muerto, y el **backlog NX-01…NX-28 con columna de estado actualizada**
  lote a lote. (Existe también `REVISION_ARQUITECTURA_2026-07-02.md` de Opus — el bueno es
  el de fable-5; de Opus solo se fusionó NX-27.)

### 3.2 Backlog por lotes (prompt: `CONTRATO/PROMPT_Ejecucion_Backlog_NTEXECG.md`)
Los 8 lotes ejecutados, aprobados, commiteados y desplegados:

| Lote | Ítems | Lo esencial |
|---|---|---|
| 1 | NX-01/02/03 + NX-19 | Freno global `paused` desplegado; perfiles ya no escalan el kill-switch (OR/AND); env `DRY_RUN` es capa real del gate (server con `DRY_RUN=false`, verificado); tests de dispatch_profiles |
| 2 | NX-05/10/11/12 | Entrada sin precio → BLOCK `entry_price_missing`; `dedup_seconds` conectado; UI Scale Entry preserva `mode=execute`; `score_minimum` editable en UI |
| 3 | NX-04 | **Calidad UNKNOWN/LOW/MEDIUM/HIGH** (Anexo 25 §1-bis): score ya no parte en 100 (salidas/blocks → None), `filters_active`+`quality` en traza L4, `ntexecg_quality` en extras, ribbon sin ✅ para UNKNOWN |
| 4 | NX-06/07/08/27 | Flatten UI despacha cierre REAL; forced/reversal exits a TODOS los perfiles; FAILED → estado honesto (salida→UNKNOWN, entrada→FLAT) + `find_stale_positions`; reversal respeta L1 (quarantined no cierra) |
| 5 | NX-09 + NX-18A | **`symbol_busy` L3.4** (una posición por símbolo, opt-out `allow_stacking`, reversals exentos — validado en demo); reconciliación Fase A: el import semanal pone FLAT solo con match exacto por signal_id |
| 6 | NX-28 + NX-13/14/15/16 | **Liberar reservas sin fill** (entry_style market/limit_only; timeout = `entry_reserve_timeout_seconds` ≈ cancel_after; fantasmas legacy limpiados con `reset_position_state`); EOD "off" por estrategia; `atr_timeframe` consumido; retries desde GlobalProfile; `signal_ts` desde `payload.time` |
| 7 | NX-17/20/24 | `pullback_timing --apply` escribe `entry_reserve_timeout_seconds` (**una sola caducidad** con NX-28 y el cancel_after de TradersPost, que se fija A MANO); clone copia config saneada; alias de renames en Analytics (desde AuditLog `renamed_from`) |
| 8 | NX-21/22/23/25/26 | Flag `ALLOW_STRATEGY_AUTOCREATE`; **tokens de webhook hasheados** con dual-read (sin re-alta; script `hash_webhook_tokens.py` — ⚠ ver §6 si aún no se corrió en server); migración `e5f6a7b8c9d0` (19 columnas muertas + `economic_events` fuera; **ConflictLog conservado para NX-18C**; reversible, verificada up/down/up); docs actualizados; nits (avg_score solo medidos vía `scored_signals`, parsers, warning régimen vacío) |

### 3.3 Laboratorio — camino A (CLI offline) — 3 fases, commiteadas
- Prompt: `CONTRATO/PROMPT_Laboratorio_CaminoA.md`. Motor: **`scripts/lab_analyze.py`**.
- F1: parser CSV LuxAlgo (`ListaDeOperaciones/*.csv`, 2 filas/trade, BOM) + OHLC
  (`NINJATRADER/HOLC/{SYM}_{tf}.csv`, estático hasta 22-jun; costura opcional de Postgres
  `--stitch-db` para la cola) + **validación TZ bloqueante** (los 8 instrumentos: offset +0
  = ET, sanity 87-100%; detector robusto a rolls — YM con trades semanales fue el caso duro)
  + baseline + SL sweep + edge por hora, split in/out 70/30.
- F2: 4 subscores con `quality_scorer` VIVO, régimen `classify_regime` 1h/4h, EMA-bias,
  TP sweep, SL+TP conjunto con orden de toques intrabar.
- F3: pullback (fill-rate por nivel ×ATR × desenlace; **cancel_after con el MISMO estimador
  que `pullback_timing`**: min(3600, p90·60+60)) + `--all-summary` (8 instrumentos).
- **Artefactos** (regenerables, no van al repo): `REPORTES/LAB_<SYM>_2026-07-03.md` (8),
  `REPORTES/LAB_RESUMEN_2026-07-03.md`, `REPORTES/lab_features_<SYM>.json` (la matriz+meta).
- **Hallazgos clave del resumen:** GC→familia QualityScorer robusta OOS (9 supervivientes);
  ES→`volume_relative≥50/60` único sólido; 6E/6J→nativo domina; NQ/CL/YM ganadores con
  n_out<15 (no confiables aún). ES out-of-sample nativo PF 0.92. Pullback ES: niveles
  profundos que llenan GANAN MENOS (PF 1.75→0.86 de 0.25× a 5×).

### 3.4 Laboratorio — camino B (visor UI) — B1/B2 commiteadas, **B3 SIN COMMITEAR**
- Prompt: `CONTRATO/PROMPT_Laboratorio_CaminoB.md` (untracked — commitear con docs).
- Candados: visor READ-ONLY (consume `lab_features_<SYM>.json`; caché ausente/vieja → banner
  con `python -m scripts.lab_analyze --all-summary [--stitch-db]`); **una sola fuente de
  verdad** = `app/services/lab_metrics.py` (el reporte offline y los endpoints llaman LAS
  MISMAS funciones — paridad exacta verificada en ES real en TODAS las tablas); no aplica
  nada a producción; guarda n<15 visible.
- B1: `/ui/lab` + `GET /ui/lab/data` + `POST /ui/lab/aggregate` + tarjeta base. B2: filtros
  interactivos (subscores/umbral/régimen/EMA, Alpine) + edge por hora. **B3 (working tree):**
  `POST /ui/lab/resim` (SL/TP/conjunto con orden intrabar desde toques CACHEADOS
  `t_sl_touch`/`t_tp_touch` — el camino A los escribe; sin recompute pesado) + curva de
  equity Chart.js + panel pullback con la nota "una sola caducidad".

## 4. MAPA DE ARCHIVOS (dónde está cada cosa)

**Documentos:** `CONTRATO/REVISION_ARQUITECTURA_2026-07-02_claude-fable-5.md` (maestro,
backlog §7 con estados) · `CONTRATO/PROMPT_*.md` (los 4 prompts de la sesión) · Memoria/
Anexos en `CONTRATO/` · histórico archivado en `A REVISAR/2026-07_archivado/`.

**Código nuevo/tocado de la sesión (lo grande):**
- `app/services/lab_metrics.py` — núcleo puro compartido del Laboratorio (agregación, lift,
  hourly, **resim_rows**, equity_curve; guardas `LOW_N_OUT=15`).
- `scripts/lab_analyze.py` — motor offline (parser, TZ, ATR, features, sweeps, pullback,
  `--all-summary`, cache con meta+toques).
- `app/web/routes_lab.py` + `app/templates/lab.html` — visor.
- `app/services/quality_scorer.py` (taxonomía UNKNOWN…), `filter_pipeline.py` (symbol_busy,
  quality, atr_timeframe), `forced_exit.py` (multi-perfil, stale, release reservas),
  `position_service.py` (entry_style, failed-transitions), `dispatch_profiles.py` (OR/AND),
  `traderspost_client.py` (retries param), `signal_normalizer.py` (time real),
  `results_import.py` (reconcile A), `strategy_aliases.py`, `config_resolver.py`.
- `scripts/`: `pullback_timing.py` (--apply), `hash_webhook_tokens.py`, `rename_strategy.py`.
- Migración: `app/db/migrations/versions/20260703_e5f6a7b8c9d0_nx22_nx23_cleanup.py`.

**Tests** (563 en verde): por lote `tests/test_dispatch_profiles.py`, `test_entry_price_nx05`,
`test_config_lote2`, `test_quality_nx04`, `test_exits_lote4`, `test_portfolio_lote5`,
`test_reserve_nx28`, `test_config_lote6`, `test_meta_lote7`, `test_security_lote8`; Laboratorio
`test_lab_analyze.py` (18) y `test_lab_ui.py` (17).

## 5. QUÉ FALTA (backlog y seguimientos)

- **Inmediato:** commit+push de B3 (§0) → en el server: pull, restart, y
  `python -m scripts.lab_analyze --all-summary --stitch-db` (regenera cachés CON toques,
  pullback y los ~29 trades de la cola post-22-jun; sin esto el conjunto SL+TP del visor
  avisa "cache legado").
- **Backlog NX abierto:** `NX-18 Fases B y C` (expiración→UNKNOWN; poll broker→ConflictLog,
  que se CONSERVÓ para esto) · restos de `NX-26` (consolidar `Strategy.enabled` y
  `traderspost_webhook_url` duplicado — tienen lectores en UI; reloj inyectable; ficha
  `allow_exits=False` tri-estado). Todo lo demás ✅ (ver estados en el reporte maestro §7).
- **Decisiones de calibración del operador (con el visor + resumen):** qué filtros aplicar
  (candidato firme: GC QualityScorer; ES volume_relative — aplicar SOLO por CLI auditado
  `apply_quality_filter.py`) y correr `pullback_timing --apply` con datos demo → replicar
  cancel_after A MANO en TradersPost (no hay API).
- **Posibles siguientes features:** botón "aplicar" desde el visor (declarado fuera de alcance
  por ahora), alertas push, health profundo, CI (ideas §6 del reporte maestro).

## 6. NOTAS OPERATIVAS CRÍTICAS (no perder)

- **Server .env:** `DRY_RUN=false` (si se pone true, TODO va a dry-run — es capa real desde
  NX-03), `TRADERSPOST_ENABLED=true`, `ALLOW_STRATEGY_AUTOCREATE=false`, y
  **`WEBHOOK_TOKEN_SALT` debe ser estable** — cambiarlo invalida los hashes de tokens.
- **Tokens (NX-22):** dual-read activo (hash → claro legacy → secret global). Si
  `scripts/hash_webhook_tokens.py --apply` YA se corrió en el server, los tokens están
  hasheados (verificar: `strategies.webhook_token` NULL, `webhook_token_hash` poblado);
  las alertas de LuxAlgo siguen válidas sin re-alta. `test_buy_all.py` ya no puede leer
  tokens de la DB tras el hashing.
- **Migraciones:** al desplegar Lote 8 se corrió `alembic upgrade head` (rev `e5f6a7b8c9d0`).
- **symbol_busy (NX-09):** las dos ES sobre MES se excluyen mutuamente; opt-out
  `allow_stacking` por JSON. Reservas sin fill se liberan solo con `entry_style=limit_only`
  (posiciones previas al fix no tienen marca → `reset_position_state.py`).
- **Una sola caducidad:** `entry_reserve_timeout_seconds` (ficha Config / pullback_timing
  --apply / visor) = el "Cancel entry after" de TradersPost (manual). Default 3600.
- Los `REPORTES/LAB_*` y `lab_features_*` son ARTEFACTOS regenerables — no commitearlos.
- La suite usa `.env.test` (SQLite in-memory, mocks); nunca toca red ni la DB real.

## 7. DATOS DEL LABORATORIO (por si hay que regenerar)

- Trades: `ListaDeOperaciones/LuxAlgo®_-_Backtester_..._{SYM}1!_2026-06-27_*.csv` — CSV con
  BOM, 2 filas por `Trade number` (Salida ANTES que Entrada), decimal punto, MFE/MAE en
  columnas `Desviación favorable/adversa %`. TZ del chart = **ET** (offset +0 validado).
- OHLC: `NINJATRADER/HOLC/{SYM}_{5m,15m,1h,4h}.csv` — estático, 2021→2026-06-22, ET,
  continuo back-ajustado (δ de roll por instrumento: ES +61, NQ +282, YM +670…). La cola
  posterior se cose de Postgres `OhlcvBar` con `--stitch-db` (solo en server).
- YM es el caso especial: backtest ago-2025→jun-2026, ~1 trade/semana (por eso el detector
  TZ usa dispersión local por pares ≤10 días).

## 8. COMANDOS DE VERIFICACIÓN RÁPIDA

```bash
cd C:\NTEXECG
.venv\Scripts\python.exe -m pytest -q                      # 563 passed
git log --oneline -5                                        # HEAD df69419 (B2)
git status -s                                               # B3 pendiente (5 M + 1 ??)
.venv\Scripts\python.exe -m scripts.lab_analyze --instrument ES   # reporte+cache ES
# visor: levantar la app y abrir /ui/lab?instrument=ES
```

## 9. COMMIT PENDIENTE (B3 — trabajo aprobado, listo para push)

```
feat(lab-ui): Fase B3 — cambia-desenlace + pullback (visor completo)

La re-sim SL/TP se muda al nucleo compartido (resim_rows): el camino A
cachea el estadistico suficiente (minutos al primer toque por nivel de
la grilla, una caminata 5m por trade) y el orden intrabar se resuelve
sin recompute pesado (mismo minuto = misma barra = ambiguo → SL); el
reporte §2/§8/§9 delega en la misma funcion — known-answers intactos.
POST /ui/lab/resim (grillas validadas, deltas, guarda n<15, curvas de
equity base vs re-sim con Chart.js) + panel de pullback desde el
agregado cacheado con la nota "una sola caducidad" (cancel_after =
entry_reserve_timeout_seconds, estimador de pullback_timing). Cache
legado: conjunto degrada a SL conservador con aviso de regenerar.
Paridad verificada en ES real en §2/§8/§9/§10 (cancel_after 1560).
```
(archivos: `app/services/lab_metrics.py`, `scripts/lab_analyze.py`, `app/web/routes_lab.py`,
`app/templates/lab.html`, `tests/test_lab_ui.py`; + `CONTRATO/PROMPT_Laboratorio_CaminoB.md`
y este HANDOFF en un commit de docs)

---
*Generado por `claude-fable-5` al cierre de la sesión 2026-07-03. Paridad UI↔reporte
verificada; suite 563 passed; ningún cambio pendiente de implementación — solo el commit B3.*
