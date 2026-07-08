"""LOTE DASH-1 — tabla del bridge sana (datos reales, unidades reales).

Candados verificados:
  - el HeartbeatMonitor persiste last_atr_1h y heartbeat_age_seconds (ya no
    quedan columnas muertas);
  - los ATR de FX se muestran en TICKS (Symbol Mapper tick_size), nunca '0.00';
  - sin edad de heartbeat → '—' (nada inventado);
  - una fila por símbolo de DATOS (micro y padre colapsan; badge de tradeables).
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.core.scheduler import HeartbeatMonitor
from app.models.market_data_status import MarketDataStatus
from app.models.symbol_map import SymbolMap
from app.web.routes_dashboard import _bridge_rows
from app.web.units import fmt_atr


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_bridge")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


def _sm(db: AsyncSession, tv: str, mapped: str, *, mds: str | None = None,
        tick: float | None = None) -> None:
    db.add(SymbolMap(
        tv_symbol=tv, mapped_symbol=mapped, market_data_symbol=mds,
        exchange="CME", contract_type="future",
        pine_script_config=f'"ticker": "{tv}"', tick_size=tick, active=True,
    ))


async def _status(db: AsyncSession, symbol: str, *, active=True,
                  atr_5m=None, atr_1h=None, hb=None) -> None:
    db.add(MarketDataStatus(
        symbol=symbol, provider="Test", is_active=active,
        last_atr_5m=atr_5m, last_atr_1h=atr_1h, heartbeat_age_seconds=hb,
    ))


# ---------------------------------------------------------------------------
# fmt_atr — la unidad (helper puro)
# ---------------------------------------------------------------------------

def test_fmt_atr_fx_en_ticks_nunca_cero():
    """FX (tick sub-céntimo): ATR en ticks, nunca '0.00'."""
    out = fmt_atr(3.6e-5, 5e-7)               # 6J: 0.000036 / 0.0000005
    assert "72 ticks" in out and "0.00" not in out
    assert fmt_atr(8.0, 0.25) == "8.00"        # índice: puntos, 2 decimales
    assert fmt_atr(None, 0.25) == "—"          # sin ATR
    assert fmt_atr(0.036, 5e-5) == "720 ticks (0.036)"


# ---------------------------------------------------------------------------
# HeartbeatMonitor — persiste atr_1h y heartbeat_age (mock provider)
# ---------------------------------------------------------------------------

class _MockBridgeSvc:
    """Servicio de datos falso: ATR distinto por timeframe + edad de heartbeat."""
    class _Prov:
        pass

    def __init__(self) -> None:
        self.provider = self._Prov()

    async def is_active(self, symbol: str) -> bool:
        return True

    async def get_atr(self, symbol: str, timeframe: str, period: int = 14):
        return {"5m": 8.0, "1h": 12.5}.get(timeframe)

    async def heartbeat_age(self, symbol: str) -> float | None:
        return 7.0


@pytest.mark.asyncio
async def test_monitor_persiste_atr1h_y_heartbeat(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sm(db, "MES", "MESU2025", mds="ES", tick=0.25)     # micro → datos de ES
    await db.commit()

    class _Ctx:
        async def __aenter__(self_) -> AsyncSession:
            return db

        async def __aexit__(self_, *a) -> bool:
            return False

    # el job abre su propia sesión (AsyncSessionLocal); apuntarla a la de test
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", lambda: _Ctx())

    monitor = HeartbeatMonitor(_MockBridgeSvc())
    await monitor._check()

    st = (await db.execute(
        select(MarketDataStatus).where(MarketDataStatus.symbol == "MES")
    )).scalar_one()
    assert float(st.last_atr_5m) == 8.0
    assert float(st.last_atr_1h) == 12.5            # columna antes muerta → real
    assert st.heartbeat_age_seconds == 7            # edad real persistida


@pytest.mark.asyncio
async def test_monitor_sin_heartbeat_age_persiste_none(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider sin heartbeat_age (yfinance) → heartbeat_age_seconds None, nunca
    revienta el ciclo."""
    _sm(db, "ES", "ESU2025", tick=0.25)
    await db.commit()

    class _Ctx:
        async def __aenter__(self_):
            return db

        async def __aexit__(self_, *a):
            return False

    monkeypatch.setattr("app.db.session.AsyncSessionLocal", lambda: _Ctx())

    class _NoHB(_MockBridgeSvc):
        heartbeat_age = None                        # atributo ausente ⇒ getattr None

    monitor = HeartbeatMonitor(_NoHB())
    await monitor._check()
    st = (await db.execute(
        select(MarketDataStatus).where(MarketDataStatus.symbol == "ES")
    )).scalar_one()
    assert st.heartbeat_age_seconds is None
    assert float(st.last_atr_1h) == 12.5


