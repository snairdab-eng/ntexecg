"""Export READ-ONLY de la config viva para el respaldo — SIN secretos.

Pregunta que responde: ¿cómo reconstruyo el estado de config que aplicamos
(ventanas, brackets, modos, catálogo de instrumentos) a partir de un snapshot
legible, sin llevarme ni un secreto?

Genera un `ntexecg_config_YYYY-MM-DD.json` (una clave por tabla, ordenado por
id, determinista) en el CWD (home/repo del server, igual que
audit_bracket_post_apply.py).

TABLAS INCLUIDAS (config que reconstruye el estado):
  - strategies        (catálogo: name/status/asset/mode/enabled...) [1]
  - strategy_profiles (el corazón: pipeline_config_json, mode, dry_run, SL/TP,
                       ventanas, riesgo)
  - asset_profiles    (session_config_json, SL/TP, riesgo por activo)
  - symbol_maps       (catálogo de instrumentos: tick_value/tick_size, mapeo)
  - global_profile    (defaults del sistema)
  - audit_logs        SOLO action == "APPLY_RIESGO_RECO" (historial de qué
                       bracket se aplicó y cuándo)

  [1] `strategies` no está en la lista literal del encargo, pero es config (no
      runtime) y es el padre de strategy_profiles: sin él, strategy_profiles
      referencia un strategy_id huérfano. Además es donde viven el token/hash
      por estrategia que el encargo pide escrubir. Se incluye con esas columnas
      REDACTADAS. Si el operador no lo quiere, quitar "strategies" de EXPORTS.

TABLAS EXCLUIDAS por completo (runtime/regenerable/sensible): StrategyDecision,
WebhookDelivery, RawSignal, NormalizedSignal, PositionState, MarketDataStatus,
ConflictLog, ExecutionResult, OhlcvBar, StrategyPerformance, StrategyTemplate y
cualquier tabla de usuarios/auth.

SCRUB de secretos (obligatorio):
  - Columnas explícitamente sensibles (REDACT_COLUMNS): la URL de webhook de
    TradersPost (lleva token en la URL) y el token/hash por estrategia. Se
    reemplazan por "<REDACTED>" si tenían valor, o null si eran null.
  - Defensa en profundidad sobre CUALQUIER blob JSON: se recorre recursivamente
    y se redacta todo valor cuya CLAVE parezca sensible (token/secret/hash/
    webhook_url/password/credential/bearer/api_key) o cuyo VALOR parezca un
    secreto real (URL http(s), "Bearer ...", hash hex largo).

INVARIANTES: solo lectura. SELECT únicamente. No abre transacciones de
escritura. No commitea. No despacha nada.

VERIFICACIÓN (obligatoria, se reporta): tras escribir el JSON se re-lee del
disco, se recorren todos los VALORES buscando patrones de secreto real, y se
cuentan las apariciones crudas de (http, token, secret, webhook, hash, Bearer).
Si aparece cualquier VALOR real de secreto → NO se entrega: el archivo se
renombra a *.QUARANTINE.json y el script sale con código 1. Si está limpio,
imprime conteo de filas por tabla y tamaño.

Uso:
    .venv/Scripts/python.exe scripts/backup_config_dump.py
    (apunta DATABASE_URL a la BD que corresponda exportar — la viva en el server)
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from uuid import UUID

# La consola de Windows (cp1252) no encodea símbolos — forzar UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.asset_profile import AssetProfile
from app.models.audit_log import AuditLog
from app.models.global_profile import GlobalProfile
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.models.symbol_map import SymbolMap

REDACTED = "<REDACTED>"

# Solo se exporta la config que reconstruye el estado. NADA de runtime.
# (tabla_json_key, modelo, columna_orden)
EXPORTS: list[tuple[str, type, str]] = [
    ("strategies", Strategy, "strategy_id"),
    ("strategy_profiles", StrategyProfile, "strategy_id"),
    ("asset_profiles", AssetProfile, "symbol"),
    ("symbol_maps", SymbolMap, "tv_symbol"),
    ("global_profile", GlobalProfile, "id"),
    ("audit_logs", AuditLog, "created_at"),
]

# Solo estos eventos del audit se exportan (historial de bracket aplicado).
AUDIT_ACTIONS_INCLUDED = {"APPLY_RIESGO_RECO"}

# Columnas que SIEMPRE se redactan, por tabla. Identificadas inspeccionando
# app/models/: la URL de TradersPost lleva token en la ruta; webhook_token(_hash)
# autentican los webhooks entrantes por estrategia.
REDACT_COLUMNS: dict[str, set[str]] = {
    "strategies": {"webhook_token", "webhook_token_hash", "traderspost_webhook_url"},
    "strategy_profiles": {"traderspost_webhook_url"},
}

# Claves sospechosas dentro de blobs JSON (defensa en profundidad).
_SENSITIVE_KEY_PARTS = (
    "token", "secret", "password", "passwd", "credential", "webhook_url",
    "authorization", "bearer", "api_key", "apikey", "private_key", "hash",
)

_HEX_TOKEN = re.compile(r"^[0-9a-f]{32,}$")
# Patrones para el grep crudo del reporte (informativo).
_RAW_PATTERNS = ("http", "token", "secret", "webhook", "hash", "bearer")


def _key_is_sensitive(key: str) -> bool:
    k = str(key).lower()
    return any(part in k for part in _SENSITIVE_KEY_PARTS)


def _value_looks_secret(value) -> bool:
    """¿Este VALOR (no la clave) parece un secreto real? Se usa tanto para el
    scrub de blobs JSON como para la verificación final."""
    if not isinstance(value, str):
        return False
    if value == REDACTED:
        return False
    low = value.strip().lower()
    if low.startswith(("http://", "https://")):
        return True
    if low.startswith("bearer "):
        return True
    if _HEX_TOKEN.match(low):  # hash/token hex largo
        return True
    return False


def _scrub_json(value):
    """Recorre recursivamente un blob JSON y redacta secretos: por clave
    sospechosa o por valor que parezca secreto. Devuelve una copia escrubada."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if _key_is_sensitive(k) and v is not None:
                out[k] = REDACTED
            else:
                out[k] = _scrub_json(v)
        return out
    if isinstance(value, list):
        return [_scrub_json(v) for v in value]
    if _value_looks_secret(value):
        return REDACTED
    return value


