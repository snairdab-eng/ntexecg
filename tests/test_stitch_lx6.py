"""LX-6 — fix de la cola cosida (TZ) y endurecimiento fail-closed del stitch.

`stitch_from_db` se prueba parcheando `AsyncSessionLocal` con una sesión falsa
(sin Postgres). El fix es fail-closed: una cola sin solape verificable, con solape
insuficiente, o con un salto, ABORTA en vez de cosurar a ciegas.
"""
from datetime import datetime, timedelta, timezone

import pytest

import scripts.lab_analyze as la
import scripts.mr_luxy as mrl
from tests.test_stitch_lx4 import _FakeBar, _patch_db, _grid, _T0


# ── normalización de TZ en LECTURA ──────────────────────────────────────────

def test_et_naive_convierte_aware_y_respeta_naive():
    # naive → se asume ET (se deja igual)
    n = datetime(2026, 7, 9, 12, 0)
    assert la._et_naive(n) == n
    # tz-aware UTC → America/New_York → naive. 16:00 UTC = 12:00 EDT (verano)
    aware = datetime(2026, 7, 9, 16, 0, tzinfo=timezone.utc)
    got = la._et_naive(aware)
    assert got.tzinfo is None and got == datetime(2026, 7, 9, 12, 0)


@pytest.mark.asyncio
async def test_stitch_normaliza_cola_utc_aware(monkeypatch):
    # HOLC ET; la DB entrega la cola tz-aware UTC → tras normalizar, encaja ET.
    bars = _grid(15, close=101.0)
    keys = sorted(bars); t_last = keys[-1]
    # solape: mismas keys ET, pero entregadas como UTC-aware (+4h respecto a ET)
    rows = [_FakeBar(k + timedelta(hours=4), bars[k][3]) for k in keys]
    for r in rows:                                     # marcar tz-aware UTC
        r.bar_time = r.bar_time.replace(tzinfo=timezone.utc)
    tail = t_last + timedelta(minutes=5)
    rows.append(_FakeBar((tail + timedelta(hours=4)).replace(tzinfo=timezone.utc), 102.0))
    _patch_db(monkeypatch, rows)
    out, stats = await la.stitch_from_db(bars, "ES", "5m")
    assert stats["checked"] == 15 and stats["added"] == 1
    assert tail in out                                 # normalizada a ET, no +4h


# ── fail-closed ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stitch_solape_vacio_aborta(monkeypatch):
    # cola presente pero SIN solape (keys no coinciden) → no verificable → abort
    bars = _grid(15, close=101.0)
    t_tail = max(bars) + timedelta(minutes=5)
    rows = [_FakeBar(t_tail, 102.0)]                   # sólo cola, checked=0
    _patch_db(monkeypatch, rows)
    with pytest.raises(SystemExit):
        await la.stitch_from_db(bars, "ES", "5m")


@pytest.mark.asyncio
async def test_stitch_solape_insuficiente_aborta(monkeypatch):
    # cola presente con solape < STITCH_MIN_OVERLAP_BARS → abort
    bars = _grid(15, close=101.0)
    keys = sorted(bars)
    rows = [_FakeBar(k, bars[k][3]) for k in keys[:5]]  # sólo 5 solapan
    rows.append(_FakeBar(max(bars) + timedelta(minutes=5), 102.0))
    _patch_db(monkeypatch, rows)
    assert la.STITCH_MIN_OVERLAP_BARS > 5
    with pytest.raises(SystemExit):
        await la.stitch_from_db(bars, "ES", "5m")


@pytest.mark.asyncio
async def test_stitch_salto_en_la_costura_aborta(monkeypatch):
    # solape suficiente pero la cola SALTA más que la rejilla de sesión → abort
    bars = _grid(15, close=101.0)
    keys = sorted(bars)
    rows = [_FakeBar(k, bars[k][3]) for k in keys]     # 15 solape ok
    salto = max(bars) + timedelta(hours=8)             # >> rejilla 5m
    rows.append(_FakeBar(salto, 102.0))
    _patch_db(monkeypatch, rows)
    with pytest.raises(SystemExit):
        await la.stitch_from_db(bars, "ES", "5m")


@pytest.mark.asyncio
async def test_stitch_db_vacia_sin_cola_no_aborta(monkeypatch):
    # sin cola (DB vacía) → NO aborta aunque checked=0 (LX-4: no inventa datos)
    _patch_db(monkeypatch, [])
    out, stats = await la.stitch_from_db(_grid(15), "ES", "5m")
    assert stats["added"] == 0 and stats["checked"] == 0


# ── tripwire de plausibilidad ───────────────────────────────────────────────

def test_tripwire_on_off():
    legs = ((0.0, 0.5), (1.6, 0.3), (3.2, 0.2))        # C1 al mercado (depth 0)
    # sano: dir both, participación 100, PF 2.0 → plausible
    impl, msg = mrl.tripwire_implausible(legs, None, 100.0, 2.0)
    assert impl is False and msg is None
    # PF absurda → implausible
    impl, msg = mrl.tripwire_implausible(legs, None, 100.0, 184.8)
    assert impl is True and "PF 184.8" in msg
    # participación baja con C1 al mercado y sin corte → implausible
    impl, msg = mrl.tripwire_implausible(legs, None, 52.1, 2.0)
    assert impl is True and "52.1%" in msg
    # participación baja PERO con corte de lado → NO dispara (es legítimo)
    impl, _ = mrl.tripwire_implausible(legs, "cortar", 52.1, 2.0)
    assert impl is False
    assert mrl.PF_ABSURDO == 50.0 and mrl.PART_MIN_PLAUSIBLE == 90.0
