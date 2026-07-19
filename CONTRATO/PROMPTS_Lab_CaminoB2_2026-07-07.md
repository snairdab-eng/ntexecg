# Pestaña Lab — Evaluación lógica y prompts de arreglo · 2026-07-07

> Evaluación del arquitecto saliente (Fable) para que **Opus 4.8** implemente.
> Patrón de referencia: la pestaña Riesgo (Puente, v2-D, R-obs) — misma
> filosofía: upload validado con el parser real, jobs con polling, locks,
> eliminar NO destructivo, fail-closed, un solo origen de verdad, tests.

## A. Evaluación (estado real, verificado en producción 2026-07-07)

**Lo sano (conservar tal cual):**
- Visor read-only con núcleo COMPARTIDO (`app/services/lab_metrics`) — la
  paridad UI ↔ reporte offline es real y es el mayor activo del Lab.
- Recalc como job en subproceso con polling (patrón correcto).
- Upload validado con `parse_luxalgo_csv` antes de aceptar (B6.2).
- Anti-traversal (`_KEY_RE`) y manifest compartido con Riesgo.

**Lo roto / desconectado (verificado en vivo):**
1. **TODAS las cachés están STALE** (generadas 2026-07-04; cobertura hasta
   06-24/26) mientras Riesgo ya opera con los exports 070726. El Lab hoy
   muestra estudios de datos viejos con apenas un banner → para decidir,
   es información inservible. Números concretos del desfase: GC Lab
   n=107 vs master Riesgo n=42; ES 122 vs 121; 6J 78 vs 64; RTY además
   arrastra cobertura desde 2025-08-12 (¿export de otra ventana?).
2. **Estrategia nueva = hoyo**: `ES5m_ConfStrong` (alta de hoy vía Riesgo)
   da `cache_missing` 409 — el Lab no ofrece generar la caché ahí mismo.
3. **Subir listado NO invalida/regenera nada**: ni el upload del Lab ni el
   de Riesgo tocan la caché del Lab → siempre queda stale hasta recalc
   manual. (El de Riesgo sí integra su master; el del Lab no encola nada.)
4. **Sin gestión de datos** (espejo v2-D de Riesgo ausente): no se puede
   ELIMINAR listado ni estrategia desde el Lab; eliminar/renombrar desde
   Riesgo deja `lab_features_<key>.json` HUÉRFANAS en REPORTES (renombrar
   además deja la llave nueva sin caché).
5. **No se pueden COMPARTIR las listas**: no hay descarga del CSV vigente
   ni del enriched.
6. **Escritura del manifest duplicada y sin lock**: `lab_upload` escribe
   `lab_manifest.json` con su propia copia del código, sin el lock
   `_lock_integrar` de Riesgo → carrera last-writer-wins entre pestañas.

---

## B. 📋 PROMPT PARA OPUS 4.8 — LOTE LAB-1: "datos conectados" (frescura + fuente única)

Eres el implementador de NTEXECG (FastAPI + Jinja2/HTMX/Alpine; solo
paper/demo). Trabaja sobre `app/web/routes_lab.py`, `app/web/routes_riesgo.py`,
`app/templates/lab.html` y tests. NO hagas commit ni push. Verifica con
`.venv\Scripts\python.exe -m pytest -q` (suite completa al final; si se
cuelga, relanza con `-o faulthandler_timeout=300 --timeout=600`).

Contexto: el Lab es un visor read-only cuya caché (`REPORTES/
lab_features_<key>.json`) la regenera `scripts/lab_analyze.py` vía job
(`/ui/lab/recalc`). Hoy la caché queda stale en silencio cuando se sube un
listado (desde el Lab o desde Riesgo) y las estrategias nuevas dan 409.
El manifest (`REPORTES/lab_manifest.json`) es COMPARTIDO con Riesgo, pero
cada ruta lo escribe con código propio y solo Riesgo usa lock.

Tareas:
1. **Fuente única del manifest**: extrae a un helper compartido (p. ej.
   `app/web/manifest_store.py`) las funciones `load_manifest()` /
   `guardar_manifest()` y el lock por estrategia (hoy `_lock_integrar` en
   routes_riesgo). Ambas rutas lo importan; cero duplicación. Retrocompat:
   `routes_lab.load_manifest` y `routes_riesgo` siguen funcionando como
   símbolos (los tests los monkeypatchean — consérvalos como re-export).
2. **Upload → recalc encadenado (opt-out)**: al subir listado en
   `/ui/lab/upload`, encolar automáticamente el job de recalc de esa llave
   (mismo mecanismo de JOBS; si ya corre uno, 409 como hoy). En
   `/ui/riesgo/upload` (que ya integra el master del Motor), encolar
   TAMBIÉN el recalc del Lab para esa estrategia — un solo upload deja las
   DOS pestañas frescas. Respeta el patrón: subproceso + polling, nunca
   recompute en el request.
