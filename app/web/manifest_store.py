"""Fuente ÚNICA del manifest CSV↔estrategia (LAB-1).

`REPORTES/lab_manifest.json` es COMPARTIDO entre el Lab (visor) y Riesgo
(motor). Antes cada ruta lo leía/escribía con código propio y solo Riesgo
usaba lock — dos verdades para el mismo archivo. Aquí viven las tres piezas
compartidas: `load_manifest()`, `guardar_manifest()` y el lock por estrategia
(`lock_integrar`). Ambas rutas las importan; cero duplicación.

El directorio es `routes_lab.LAB_DIR` resuelto EN CADA LLAMADA (los tests
monkeypatchean ese símbolo); por eso se importa de forma diferida dentro de
las funciones y no como constante de módulo.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path


def _manifest_path() -> Path:
    # LAB_DIR es patcheable en tests (monkeypatch de routes_lab.LAB_DIR); se
    # lee al vuelo. Import diferido para evitar el ciclo routes_lab↔store.
    import app.web.routes_lab as routes_lab

    return routes_lab.LAB_DIR / "lab_manifest.json"


def load_manifest() -> dict:
    """entries del manifest CSV↔estrategia; {} si no hay manifest o no parsea."""
    p = _manifest_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("entries") or {}
    except (ValueError, OSError):
        return {}


def guardar_manifest(manifest: dict) -> None:
    """Persiste las entries conservando el resto del documento (version, …)."""
    p = _manifest_path()
    data = (json.loads(p.read_text(encoding="utf-8"))
            if p.exists() else {"version": 1})
    data["entries"] = manifest
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False),
                 encoding="utf-8")


# P1-4 — integrar/guardar SERIALIZADO por estrategia: dos subidas simultáneas
# de la misma clave no compiten por el manifest (last-writer-wins mudo). El
# dict es el estado compartido; los tests lo limpian con .clear().
_INTEGRAR_LOCKS: dict[str, asyncio.Lock] = {}


def lock_integrar(strategy: str) -> asyncio.Lock:
    return _INTEGRAR_LOCKS.setdefault(strategy, asyncio.Lock())
