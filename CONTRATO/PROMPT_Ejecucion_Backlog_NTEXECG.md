# PROMPT para Claude Code — Ejecución del backlog NTEXECG (por lotes)

> Pégalo en Claude Code (con Fable 5: `/model fable`, verifica con `/model`). Implementa
> el backlog de la revisión de arquitectura **por lotes de arreglos compatibles**, con
> tests, sin romper invariantes, y **deteniéndote a esperar mi aprobación entre lotes**.
> Lo delicado (P0, dispatch, fail-closed, migraciones) va aislado o en lote mínimo.

---

## Rol
Actúa como **ingeniero senior** implementando el backlog. Cambios **quirúrgicos**, con
tests, sin refactors oportunistas fuera del lote.

## Fuente de verdad (usa el reporte de FABLE 5, es el bueno)
- **Backlog principal:** `CONTRATO/REVISION_ARQUITECTURA_2026-07-02_claude-fable-5.md`
  (§7, ítems **NX-01 … NX-26**). Es más completo y verificado (corrió la suite: 396 passed).
- **Ítem a fusionar del reporte de Opus** (`REVISION_ARQUITECTURA_2026-07-02.md`), que Fable
  no listó:
  - **NX-27 · Reversal respeta L1:** `_classify_and_handle_reversal` despacha el cierre
    ANTES de validar `strategy.status` (`webhooks_luxalgo.py:~221-254`); una estrategia
    `quarantined`/`retired` todavía puede despachar el cierre. Fix: cortocircuitar el
    reversal si `status in (quarantined, retired)` → BLOCK normal, 0 deliveries. Test.
- **Objetivo/invariantes:** `CONTRATO/PROMPT_Revision_Arquitectura_NTEXECG.md`,
  `CONTRATO/MEMORIA_TECNICA_NTEXECG_v1_0.md`, `CONTRATO/Anexo_25_Bateria_Pruebas_Filtros.md`.

> **Nota de estado:** el fix de **NX-01 ya está aplicado en el working tree pero SIN
> commitear** (`git status`: `config_resolver.py`, `filter_pipeline.py`,
> `tests/test_filter_pipeline.py` + `tests/test_global_mode_nx01.py`). El servidor, que
> consume por `git pull`, **todavía corre la versión rota**. Por eso NX-01 es
> "commit + push + verificar en server", no "implementar".

## Invariantes que NO se rompen (si un cambio los pone en riesgo, DETENTE y avísame)
- **Fail-closed en entradas:** entrada sin `sl_price` válido → BLOCK (nunca orden desnuda).
- **Paper/demo únicamente; un solo camino** LuxAlgo → NTEXECG → TradersPost.
- **Kill-switch por capas:** `dry_run` gana con que CUALQUIER nivel lo pida (OR);
  `traderspost_enabled` = global AND estrategia (AND). Un perfil solo puede **restringir**,
  nunca abrir. Envío real solo con todas las capas abiertas.
- **Despliegue:** el push se hace desde **NTDEV**; el servidor solo hace pull. **No ejecutes
  envíos reales a TradersPost ni corras jobs/scripts que despachen. No hagas commit/push tú.**

## Trabajo por LOTES — criterios de agrupación
Agrupa ítems que: (a) **no se pisen** (archivos/áreas compatibles), (b) sumen **bajo riesgo
combinado** y se puedan **testear juntos**, (c) sean del **mismo dominio**.
**Van aislados (o en lote mínimo con tests dedicados):** todo lo que toque
**dispatch / fail-closed / kill-switch**, cambios que alteren el **SL/precio**, y las
**migraciones de esquema**. No mezcles un cambio riesgoso con cosméticos.

## Reglas — por CADA lote
1. **Re-verifica** cada hallazgo del lote contra el código ACTUAL (cita `ruta:línea`).
2. **Plan del lote:** qué ítems, por qué conviven, qué archivos toca y qué NO.
3. **Implementa lo mínimo** de cada ítem para cumplir su **criterio de aceptación**.
4. **Tests adversariales:** el test debe **fallar sin el fix y pasar con él** (no solo
   "suite verde"). Incluye el caso que hoy NO se detecta. Corre `python -m pytest -q` y
   **pega el resultado**.
5. **Diff:** resumen por ítem (archivos + líneas clave).
6. **Actualiza el estado** de cada ítem en el reporte de Fable (⬜→✅) con nota de 1 línea.
7. **Prepárame el/los mensaje(s) de commit** (`tipo(scope): …`) y **DETENTE**. Yo reviso,
   hago push desde NTDEV y pull en el servidor.
8. **Un lote a la vez.** No empieces el siguiente hasta que yo lo apruebe.