# ---------------------------------------------------------------------------
# Partial — FX en ticks, sin edad '—'
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_partial_fx_en_ticks_no_cero(
    client: AsyncClient, db: AsyncSession
) -> None:
    _sm(db, "6J", "6JU2025", tick=5e-7)
    await _status(db, "6J", atr_5m=3.6e-5, atr_1h=None, hb=5)
    await db.commit()

    r = await client.get("/ui/partials/bridge-status")
    assert r.status_code == 200
    html = r.text
    assert "72 ticks" in html                       # 0.000036 / 0.0000005
    assert "0.00" not in html                        # jamás el cero engañoso
    assert "hace 5s" in html                         # heartbeat real


@pytest.mark.asyncio
async def test_partial_sin_heartbeat_muestra_guion(
    client: AsyncClient, db: AsyncSession
) -> None:
    _sm(db, "ES", "ESU2025", tick=0.25)
    await _status(db, "ES", atr_5m=8.0, atr_1h=9.5, hb=None)
    await db.commit()

    r = await client.get("/ui/partials/bridge-status")
    assert r.status_code == 200
    html = r.text
    assert "hace" not in html                        # sin edad → nada inventado
    assert "—" in html                               # guion en la celda heartbeat
    assert "8.00" in html and "9.50" in html         # índice: puntos


# ---------------------------------------------------------------------------
# Agrupación — una fila por feed de datos, badge de tradeables, sin duplicados
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agrupacion_sin_duplicados(db: AsyncSession) -> None:
    # ES (padre, datos propios) + MES (micro, datos de ES): mismo feed
    _sm(db, "ES", "ESU2025", tick=0.25)
    _sm(db, "MES", "MESU2025", mds="ES", tick=0.25)
    await _status(db, "ES", atr_5m=8.0, atr_1h=9.5, hb=5)
    await _status(db, "MES", atr_5m=8.0, atr_1h=9.5, hb=5)
    await db.commit()

    rows = await _bridge_rows(db)
    assert len(rows) == 1                            # 2 tradeables → 1 feed
    row = rows[0]
    assert row["symbol"] == "ES"
    assert row["tradeables"] == ["MES"]              # badge "ES → MES"
    assert float(row["atr_1h"]) == 9.5
    assert row["heartbeat_age"] == 5


@pytest.mark.asyncio
async def test_partial_badge_tradeable_una_vez(
    client: AsyncClient, db: AsyncSession
) -> None:
    _sm(db, "ES", "ESU2025", tick=0.25)
    _sm(db, "MES", "MESU2025", mds="ES", tick=0.25)
    await _status(db, "ES", atr_5m=8.0, atr_1h=9.5, hb=5)
    await _status(db, "MES", atr_5m=8.0, atr_1h=9.5, hb=5)
    await db.commit()

    r = await client.get("/ui/partials/bridge-status")
    html = r.text
    assert html.count("→ MES") == 1                  # el badge, una sola vez
    # una única fila de datos (no la doble micro/padre)
    assert html.count('class="border-b border-gray-800 last:border-0"') == 1