def _jsonable(value):
    """Convierte tipos SQLAlchemy/Python a algo serializable y determinista."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        # str preserva la escala exacta del Numeric (no float lossy).
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return value  # ya escrubado antes de llegar aquí
    return str(value)


def _serialize_row(model: type, table_key: str, obj) -> dict:
    redact = REDACT_COLUMNS.get(table_key, set())
    row: dict = {}
    for col in model.__table__.columns:  # orden de definición → determinista
        name = col.name
        raw = getattr(obj, name)
        if name in redact:
            row[name] = REDACTED if raw is not None else None
            continue
        if isinstance(raw, (dict, list)):
            row[name] = _jsonable(_scrub_json(raw))
        else:
            row[name] = _jsonable(raw)
    return row


async def _fetch(db, model: type, order_col: str, table_key: str) -> list[dict]:
    stmt = select(model)
    if table_key == "audit_logs":
        stmt = stmt.where(AuditLog.action.in_(AUDIT_ACTIONS_INCLUDED))
    stmt = stmt.order_by(getattr(model, order_col))
    objs = (await db.execute(stmt)).scalars().all()
    return [_serialize_row(model, table_key, o) for o in objs]


def _walk_values(node, path="$"):
    """Genera (path, value) de todo VALOR escalar del árbol JSON (no claves)."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_values(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_values(v, f"{path}[{i}]")
    else:
        yield path, node