## Plan de lotes — PRIMERO propónmelo y espera visto bueno (numeración de Fable)
- **Lote 1 — LOS 3 P0 (kill-switch/deploy) ⚠:** NX-01 + NX-02 + NX-03 (+ NX-19 tests de
  `dispatch_profiles`). Conviven porque todos **solo restringen** el envío. Ver detalle abajo.
- **Lote 2 — Endurecer fail-closed + quick wins de Config:** NX-05 (precio→BLOCK) + NX-10
  (dedup_seconds) + NX-11 (Scale Entry veraz) + NX-12 (score_minimum en UI).
- **Lote 3 — Calidad UNKNOWN (el pendiente estrella, Anexo 25):** NX-04.
- **Lote 4 — Salidas/estados (dispatch ⚠, con tests):** NX-06 (flatten real) + NX-07
  (forced/reversal a todos los perfiles) + NX-27 (reversal respeta L1) + NX-08 (FAILED→estado).
- **Lote 5 — Portafolio + reconciliación:** NX-09 (symbol_busy, validar en demo) + NX-18
  (reconciliación con broker, por fases).
- **Lote 6 — Config que cambia comportamiento (validar):** NX-13 (EOD "off") + NX-14
  (atr_timeframe) + NX-15 (retries GlobalProfile) + NX-16 (staleness real).
- **Lote 7 — Observabilidad/metadata (bajo riesgo):** NX-17 (cancel_after) + NX-20 (clone
  copia config) + NX-24 (alias legacy en analytics).
- **Lote 8 — Seguridad + limpieza (migración ⚠):** NX-21 (auto-create flag) + NX-22 (hash
  tokens) + NX-23 (columnas/modelos/env muertos) + NX-25 (docs) + NX-26 (nits).

Ajusta la agrupación si ves algo mejor, pero **respeta el aislamiento de lo riesgoso** y
**empieza SIEMPRE por el Lote 1**.

---

## DETALLE — LOTE 1 (los 3 P0)

**NX-01 (P0) · Freno global `paused`/`flatten_only` — desplegar el fix ya hecho.**
- Estado: implementado local sin commitear (`config_resolver.py:42,95` añade `global_mode`;
  `filter_pipeline.py:289` lo lee; `tests/test_global_mode_nx01.py`).
- Acción: **NO reimplementar.** Verifica el diff, corre `pytest` (debe incluir el test nuevo
  verde), y prepárame el commit. Yo hago push + `git pull` en server. Verificación en server:
  señal de entrada con GlobalProfile `mode=paused` ⇒ BLOCK `global_paused`; salidas pasan.

**NX-02 (P0) · Los perfiles NO pueden escalar el kill-switch. ⚠ (bug real)**
- Evidencia: `dispatch_profiles.py:~110-114` — `"dry_run": p["dry_run"] if isinstance(bool)
  else base_dry` y análogo `traderspost_enabled`. Un perfil con `dry_run:false` **reemplaza**
  el base (rompe el OR); con `traderspost_enabled:true` rompe el AND.
- Fix: `dry_run = bool(base_dry) or bool(p.get("dry_run"))`;
  `traderspost_enabled = bool(base_tpen) and (p["traderspost_enabled"] if isinstance(bool) else True)`
  (el perfil solo restringe: si no especifica, hereda; si especifica, solo puede endurecer).
- Criterio: test — base `dry_run=True` + perfil `dry_run=False` ⇒ destino DRY_RUN; base
  `traderspost_enabled=False` + perfil `True` ⇒ sin HTTP real. **Falla sin el fix.**

**NX-03 (P0) · Honrar el env `DRY_RUN` en el gate (o quitarlo del badge).**
- Evidencia: `common.py:44` enciende el badge con `settings.DRY_RUN or not TRADERSPOST_ENABLED`,
  pero `resolve_effective_dry_run` (`webhooks_luxalgo.py:288-292`) **no** consulta
  `settings.DRY_RUN`. Badge y realidad pueden divergir.
- Fix (elige y documenta): añadir `and not getattr(settings,"DRY_RUN",False)` a `real_send`
  (cuarta capa), de modo que `DRY_RUN=True` fuerce siempre dry-run.
- Criterio: test — env `DRY_RUN=True` + todo armado ⇒ resultado DRY_RUN (sin HTTP).

**NX-19 (P2, va con el Lote 1) · Tests de `dispatch_profiles`.**
- Sin ellos, NX-02 no tendría red. Suite: herencia base↔perfil, `cap_quantities`, dedupe por
  `webhook_url`, y la **semántica de capas del kill-switch por perfil** (el caso de NX-02).

### Entregable del Lote 1
Mini-reporte: hallazgos re-verificados (`ruta:línea`), diffs por ítem, salida de `pytest`
(con los tests adversariales), estado actualizado en el reporte de Fable, y los mensajes de
commit sugeridos (sugerido: 1 commit para NX-01, 1 para NX-02+NX-19, 1 para NX-03). **Luego
te detienes y esperas mi aprobación.**
