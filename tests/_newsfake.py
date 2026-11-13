"""مصادر مزيّفة لاختبارات الأخبار والتقويم: خلاصات RSS وتصدير ForexFactory بالشكل الذي فحصناه من خوادم GitHub
(تشغيل الفحص 43482795839، 13 نوفمبر 2026)، وشبكة مزيّفة تُستبدل بـnews.http_get. لا اتصال حقيقي."""
import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

from macro_platform import news

URL = {s["url"]: s for s in news.DEFAULT_SOURCES}
FED_PRESS, FED_SPEECH, BEA, CNBC_TOP, CNBC_ECON, CNBC_MKT, MW = (s["url"] for s in news.DEFAULT_SOURCES)
FF_THIS, FF_NEXT = (f["url"] for f in news.DEFAULT_CALENDAR)


def now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rss(*items, rfc_tz=None):
    """items: (title, link, datetime). pubDate بصيغة RFC 822 كما تنشرها المصادر."""
    body = "".join(
        f"<item><title>{escape(t)}</title><link>{escape(l)}</link><guid isPermaLink=\"true\">{escape(l)}</guid>"
        f"<pubDate>{format_datetime(p.astimezone(rfc_tz) if rfc_tz else p.astimezone(timezone.utc), usegmt=rfc_tz is None)}"
        f"</pubDate></item>" for t, l, p in items)
    return f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>feed</title>{body}</channel></rss>'.encode()


def ff_row(title, t, impact="High", country="USD", forecast="0.3%", previous="0.2%"):
    """صف من ff_calendar_thisweek.json: المفاتيح كما فُحصت (country, date, forecast, impact, previous, title)، بلا actual."""
    ny = t.astimezone(news.NY)
    return {"title": title, "country": country, "date": ny.isoformat(), "impact": impact,
            "forecast": forecast, "previous": previous}


def ff(*rows):
    return json.dumps(list(rows)).encode()


def default_routes(t=None):
    """يوم عادي: كل مصدر يعمل، بعناوين مهمة وأخرى تُسقط، وتقويم يضم ماضياً وقادماً ودولاً أخرى."""
    t = t or now()
    return {
        FED_PRESS: rss(("Federal Reserve issues FOMC statement", "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261029a.htm", t - timedelta(hours=5)),
                       ("Federal Reserve Board announces termination of enforcement action with Example Bank",
                        "https://www.federalreserve.gov/newsevents/pressreleases/enforcement20261112a.htm", t - timedelta(hours=4))),
        FED_SPEECH: rss(("Waller, Outlook for the Economy and Policy", "https://www.federalreserve.gov/newsevents/speech/waller20261112a.htm", t - timedelta(hours=26))),
        BEA: rss(("Personal Income and Outlays, September 2026", "https://www.bea.gov/news/2026/personal-income-and-outlays-september-2026", t - timedelta(hours=30)),
                 ("Personal Income by State, 2nd Quarter 2026", "https://www.bea.gov/news/2026/personal-income-state-2nd-quarter-2026", t - timedelta(hours=30))),
        CNBC_TOP: rss(("Stocks fall as Treasury yields jump after hot inflation data", "https://www.cnbc.com/2026/11/13/stock-market-today.html", t - timedelta(minutes=40)),
                      ("Cramer's lightning round: I like this stock", "https://www.cnbc.com/2026/11/13/cramer-lightning-round.html", t - timedelta(minutes=50)),
                      ("5 tips to pay off credit cards faster", "https://www.cnbc.com/select/pay-off-credit-cards.html", t - timedelta(hours=2))),
        CNBC_ECON: rss(("Stocks fall as Treasury yields jump after hot inflation data", "https://www.cnbc.com/2026/11/13/stock-market-today.html", t - timedelta(minutes=40))),
        CNBC_MKT: rss(("Nvidia shares rise ahead of earnings", "https://www.cnbc.com/2026/11/13/nvidia-shares.html", t - timedelta(hours=3))),
        MW: rss(("European shares edge higher", "https://www.marketwatch.com/story/europe-1", t - timedelta(hours=1)),
                ("A celebrity chef opens a new restaurant", "https://www.marketwatch.com/story/chef-2", t - timedelta(hours=1))),
        FF_THIS: ff(ff_row("Core CPI m/m", t - timedelta(hours=20)),
                    ff_row("Unemployment Claims", t + timedelta(hours=20), impact="Medium", forecast="225K", previous="221K"),
                    ff_row("Crude Oil Inventories", t + timedelta(hours=26), impact="Low"),
                    ff_row("German ZEW Economic Sentiment", t - timedelta(hours=40), country="EUR"),
                    ff_row("FOMC Member Speaks", t + timedelta(hours=44), impact="Medium", forecast="", previous="")),
        FF_NEXT: news.NewsError("HTTP 404"),
    }


class Net:
    """بديل news.http_get: يعيد المحتوى المسجّل لكل رابط أو يرفع الاستثناء المسجّل، ويسجّل كل طلب."""

    def __init__(self, routes):
        self.routes, self.calls = dict(routes), []

    def __call__(self, url, timeout=25):
        self.calls.append(url)
        if url not in self.routes:
            raise AssertionError(f"unexpected request: {url}")
        r = self.routes[url]
        if isinstance(r, Exception):
            raise r
        return r

    def count(self, url):
        return sum(1 for c in self.calls if c == url)
