"""FIX-D1 — bounded webhook quarantine guard (pure unit tests)."""
from app.core import quarantine_guard as qg


def test_allows_up_to_cap_then_blocks():
    qg.reset()
    ip = "1.2.3.4"
    allowed = sum(1 for _ in range(qg.QUARANTINE_MAX_PER_WINDOW + 10) if qg.allow(ip))
    assert allowed == qg.QUARANTINE_MAX_PER_WINDOW      # exactamente la cota
    assert qg.allow(ip) is False                        # sigue bloqueando


def test_cap_is_per_ip():
    qg.reset()
    for _ in range(qg.QUARANTINE_MAX_PER_WINDOW):
        assert qg.allow("10.0.0.1") is True
    assert qg.allow("10.0.0.1") is False                # IP saturada
    assert qg.allow("10.0.0.2") is True                 # otra IP: cota propia


def test_none_ip_bucketed_as_unknown():
    qg.reset()
    for _ in range(qg.QUARANTINE_MAX_PER_WINDOW):
        assert qg.allow(None) is True
    assert qg.allow(None) is False


def test_window_expiry_frees_slots(monkeypatch):
    qg.reset()
    t = [1000.0]
    monkeypatch.setattr(qg.time, "time", lambda: t[0])
    ip = "9.9.9.9"
    for _ in range(qg.QUARANTINE_MAX_PER_WINDOW):
        assert qg.allow(ip) is True
    assert qg.allow(ip) is False
    t[0] += qg.QUARANTINE_WINDOW_S + 1                   # toda la ventana caducó
    assert qg.allow(ip) is True                          # slots liberados


def test_reset_clears_state():
    qg.reset()
    for _ in range(qg.QUARANTINE_MAX_PER_WINDOW):
        qg.allow("7.7.7.7")
    assert qg.allow("7.7.7.7") is False
    qg.reset()
    assert qg.allow("7.7.7.7") is True


def test_list_never_grows_past_cap():
    """La guarda no puede floodearse a sí misma: el bucket por IP queda topado."""
    qg.reset()
    ip = "5.5.5.5"
    for _ in range(qg.QUARANTINE_MAX_PER_WINDOW * 3):
        qg.allow(ip)
    assert len(qg._hits[f"ip:{ip}"]) == qg.QUARANTINE_MAX_PER_WINDOW
