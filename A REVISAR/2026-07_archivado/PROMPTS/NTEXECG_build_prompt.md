# Prompt de construcción — NTEXECG

> **Cómo usar este prompt:** pégalo en Claude Code (o en una sesión de Claude con acceso a archivos) y **adjunta los 4 archivos del modelo de datos**: `instrument_catalog.json`, `strategy_profile.schema.json`, `account_profile.schema.json` y `strategy_profile.example.MES.json`. Esos archivos son la fuente de verdad del modelo de datos; este prompt define el comportamiento.

---

## Rol y objetivo

Eres un ingeniero backend senior. Construye **NTEXECG**, una capa intermedia que recibe señales de trading de LuxAlgo (vía webhook), las **valida y filtra por calidad**, y solo si pasan todas las validaciones las **reenvía** a TradersPost para ejecución. TradersPost es un ejecutor "tonto": NTEXECG decide qué es válido.

Flujo: `LuxAlgo (alerta JSON) → NTEXECG (compuerta) → TradersPost (ejecuta a broker)`.

## Alcance — qué NO hacer

- **No** reimplementes la ejecución de órdenes, el ruteo interno de TradersPost, ni la conexión a brokers. NTEXECG solo **reenvía** el webhook a una URL de TradersPost.
- **No** reintentes automáticamente reenvíos fallidos (evita órdenes duplicadas). Loguea y, opcionalmente, alerta.
- **No** asumas defaults silenciosos: si falta un campo crítico (p. ej. `quantity`), rechaza.

## Stack

- **Python 3.11+**, **FastAPI**, **Pydantic v2**.
- **SQLite** para persistencia en la fase inicial, **detrás de una interfaz/repositorio** para poder migrar a PostgreSQL sin tocar la lógica de negocio.
- **pytest** para pruebas.
- Validación de perfiles con la librería `jsonschema` contra los esquemas provistos, o modelos Pydantic equivalentes derivados de ellos (mantener consistencia con los `.schema.json`).

## Modelo de datos (usar los archivos adjuntos)

Tres entidades:
1. **Strategy profile** — definido por `strategy_profile.schema.json`. Una por estrategia.
2. **Account profile** — definido por `account_profile.schema.json`. Varias estrategias referencian una cuenta. **Aquí vive el riesgo diario por cuenta.**
3. **Instrument catalog** — datos semilla en `instrument_catalog.json`. Precárgalo. La estrategia referencia `simbolo_esperado` y NTEXECG **autocompleta** `tipo_contrato`, `valor_tick`, `tick_minimo` desde el catálogo (no se teclean).

Respeta las anotaciones `x-source` (system/user/catalog/validation) y `x-validation` de los esquemas al construir formularios y validaciones.

## Endpoint principal — contrato

`POST /hook/{token}` · `Content-Type: application/json` · origen: alertas de LuxAlgo/TradingView (solo body, sin headers de auth).

**Payload de entrada:**
```json
{ "ticker": "MES", "action": "buy", "sentiment": "long", "quantity": "2",
  "price": "5432.75", "time": "2026-06-19T13:40:00.000Z", "interval": "15" }
```

**Clasificación entrada vs salida** (para la regla de ventana):
- Entrada: abre/incrementa/revierte (`buy/long`, `sell/short`).
- Salida: cierre (`sentiment: flat` o exit explícito). **Las salidas nunca se bloquean por ventana.**

## Pipeline de validación (orden estricto)

Ejecutar en este orden. **Primer fallo: rechazar + loguear motivo + NO reenviar.**

| # | Check | Regla | Si falla |
|---|---|---|---|
| 1 | Estrategia | `{token}` resuelve una estrategia | `404` `UNKNOWN_TOKEN` |
| 2 | Estado | `estado == enabled` | `409` `STRATEGY_DISABLED` |
| 3 | Símbolo | `payload.ticker == simbolo_esperado` | `422` `SYMBOL_MISMATCH` |
| 4 | Temporalidad | `int(payload.interval) == temporalidad_esperada_min` | `422` `INTERVAL_MISMATCH` |
| 5 | Antigüedad | `now - payload.time ≤` umbral (entrada/salida) | `422` `SIGNAL_STALE` |
| 6 | Dedup | no hubo señal idéntica dentro de `dedup_seg` | `200` `DUPLICATE_IGNORED` |
| 7 | Ventana | si entrada: cae en una ventana activa para el día (en `timezone`); si salida: permitida | `422` `OUTSIDE_WINDOW` |
| 8 | Cantidad | `payload.quantity ≤ cantidad_maxima_contratos` | `422` `QTY_EXCEEDS_MAX` |
| 9 | Riesgo | `quantity × stop_ticks × valor_tick ≤ riesgo_usd_max_operacion`; stop presente si `stop_obligatorio` | `422` `RISK_EXCEEDS_MAX` / `STOP_REQUIRED` |
| 10 | Reenvío | `POST` al `webhook_traderspost` de la estrategia | `502` `FORWARD_FAILED` |

Notas: `stop_ticks` viene del payload si se manda, si no de `stop_esperado_ticks`. `valor_tick`/`tipo_contrato` salen del catálogo, no del payload. El paso 6 responde `200` (no es error del emisor) pero se loguea.

