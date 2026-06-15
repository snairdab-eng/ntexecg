#!/usr/bin/env bash
#
# mount_ntbridge.sh — Mount \\NTRADER\bridge → /mnt/ntbridge on NTEXECG (Ubuntu).
#
# Runs on the NTEXECG Ubuntu server ONLY — never on NTDEV (which uses yfinance).
# Mounts read-only via CIFS/Samba using /etc/ntbridge-credentials.
# Idempotent: checks if already mounted before mounting, and verifies the
# heartbeat file exists afterwards.
#
# Usage: ./scripts/mount_ntbridge.sh
#
# See doc 07 §3 for full server setup (fstab, credentials, cron remount).

set -euo pipefail

# ── Config (override via environment if NTRADER IP differs) ──────────────────
NTRADER_SHARE="${NTRADER_SHARE:-//192.168.1.100/bridge}"
MOUNT_POINT="${NTBRIDGE_MOUNT:-/mnt/ntbridge}"
CREDENTIALS="${NTBRIDGE_CREDENTIALS:-/etc/ntbridge-credentials}"
HEARTBEAT_SYMBOL="${NTBRIDGE_HEARTBEAT_SYMBOL:-MES}"
HEARTBEAT_FILE="${MOUNT_POINT}/heartbeat_${HEARTBEAT_SYMBOL}.json"

echo "── NTEXECG bridge mount ──"
echo "Share:       ${NTRADER_SHARE}"
echo "Mount point: ${MOUNT_POINT}"

# ── 1. Already mounted? ──────────────────────────────────────────────────────
if mountpoint -q "${MOUNT_POINT}"; then
    echo "✓ Ya montado en ${MOUNT_POINT}."
else
    # ── 2. Pre-flight checks ─────────────────────────────────────────────────
    if [[ ! -d "${MOUNT_POINT}" ]]; then
        echo "Creando punto de montaje ${MOUNT_POINT}…"
        sudo mkdir -p "${MOUNT_POINT}"
    fi

    if [[ ! -f "${CREDENTIALS}" ]]; then
        echo "❌ No existe el archivo de credenciales: ${CREDENTIALS}" >&2
        echo "   Créalo con username/password/domain y chmod 600 (ver doc 07 §3)." >&2
        exit 1
    fi

    # ── 3. Mount read-only ───────────────────────────────────────────────────
    echo "Montando (read-only)…"
    sudo mount -t cifs \
        "${NTRADER_SHARE}" \
        "${MOUNT_POINT}" \
        -o "credentials=${CREDENTIALS},ro,iocharset=utf8,vers=3.0,_netdev"
    echo "✓ Montado."
fi

# ── 4. Verify heartbeat file ─────────────────────────────────────────────────
if [[ -f "${HEARTBEAT_FILE}" ]]; then
    # Age in seconds (GNU stat)
    now=$(date +%s)
    mtime=$(stat -c %Y "${HEARTBEAT_FILE}")
    age=$((now - mtime))
    echo "✓ Heartbeat encontrado: ${HEARTBEAT_FILE} (edad: ${age}s)"
    if (( age > 60 )); then
        echo "⚠ Heartbeat tiene ${age}s (> 60s) — NinjaTrader podría estar inactivo." >&2
    fi
else
    echo "⚠ No se encontró ${HEARTBEAT_FILE}." >&2
    echo "  Verifica que NinjaTrader esté exportando en NTRADER." >&2
    exit 1
fi

echo "── Listo ──"
