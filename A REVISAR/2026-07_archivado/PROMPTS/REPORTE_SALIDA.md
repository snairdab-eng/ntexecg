# REPORTE_SALIDA — NTEXECG

## Sección 2026-06-19

### ⚠️ Decisión de ubicación (léeme primero)

`c:\NTEXECG` **ya contenía un proyecto distinto y versionado en git** (otra
implementación NTEXECG: SQLAlchemy/PostgreSQL, app LuxAlgo→TradersPost, con
`app/`, `alembic.ini`, `tests/`, bundles, etc.). El `NTEXECG_build_prompt.md`
describe un sistema **diferente y más simple** (SQLite, `/hook/{token}`, perfiles
de estrategia/cuenta) cuya estructura sugerida (`app/main.py`, `app/db/repository.py`,
`app/pipeline/…`) **colisiona y sobrescribiría** ese código existente.

Para seguir el spec **sin destruir** lo que ya había, construí el proyecto del
prompt en la subcarpeta autocontenida **`c:\NTEXECG\ntexecg\`** (que además coincide
con la carpeta raíz `ntexecg/` del propio diagrama del prompt). **No toqué** el
`c:\NTEXECG\app` existente. → Si tu intención era reemplazar el proyecto anterior,
dímelo y muevo/reubico; es trivial.

---

### 1. Resumen de lo construido

Compuerta de validación **LuxAlgo → NTEXECG → TradersPost** en FastAPI + Pydantic v2,
persistencia SQLite **detrás de una interfaz de repositorio** (portable a PostgreSQL).

Árbol (`c:\NTEXECG\ntexecg\`):

```
app/
  config.py                 # settings de infra (sin defaults de negocio)
  main.py                   # FastAPI: /hook/{token} + admin CRUD + OpenAPI
  schema_validation.py      # jsonschema contra los .schema.json provistos
  models/                   # Pydantic: signal, strategy, account, instrument, audit
  db/
    repository.py           # interfaz (Protocol) — la lógica depende de esto
    sqlite_repo.py          # implementación SQLite
  pipeline/
    validators.py           # cada check como función pura y testeable
    runner.py               # orquesta el orden, corta al primer fallo, audita
  services/
    catalog.py              # carga/consulta del catálogo (autocompletado tick)
    forwarder.py            # POST a TradersPost (sin retry), mockeable
    tokens.py               # generación + compare_digest
data/
  instrument_catalog.json   # catálogo precargado (semilla)
  schemas/                  # los 3 schemas + el example MES