**Respuestas:**
```json
// aceptada
{ "status": "accepted", "strategy_id": "...", "forwarded": true, "request_id": "...", "ts": "..." }
// rechazada
{ "status": "rejected", "reason_code": "SYMBOL_MISMATCH", "detail": "expected MES, got ES", "strategy_id": "...", "request_id": "...", "ts": "..." }
```
Códigos HTTP: `200` aceptada/duplicada · `404` token desconocido · `409` deshabilitada · `422` validación · `502` fallo de reenvío.

## Reglas innegociables (invariantes)

1. **Identidad por endpoint:** 1 token de URL = 1 estrategia. El payload no trae id ni auth; el token es identidad y llave. Token aleatorio y largo (UUID v4 / 32+ bytes), comparado de forma segura. Token desconocido → `404` sin filtrar detalle.
2. **Nace `disabled`:** al crear una estrategia genera la URL pero el `estado` arranca en `disabled`. NTEXECG **no reenvía** hasta que esté `enabled` explícitamente, aunque ya lleguen señales.
3. **Salidas siempre permitidas:** las salidas no se bloquean por ventana.
4. **Sin reintentos** de reenvío.
5. **Riesgo diario por cuenta:** vive en el account profile, no en la estrategia. Si `dueno_riesgo_diario == "ntexecg"`, NTEXECG debe poder agregar el riesgo de todas las estrategias que apuntan a esa cuenta (diseñar el estado para ello, aunque la lógica de bloqueo diario se implemente en una iteración posterior; dejar el hook listo).
6. **Instrumento desde catálogo:** nunca teclear valor_tick; resolver desde el catálogo por símbolo.
7. **Loguear toda señal** (aceptada, rechazada o duplicada) para auditoría.

## Endpoints de administración (backend del "alta")

CRUD para gestionar perfiles (esto soporta el formulario de alta manual):
- Crear/editar/listar **strategy profiles** → al crear, generar `token` + `url_unica` y devolverlos; estado inicial `disabled`.
- Habilitar/deshabilitar una estrategia.
- Crear/editar/listar **account profiles**.
- Leer el **catálogo** de instrumentos (para poblar el formulario y autocompletar).
- Validar un strategy profile contra el JSON Schema antes de guardar.

## Logging / auditoría

Registrar por cada petición: `request_id`, `received_at`, `strategy_id` (o null), `payload` crudo, `result` (accepted/rejected/duplicate), `reason_code`, `forwarded`, `forward_status`, `latency_ms`. Persistir en tabla consultable.

## Estructura de proyecto sugerida

```
ntexecg/
  app/
    main.py                 # FastAPI app, rutas
    config.py
    models/                 # Pydantic: StrategyProfile, AccountProfile, Instrument, SignalPayload
    db/
      repository.py         # interfaz
      sqlite_repo.py        # implementación SQLite
    pipeline/
      validators.py         # cada check del pipeline como función pura
      runner.py             # orquesta el orden y corta al primer fallo
    services/
      forwarder.py          # POST a TradersPost (sin retry)
      catalog.py            # carga/consulta del catálogo
      tokens.py             # generación/validación de tokens
    logging/
      audit.py
  data/
    instrument_catalog.json # semilla (adjunto)
    schemas/                # los .schema.json (adjuntos)
  tests/
    test_pipeline.py
    test_endpoint.py
  README.md
```

## Pruebas / criterios de aceptación

Implementa tests que cubran (mínimo):
1. Señal válida (símbolo, temporalidad, dentro de ventana, riesgo OK) → **aceptada y reenviada**.
2. `ticker` distinto al `simbolo_esperado` → **rechazada** `SYMBOL_MISMATCH`.
3. `interval` distinto → **rechazada** `INTERVAL_MISMATCH`.
4. Señal vieja (supera antigüedad) → **rechazada** `SIGNAL_STALE`.
5. Entrada fuera de ventana → **rechazada** `OUTSIDE_WINDOW`; **salida** fuera de ventana → **procesada**.
6. `quantity` mayor al máximo → **rechazada** `QTY_EXCEEDS_MAX`.
7. Riesgo (`qty × stop × tick`) supera el máximo → **rechazada** `RISK_EXCEEDS_MAX`.
8. Estrategia `disabled` → **rechazada** `STRATEGY_DISABLED`, aunque el token exista.
9. Señal duplicada dentro de `dedup_seg` → `DUPLICATE_IGNORED`, no reenvía.
10. Fallo de reenvío a TradersPost → `FORWARD_FAILED`, **sin reintento**, logueado.

Usa `strategy_profile.example.MES.json` como fixture base. Mockea el reenvío a TradersPost en los tests.

## Entregables esperados

- Código funcional con la estructura anterior.
- Catálogo precargado y esquemas integrados.
- Suite de pytest verde cubriendo los 10 criterios.
- `README.md` con cómo correr, cómo dar de alta una estrategia, y cómo apuntar la alerta de LuxAlgo a la URL generada.
- OpenAPI/Swagger disponible (FastAPI) para inspeccionar y probar el endpoint.

## Instrucciones de ejecución para ti (el constructor)

- Empieza confirmando que entiendes el pipeline y el modelo de datos; si algo del esquema es ambiguo, pregunta antes de codificar.
- Implementa primero el modelo + repositorio + catálogo, luego el pipeline (cada validador aislado y testeable), luego el endpoint, luego los endpoints de administración.
- Manténlo simple y legible: el valor de NTEXECG está en que las reglas sean obvias y auditables, no en abstracciones.
