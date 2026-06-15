"""Manual PostgreSQL backup → compressed SQL dump.

Usage:
    python scripts/backup_db.py

Creates backups/ntexecg_{YYYYMMDD_HHMMSS}.sql.gz using pg_dump, reading the
connection from .env (DATABASE_URL). Prints the backup file path and size.

Requires pg_dump in PATH (PostgreSQL client tools).
"""
from __future__ import annotations

import gzip
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).parent.parent))


def _parse_dsn(database_url: str) -> dict:
    """Extract pg_dump connection params from a SQLAlchemy async URL."""
    # postgresql+asyncpg://user:pass@host:port/dbname  → strip the +driver
    clean = database_url.replace("+asyncpg", "").replace("+psycopg2", "")
    parsed = urlparse(clean)
    return {
        "user": unquote(parsed.username or "postgres"),
        "password": unquote(parsed.password or ""),
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "dbname": (parsed.path or "/postgres").lstrip("/"),
    }


def main() -> int:
    from app.core.config import settings

    if shutil.which("pg_dump") is None:
        print("❌ pg_dump no está en PATH. Instala las PostgreSQL client tools.")
        return 1

    dsn = _parse_dsn(settings.DATABASE_URL)

    backups_dir = Path(__file__).parent.parent / "backups"
    backups_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = backups_dir / f"ntexecg_{timestamp}.sql.gz"

    cmd = [
        "pg_dump",
        "-h", dsn["host"],
        "-p", dsn["port"],
        "-U", dsn["user"],
        "-d", dsn["dbname"],
        "--no-owner",
        "--no-acl",
    ]
    env = {"PGPASSWORD": dsn["password"]}

    print(f"Respaldando '{dsn['dbname']}' desde {dsn['host']}:{dsn['port']}…")
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**_os_environ(), **env},
        )
        with gzip.open(out_path, "wb") as gz:
            assert proc.stdout is not None
            shutil.copyfileobj(proc.stdout, gz)
        _, stderr = proc.communicate()
    except Exception as exc:
        print(f"❌ Backup falló: {exc}")
        return 1

    if proc.returncode != 0:
        print(f"❌ pg_dump falló (code {proc.returncode}): "
              f"{stderr.decode(errors='replace')[:400]}")
        if out_path.exists():
            out_path.unlink()
        return 1

    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"✅ Backup creado: {out_path}")
    print(f"   Tamaño: {size_mb:.2f} MB")
    return 0


def _os_environ() -> dict:
    import os
    return dict(os.environ)


if __name__ == "__main__":
    raise SystemExit(main())