3. **Estrategia sin caché ≠ error críptico**: cuando `load_cache` devuelva
   None para una llave del manifest, la página debe mostrar la ficha de
   datos con botón "Generar caché ahora" (dispara el mismo recalc) en vez
   del banner regen_cmd de consola. El 409 de `/ui/lab/data` se queda (API).
4. **Identidad del dato SIEMPRE visible** (como el "MASTER EN USO" del
   motor): en la cabecera del estudio muestra export (nombre del CSV del
   manifest), cobertura, n_trades, fecha de caché; si `meta.stale`, banner
   ÁMBAR FUERTE con un botón "Recalcular" (no solo texto). El operador
   jamás debe leer un estudio viejo sin darse cuenta.
5. Tests (en `tests/test_lab_b6.py` o nuevo `tests/test_lab_datos.py`):
   upload encola recalc; upload de Riesgo encola recalc del Lab; página
   sin caché muestra el botón; banner stale con botón; manifest_store con
   lock (dos uploads concurrentes no se pisan — usa asyncio.gather).

Invariantes: read-only del visor (solo la caché cambia vía job), paridad
lab_metrics intacta, anti-traversal intacto, NO tocar scripts/lab_analyze
ni lab_metrics (el Motor de Riesgo los reusa). Al final: `git diff --stat`,
resumen de qué tocaste y "LISTO PARA COMMIT" si la suite queda verde.

---

## C. 📋 PROMPT PARA OPUS 4.8 — LOTE LAB-2: gestión de datos (compartir + eliminar, espejo v2-D)

Mismo marco operativo que LAB-1 (hazlo DESPUÉS de LAB-1; asume el
manifest_store compartido). Referencia de diseño: los endpoints v2-D de
Riesgo (`/ui/riesgo/estrategia` DELETE, `/ui/riesgo/datos` DELETE,
`renombrar`) y su UI (confirm + Alpine, botones en la ficha de Datos).

Tareas:
1. **Compartir/descargar listados**: `GET /ui/lab/csv?strategy=<id>` →
   FileResponse del CSV vigente del manifest (media_type text/csv,
   filename legible `<strategy>_<fecha>.csv`). Anti-traversal ESTRICTO:
   resolver el path y exigir que quede dentro de `ListaDeOperaciones/`
   (resolve() + is_relative_to). Botón "⬇ descargar CSV" en la fila de
   Datos de cada estrategia. Si el motor tiene enriched
   (`MotorRiesgo/<clave>/master_enriched.csv` o equivalente — verifica el
   nombre real en scripts/nt_riesgo.py), ofrece también "⬇ enriched".
2. **Eliminar LISTADO del Lab** (`DELETE /ui/lab/datos?strategy=<id>`):
   borra la caché `lab_features_<key>.json` y el CSV SOLO si es
   `upload_*` (jamás un export original del operador — regla v2-D); la
   entrada del manifest se conserva (queda "sin datos", lista para subir
   otro). Confirm en UI con texto explícito de qué borra.
3. **Eliminar ESTRATEGIA**: NO dupliques la lógica — reusa el endpoint de
   Riesgo v2-D (`/ui/riesgo/estrategia`) y EXTIÉNDELO para que además
   borre `REPORTES/lab_features_<key>.json` (hoy queda huérfana). El botón
   en el Lab llama a ese mismo endpoint. Igual `renombrar`: al renombrar
   en Riesgo, renombra también la caché del Lab si existe.
4. **Alta nueva**: una sola puerta de identidad (decisión del Puente) — el
   Lab NO da altas; pon el CTA "➕ nueva estrategia → (pestaña Riesgo)"
   apuntando a `/ui/riesgo` cuando la llave no exista.
5. Tests: descarga con anti-traversal (un `strategy=../../etc` → 400);
   eliminar listado borra caché + upload_ y conserva manifest; eliminar
   estrategia desde riesgo limpia la caché del Lab; renombrar mueve la
   caché; el CTA de alta aparece.

Invariantes: mismos de LAB-1 + eliminar jamás toca la estrategia viva de
la DB ni los exports originales. "LISTO PARA COMMIT" solo con suite verde.

---

## D. 📋 PROMPT PARA OPUS 4.8 — LOTE LAB-3: sanidad de salidas (auditoría de números)

Mismo marco operativo. Este lote es de AUDITORÍA + arreglos quirúrgicos,
no de features. Hazlo al final, con LAB-1/2 desplegados y las cachés
regeneradas con los exports 070726.