def _verify(file_path: Path) -> list[tuple[str, str]]:
    """Re-lee el archivo del disco, recorre TODOS los valores y devuelve la
    lista de (path, valor) que parecen un secreto real. Vacía == limpio."""
    text = file_path.read_text(encoding="utf-8")
    data = json.loads(text)
    leaks: list[tuple[str, str]] = []
    for path, value in _walk_values(data):
        if _value_looks_secret(value):
            leaks.append((path, value))
    return leaks


def _raw_pattern_counts(file_path: Path) -> dict[str, int]:
    """Conteo crudo (informativo) de patrones sobre TODO el texto — incluye
    nombres de columna como claves, por eso aparecerán aunque sus valores estén
    redactados. Se reporta para transparencia."""
    text = file_path.read_text(encoding="utf-8").lower()
    return {p: text.count(p) for p in _RAW_PATTERNS}


async def main() -> None:
    out_name = f"ntexecg_config_{date.today().isoformat()}.json"
    out_path = Path.cwd() / out_name

    export: dict = {}
    counts: dict[str, int] = {}

    async with AsyncSessionLocal() as db:  # sesión de solo lectura; sin commit
        for table_key, model, order_col in EXPORTS:
            rows = await _fetch(db, model, order_col, table_key)
            export[table_key] = rows
            counts[table_key] = len(rows)

    payload = {
        "_meta": {
            "generated_at": datetime.now().astimezone().isoformat(),
            "generator": "scripts/backup_config_dump.py",
            "read_only": True,
            "tables_included": [k for k, _, _ in EXPORTS],
            "audit_actions_included": sorted(AUDIT_ACTIONS_INCLUDED),
            "redacted_columns": {k: sorted(v) for k, v in REDACT_COLUMNS.items()},
            "row_counts": counts,
            "note": (
                "Snapshot de config viva SIN secretos. Columnas sensibles y "
                "cualquier secreto embebido en blobs JSON van como '<REDACTED>'."
            ),
        },
        **export,
    }

    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # ---- Verificación obligatoria ----
    print("=" * 78)
    print("EXPORT DE CONFIG — VERIFICACIÓN DE SCRUB (solo lectura)")
    print("=" * 78)
    print(f"\nArchivo   : {out_path}")
    size = out_path.stat().st_size
    print(f"Tamaño    : {size:,} bytes")
    print("\nFilas por tabla:")
    for table_key, _, _ in EXPORTS:
        print(f"  · {table_key:18s}: {counts[table_key]}")

    raw = _raw_pattern_counts(out_path)
    print("\nGrep crudo (informativo — incluye NOMBRES de columna como claves,")
    print("por eso aparecen aunque sus valores estén redactados):")
    for p in _RAW_PATTERNS:
        print(f"  · patrón '{p}'    apariciones (texto): {raw[p]}")

    leaks = _verify(out_path)
    print("\nEscaneo de VALORES reales que parezcan secreto "
          "(URL http, Bearer, hash hex largo):")
    if not leaks:
        print("  ✓ 0 valores de secreto real. Scrub OK.")
        print("\n" + "=" * 78)
        print("RESULTADO: LIMPIO — apto para entregar al respaldo.")
        print("=" * 78)
        return

    # Fuga: NO se entrega. Se pone en cuarentena.
    q_path = out_path.with_suffix(".QUARANTINE.json")
    out_path.replace(q_path)
    print(f"  ✗ {len(leaks)} VALOR(es) sospechoso(s) de secreto REAL:")
    for path, value in leaks[:50]:
        preview = value if len(str(value)) <= 60 else f"{str(value)[:57]}..."
        print(f"      {path} = {preview!r}")
    print("\n" + "=" * 78)
    print("RESULTADO: FUGA DETECTADA — NO ENTREGAR.")
    print(f"Archivo movido a cuarentena: {q_path}")
    print("Ampliar el scrub (REDACT_COLUMNS / _SENSITIVE_KEY_PARTS) y re-correr.")
    print("=" * 78)
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