tests/                      # test_pipeline.py (criterios), test_endpoint.py (HTTP/admin)
README.md, requirements.txt, pytest.ini
```

Endpoints: `POST /hook/{token}`; admin `POST/GET/PUT /admin/strategies[/{id}]`,
`…/enable`, `…/disable`, `POST /admin/strategies/validate`,
`GET/POST/PUT /admin/accounts[/{id}]`, `GET /admin/accounts/{id}/risk`,
`GET /catalog`, `GET /admin/audit`. Swagger en `/docs`.

### 2. Estado de los 10 criterios de aceptación

Todos **PASAN**. (Los criterios se ejercen vía `process_signal()` con `now`
inyectado para que ventana/antigüedad sean deterministas en cualquier host; la capa
HTTP se cubre aparte con TestClient.)

| # | Criterio | Estado | Test |
|---|----------|--------|------|
| 1 | Señal válida → aceptada y reenviada | ✅ | `test_01_valid_signal_accepted_and_forwarded` |
| 2 | ticker ≠ esperado → `SYMBOL_MISMATCH` | ✅ | `test_02_symbol_mismatch` |
| 3 | interval ≠ esperado → `INTERVAL_MISMATCH` | ✅ | `test_03_interval_mismatch` |
| 4 | señal vieja → `SIGNAL_STALE` | ✅ | `test_04_signal_stale` |
| 5 | entrada fuera de ventana → `OUTSIDE_WINDOW`; salida fuera → procesada | ✅ | `test_05a_entry_outside_window`, `test_05b_exit_outside_window_processed` |
| 6 | quantity > máx → `QTY_EXCEEDS_MAX` | ✅ | `test_06_quantity_exceeds_max` |
| 7 | riesgo > máx → `RISK_EXCEEDS_MAX` | ✅ | `test_07_risk_exceeds_max` |
| 8 | estrategia disabled → `STRATEGY_DISABLED` | ✅ | `test_08_strategy_disabled` |
| 9 | duplicado dentro de `dedup_seg` → `DUPLICATE_IGNORED`, no reenvía | ✅ | `test_09_duplicate_ignored` |
| 10 | fallo de reenvío → `FORWARD_FAILED`, sin retry, logueado | ✅ | `test_10_forward_failed` |

Extras: `UNKNOWN_TOKEN` (404, sin filtrar detalle), auditoría de toda señal,
y suite HTTP/admin (alta nace `disabled`, enable/disable, validate, catálogo,
cuenta + hook de riesgo agregado).

### 3. Resumen de pytest

```
22 passed, 1 warning in ~1.5s
```
- Python 3.12.10, venv en `c:\NTEXECG\ntexecg\.venv`.
- El único warning es `StarletteDeprecationWarning` (httpx + TestClient), benigno.
- El reenvío a TradersPost está **siempre mockeado** en tests (nunca toca la red).

### 4. Decisiones de implementación

- **Repositorio detrás de interfaz** (`Repository` Protocol) con impl SQLite; los
  perfiles se guardan como JSON + columnas indexadas (`token`, `estado`, `simbolo`,
  `cuenta_objetivo`) → portar a PostgreSQL no toca pipeline ni endpoints.
- **`valor_tick`/`tipo_contrato` se resuelven del catálogo en tiempo de request**
  (invariante 6), no de la copia guardada en el perfil.
- **Validación con `jsonschema`** (Draft 2020-12) contra los `.schema.json` provistos,
  sobre el objeto ya ensamblado (autocompletado + campos de sistema) → lo que se
  persiste es exactamente lo que el schema describe.
- **Token**: `secrets.token_hex(16)` (32 hex), lookup por columna única + verificación
  con `hmac.compare_digest`. Token desconocido → 404 sin detalle.
- **Nace `disabled`** (invariante 2): el alta ignora/override cualquier `estado`,
  `id`, `token`, `url_unica` que venga en el body.
- **Sin reintentos** (invariante 4): `forwarder.forward` hace un único POST.
- **Auditoría centralizada en el runner** (invariante 7): se escribe una fila por
  cada señal en todos los caminos, incluidos los rechazos tempranos.
- **Hook de riesgo por cuenta** (invariante 5): `aggregate_account_risk` suma el
  `riesgo_usd_max_operacion` de todas las estrategias que apuntan a la cuenta;
  el bloqueo diario real queda para una iteración posterior (estado ya listo).

### 5. Ambigüedades / supuestos (explícitos — no adiviné en silencio)

1. **Ubicación del proyecto** (ver aviso arriba): construido en `ntexecg/` subcarpeta
   para no sobrescribir el código preexistente en `c:\NTEXECG`.
2. **Definición de "señal idéntica" (dedup):** clave = SHA256 de
   `strategy_id|ticker|action|sentiment|quantity|price|interval` (excluye `time`,
   para que un reenvío del mismo evento cuente como duplicado). Ventana medida contra
   `received_at` en el audit log.
3. **Contra qué timestamp se evalúa la ventana (paso 7):** contra `payload.time`
   (el instante de la señal) localizado a `ventanas.timezone`, no contra el reloj del
   servidor. Rationale: la ventana es propiedad de la señal.
4. **Origen del stop (paso 9):** campo opcional `stop_ticks` en el payload tiene
   prioridad; si no viene, se usa `riesgo.stop_esperado_ticks`. Si `stop_obligatorio`
   y no hay ninguno → `STOP_REQUIRED`.
5. **Campo crítico faltante/no parseable** (ticker, quantity, price, time, interval,
   y al menos uno de action/sentiment) → `422 INVALID_PAYLOAD`. Este código **no está
   enumerado** en el spec; lo introduje y lo ubiqué **después** de token(1)+estado(2)
   y antes de símbolo(3), para respetar la precedencia de la tabla (token desconocido
   sigue dando 404; disabled sigue dando 409).
6. **Clasificación entrada/salida:** salida si `sentiment=="flat"` o `action` ∈
   {exit, close}; entrada en caso contrario. Se exige `action` **o** `sentiment`.
7. **Respuesta de duplicado:** el spec solo define cuerpos `accepted`/`rejected`; para
   el paso 6 devuelvo `200` con `{ "status":"duplicate", "reason_code":"DUPLICATE_IGNORED", … }`.
8. **`salidas_siempre_permitidas`:** trato la invariante 3 como absoluta (las salidas
   nunca se bloquean por ventana). El flag por-estrategia coincide con ese
   comportamiento; si alguien lo pusiera en `false`, el invariante manda (salida
   permitida). Documentado, no silencioso.
9. **`INSTRUMENT_NOT_IN_CATALOG` (422):** defensivo, por si una estrategia referenciara
   un símbolo ausente del catálogo (no debería pasar: el alta autocompleta desde él).
10. **Pruebas deterministas:** los 10 criterios corren sobre `process_signal()` con
    `now` inyectado (ventana/antigüedad reproducibles en cualquier host/fecha); la capa
    HTTP se valida con TestClient y, para el camino aceptado end-to-end, con el reloj
    del runner congelado vía monkeypatch.

### 6. Pendientes / próximos pasos

- **Enforcement del riesgo diario por cuenta** (bloquear cuando el agregado del día
  supere `riesgo_usd_max_diario`). El hook de estado ya existe.
- **`cierre_forzado_eod`**: hoy es documental (se guarda, no se enforca); el spec no
  define su lógica en el pipeline. Definir y enforzar el flat forzado EOD.
- **Autenticación/secreto en endpoints admin** (hoy abiertos; el `/hook` sí usa token).
- **Tabla de dedup dedicada** (hoy se reusa `audit_log`); separar si se quiere TTL/limpieza.
- **Migración a PostgreSQL** (interfaz lista) y despliegue uvicorn productivo.

### 7. Cómo correr

```bash
cd c:\NTEXECG\ntexecg
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# API + Swagger en http://localhost:8000/docs
uvicorn app.main:app --reload

# tests
.venv\Scripts\python -m pytest -q
```

**Alta de una estrategia:**
1. `POST /admin/strategies` con el perfil (puedes usar
   `data/schemas/strategy_profile.example.MES.json`). Nace `disabled`; la respuesta
   trae `token` y `url_unica` generados.
2. `POST /admin/strategies/{id}/enable` para habilitarla.
3. Apunta el webhook de la alerta LuxAlgo a `url_unica` → `https://<host>/hook/<token>`.

Toda señal (aceptada/rechazada/duplicada) queda en el audit log (`GET /admin/audit`).