Tareas:
1. **Reconciliación Lab ↔ Motor por estrategia** (script one-shot en
   `scripts/`, estilo `lab_analyze --all-summary`): para cada llave del
   manifest compara n_trades, cobertura y net del listado crudo entre
   `lab_features_<key>.json` y `MotorRiesgo/<clave>/manifest.json`. Emite
   tabla con ✓/⚠. Diferencias esperadas: el filtro de universo ATR del
   Lab (documenta cuántos trades filtra y por qué); diferencias NO
   esperadas: cobertura distinta (ej. RTY arrastraba 2025-08 — ventana de
   export vieja) o n desalineado más allá del filtro.
2. **Exponer esa reconciliación en la ficha del Lab** (una línea:
   "coincide con el master del Motor ✓ / difiere: <detalle>") — el mismo
   espíritu del badge de deriva del Puente.
3. **Barrido de outputs muertos**: recorre lab.html y los endpoints
   (`/data`, `/best`, `/aggregate`, `/combined`, `/default`, `/resim`) y
   verifica contra el template qué campos de la respuesta NO se renderizan
   o se renderizan siempre vacíos (candidatos a ruido). Repórtalos ANTES
   de borrar nada; el criterio es del operador. NO elimines campos de las
   respuestas JSON sin aprobación (retrocompat de tests).
4. Tests: el script de reconciliación con fixtures (coincide / difiere);
   la línea en la ficha en ambos estados.

Invariantes: paridad lab_metrics intocable; el Lab sigue read-only; nada
de recompute en requests. "LISTO PARA COMMIT" solo con suite verde.

---

## E. Topología de conexiones (decisión de arquitectura — pregunta del operador)

¿Cómo deben conectarse Lab, Riesgo y Estrategias? **Las tres comparten la
IDENTIDAD, pero la puerta de escritura a producción es UNA sola.**

```
                    identidad única: strategy_id + lab_manifest
        ┌────────────────────────┬──────────────────────────┐
        │          LAB           │          RIESGO           │
        │  (explora la señal:    │  (protege la cuenta:      │
        │  filtros, régimen,     │  backstop, TP nominal,    │
        │  what-ifs, horas)      │  escalera, gate OOS)      │
        └───────────┬────────────┴─────────────┬────────────┘
                    │   datos compartidos:      │
                    │   CSVs + manifest +       │
                    │   núcleo lab_metrics      │
                    │                           │
             solo LECTURA               ÚNICA ESCRITURA
             (links, comparación        (el Puente: aplicar con
              informativa)               diff + deriva + promoción)
                    ▼                           ▼
        ┌──────────────────────────────────────────────────┐
        │                   ESTRATEGIAS                     │
        │            (la config viva que despacha)          │
        └──────────────────────────────────────────────────┘
```

Reglas para el implementador (vinculantes en LAB-1/2/3):
1. **Lab ↔ Riesgo: conectados por datos** (ya lo están: manifest y CSVs
   compartidos, el Motor reusa `lab_metrics`/`lab_analyze`). LAB-1 los
   sincroniza de verdad (un upload refresca ambos).
2. **Riesgo → Estrategias: la ÚNICA escritura** (el Puente ya construido:
   aplicar supervisado + deriva + promoción). Sus palancas pasaron el gate
   OOS — por eso pueden escribir.
3. **Lab → Estrategias: SOLO LECTURA, jamás escritura.** El Lab explora la
   SEÑAL (filtros/score/régimen/horas) — exactamente lo que el pivote
   descartó como palanca de producción ("los filtros no dieron edge
   robusto"). Un botón "aplicar" desde el Lab reabriría esa puerta sin
   gate. Lo que SÍ debe tener (agregar en LAB-2, tarea 4-bis):
   - link "⚙ config viva →" por estrategia (como el `link_vivo` de
     Riesgo) para saltar a su ficha;
   - línea informativa de comparación: los `filters`/`regime` VIVOS de la
     estrategia (pipeline_config_json) junto a la selección what-if del
     Lab — rotulada "informativo — aplicar filtros a producción requiere
     pasar por el gate del Motor";
   - si el operador algún día quiere promover una palanca del Lab, el
     camino es integrarla como palanca del MOTOR (gate OOS) — nunca un
     atajo Lab→Estrategias.

## F. Orden y nota operativa

LAB-1 → (deploy + regenerar cachés de las 7 con el botón nuevo) → LAB-2 →
LAB-3. Cada lote es deployable solo, con el flujo de siempre (verificar en
NTDEV → commit/push del operador → pull/restart en server). Tras LAB-1,
la PRIMERA acción del operador es recalcular las 7 cachés: hoy todas están
stale (datos 06-27 vs exports 070726) y ES_ConfStrong ni siquiera tiene.
