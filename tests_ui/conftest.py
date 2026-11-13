"""أدوات اختبارات الواجهة: index.html الحقيقي مع data.json مبني في الاختبار، يُخدَّم على 127.0.0.1 ويُفتح في Chromium.

مراقبة شبكة المتصفح (عملية منفصلة لا يغطيها حارس tests/): كل طلب يخرج عن الخادم المحلي يُسجَّل ويُلغى (abort)،
فلا يصل المتصفح إلى أي مضيف خارجي. عند نهاية كل اختبار لا يُقبل إلا مضيفا Google Fonts المعلنان في <link> وCSP
داخل index.html؛ أي مضيف آخر يُفشل الاختبار. الخطوط الملغاة تعني أن الصفحة تُعرض بخط احتياطي في الاختبار.
"""
import shutil
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright

from macro_platform.build import write_json

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_EXTERNAL = {"fonts.googleapis.com", "fonts.gstatic.com"}


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class UI:
    def __init__(self, tmp_path: Path):
        self.tmp_path, self.external, self._open = tmp_path, [], []
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch()

    def _route(self, route, request):
        host = urlsplit(request.url).hostname
        if request.url.startswith(("data:", "blob:")) or host == "127.0.0.1":
            route.continue_()
        else:
            self.external.append(request.url)
            route.abort()

    def open(self, payload: dict, files: dict | None = None):
        site = self.tmp_path / f"site{len(self._open)}"
        site.mkdir()
        shutil.copy(ROOT / "index.html", site / "index.html")
        write_json(site / "data.json", payload)
        for name, obj in (files or {}).items():   # news.json وdata/ai_history.json عند الحاجة
            (site / name).parent.mkdir(parents=True, exist_ok=True)
            write_json(site / name, obj)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(site)))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        ctx = self.browser.new_context()
        ctx.route("**/*", self._route)
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{srv.server_address[1]}/index.html", wait_until="networkidle")
        page.wait_for_selector("#kpis .kpi")
        self._open.append((srv, ctx))
        return page

    def close(self):
        for srv, ctx in self._open:
            ctx.close()
            srv.shutdown()
        self.browser.close()
        self._pw.stop()


@pytest.fixture()
def ui(tmp_path):
    u = UI(tmp_path)
    try:
        yield u
    finally:
        u.close()
    hosts = {urlsplit(x).hostname for x in u.external}
    assert hosts <= EXPECTED_EXTERNAL, f"browser tried to reach unexpected hosts: {sorted(hosts - EXPECTED_EXTERNAL)}"
