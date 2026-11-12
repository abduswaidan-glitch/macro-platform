"""حارس الشبكة لاختبارات tests/: يرفض كل محاولة اتصال ويسجّلها، ثم يعيد كل ما غيّره كما كان.

يغطي عملية pytest نفسها فقط (socket وurllib في Python). المتصفح في tests_ui عملية منفصلة يراقبها tests_ui/conftest.py.
"""
from __future__ import annotations

import socket
import urllib.request
from contextlib import contextmanager

import pytest

PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy")


@contextmanager
def network_guard():
    """داخل الكتلة: لا proxy، وopener جديد في urllib، وgetaddrinfo/connect/connect_ex ترفض وتسجّل.
    عند الخروج (حتى مع استثناء) يعيد MonkeyPatch كل قيمة وكل متغير بيئة كما كان."""
    attempts: list[str] = []

    def refuse(what):
        def _f(*args, **kwargs):
            target = args[1] if what == "connect" and len(args) > 1 else (args[0] if args else "?")
            attempts.append(f"{what} {target}")
            raise OSError(f"real network is disabled in tests ({what} {target})")
        return _f

    with pytest.MonkeyPatch.context() as mp:
        for var in PROXY_VARS:
            mp.delenv(var, raising=False)
        mp.setattr(urllib.request, "_opener", None)
        mp.setattr(socket, "getaddrinfo", refuse("getaddrinfo"))
        mp.setattr(socket.socket, "connect", refuse("connect"))
        mp.setattr(socket.socket, "connect_ex", refuse("connect"))
        yield attempts
