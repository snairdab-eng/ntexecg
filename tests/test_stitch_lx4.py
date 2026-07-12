"""LX-4 — costura HOLC por default: gate del flujo web, umbral fail-honest del
solape CSV↔DB, banner con datos del manifest, y verificación del updater.

Sin Postgres: `stitch_from_db` se prueba parcheando `AsyncSessionLocal` con una
sesión falsa que devuelve barras sintéticas (criterio 'test env sin Postgres').
"""
from datetime import datetime, timedelta

import pytest

import scripts.lab_analyze as la
import scripts.mr_luxy as mrl


# ── sesión DB falsa (sin Postgres) ──────────────────────────────────────────

class _FakeBar:
    def __init__(self, ts, close):
        self.bar_time = ts
        self.open = self.high = self.low = self.close = close
        self.volume = 0


class _FakeScalars:
    def __init__(self, rows): self._r = rows
    def all(self): return self._r


class _FakeResult:
    def __init__(self, rows): self._r = rows
    def scalars(self): return _FakeScalars(self._r)


class _FakeSession:
    def __init__(self, rows): self._r = rows
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def execute(self, q): return _FakeResult(self._r)


def _patch_db(monkeypatch, rows):
    import app.db.session as s
    monkeypatch.setattr(s, "AsyncSessionLocal", lambda: _FakeSession(rows), raising=False)


_T0 = datetime(2026, 7, 4, 20, 0)


def _grid(n, close=100.0):
    return {_T0 + timedelta(minutes=5 * i): (close, close, close, close, 0)
            for i in range(n)}


# ── umbral fail-honest del solape ───────────────────────────────────────────

def test_umbral_es_constante_nombrada():
    assert la.STITCH_MAX_INCONSISTENTES_PCT == 0.01     # 0.01% = 1 en 10.000


@pytest.mark.asyncio
async def test_stitch_db_vacia_procede_con_aviso(monkeypatch):
    _patch_db(monkeypatch, [])
    bars = _grid(2)
    out, stats = await la.stitch_from_db(bars, "ES", "5m")
    assert stats["added"] == 0 and stats["checked"] == 0 and stats["mismatched"] == 0
    assert out is bars                                  # no inventa datos


@pytest.mark.asyncio
async def test_stitch_cose_la_cola_consistente(monkeypatch):
    bars = _grid(2, close=101.0)                        # HOLC hasta _T0+5m
    t_last = max(bars)
    t_tail = t_last + timedelta(minutes=5)
    rows = [_FakeBar(t_last, 101.0), _FakeBar(t_tail, 102.0)]  # solape ok + cola
    _patch_db(monkeypatch, rows)
    out, stats = await la.stitch_from_db(bars, "ES", "5m")
    assert stats["added"] == 1 and stats["checked"] == 1 and stats["mismatched"] == 0
    assert t_tail in out and stats["last_stitched"] == t_tail.isoformat()


@pytest.mark.asyncio
async def test_stitch_aborta_por_encima_del_umbral(monkeypatch):
    bars = _grid(100)                                   # 100 barras HOLC
    keys = sorted(bars)
    rows = [_FakeBar(t, 100.0) for t in keys[:-1]] + [_FakeBar(keys[-1], 200.0)]
    _patch_db(monkeypatch, rows)                        # 1/100 = 1% > 0.01%
    with pytest.raises(SystemExit):
        await la.stitch_from_db(bars, "ES", "5m")


@pytest.mark.asyncio
async def test_stitch_procede_por_debajo_del_umbral(monkeypatch):
    bars = _grid(10000)                                 # 1/10000 = 0.01% (no >)
    keys = sorted(bars)
    rows = [_FakeBar(t, 100.0) for t in keys[:-1]] + [_FakeBar(keys[-1], 200.0)]
    _patch_db(monkeypatch, rows)
    out, stats = await la.stitch_from_db(bars, "ES", "5m")
    assert stats["mismatched"] == 1 and stats["pct"] == 0.01


# ── gate del flujo web (default ON salvo APP_ENV=test) ──────────────────────

def test_stitch_gate_default_on_salvo_test(monkeypatch):
    import app.web.routes_riesgo as rr
    from app.core.config import settings
    monkeypatch.delenv("MR_CALC_STITCH", raising=False)
    monkeypatch.delenv("LAB_RECALC_STITCH", raising=False)
    monkeypatch.setattr(settings, "APP_ENV", "test")
    assert rr._stitch() is False                        # test → apagada
    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert rr._stitch() is True                         # prod → costura por default
    monkeypatch.setenv("MR_CALC_STITCH", "0")
    assert rr._stitch() is False                        # env = override explícito


def test_integrar_y_calcular_cmd_llevan_stitch(monkeypatch):
    import app.web.routes_riesgo as rr
    from pathlib import Path
    monkeypatch.setattr(rr, "_stitch", lambda: True)
    assert "--stitch-db" in rr._integrar_cmd(Path("x.csv"), "cod", "ES")
    assert "--stitch-db" in rr._calc_cmd("ES_x")
    monkeypatch.setattr(rr, "_stitch", lambda: False)
    assert "--stitch-db" not in rr._integrar_cmd(Path("x.csv"), "cod", "ES")
    assert "--stitch-db" not in rr._calc_cmd("ES_x")


# ── banner con datos del manifest (cola vs inicio) ──────────────────────────

def test_banner_distingue_cola_e_inicio():
    # LX-5 — desglose por causa desde los datos del estudio (cola vs inicio)
    b = mrl.muestra_banner(121, 102, 16, 3, "2026-07-10T21:00:00")
    assert "16 en la cola posterior a la última barra cosida (2026-07-10T21:00:00)" in b
    assert "reintegra cuando el updater alcance" in b
    assert "3 previos al inicio del almacén" in b
    assert "Crudo+ los excluye de la simulación" in b
    # sin desglose → resto genérico, pero enciende igual (n_simulable < n_total)
    b2 = mrl.muestra_banner(121, 102)
    assert "19 de 121" in b2 and "cobertura HOLC almacenada en NTEXECG" in b2
    # cubierto → sin banner (nunca cuenta estimados como simulables)
    assert mrl.muestra_banner(120, 120, 0, 0) is None


# ── criterio 6 — verificación del updater (sin arreglar aquí) ───────────────

def test_market_bars_updater_cubre_catalogo_activo():
    import inspect
    from app.core.scheduler import MarketBarsUpdater
    assert MarketBarsUpdater._TIMEFRAMES == ("5m", "15m", "1h", "4h")
    src = inspect.getsource(MarketBarsUpdater._run)
    assert "SymbolMap.active.is_(True)" in src          # itera SOLO activos
    assert "resolve_market_data_symbol" in src          # resuelve símbolo de datos
    assert "market_bars_fetch_failed" in src            # reporta fallos (no dropea)
