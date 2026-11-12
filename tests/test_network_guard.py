"""الحارس نفسه: يعيد البيئة كما كانت بعد كل استخدام، ويرصد المحاولة حتى لو ابتلع الكود الاستثناء."""
import os
import socket
import urllib.request

import pytest

from _netguard import PROXY_VARS, network_guard


def _state():
    return (tuple((v, os.environ.get(v)) for v in PROXY_VARS), socket.getaddrinfo,
            socket.socket.__dict__.get("connect"), socket.socket.__dict__.get("connect_ex"), urllib.request._opener)


def test_restores_proxy_env_opener_and_socket(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setattr(urllib.request, "_opener", urllib.request.build_opener())
    before = _state()
    with network_guard():
        assert "HTTPS_PROXY" not in os.environ and urllib.request._opener is None
        assert socket.getaddrinfo is not before[1]
    assert _state() == before


def test_restores_even_when_the_test_body_raises(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    before = _state()
    with pytest.raises(ZeroDivisionError):
        with network_guard():
            1 / 0
    assert _state() == before


def test_sees_an_attempt_the_code_swallows():
    """مثل fetch_news: الطلب يفشل، والكود يلتقط الاستثناء ويكمل؛ الحارس يحتفظ بالمحاولة."""
    with network_guard() as attempts:
        try:
            urllib.request.urlopen("https://stream.financialjuice.com/FJService.asmx", timeout=1)
        except Exception:
            pass
        try:
            socket.create_connection(("192.0.2.1", 443), timeout=1)
        except Exception:
            pass
    assert any("stream.financialjuice.com" in a for a in attempts) and any("192.0.2.1" in a for a in attempts)


def test_a_local_proxy_is_not_a_way_around_it():
    """الطلب عبر proxy على 127.0.0.1 يمر بـconnect على loopback، ويُرفض ويُسجَّل أيضاً."""
    with network_guard() as attempts:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": "http://127.0.0.1:9"}))
        try:
            opener.open("https://example.com/", timeout=1)
        except Exception:
            pass
    assert attempts, "a request through a loopback proxy went unrecorded"
