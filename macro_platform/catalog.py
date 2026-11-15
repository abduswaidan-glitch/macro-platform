"""كتالوج المؤشرات: معرّفات السلاسل (FRED، وCOT من Tradingster) مع أسمائها وطريقة تحويلها."""

from __future__ import annotations

from dataclasses import dataclass

PERIODS_PER_YEAR = {"D": 252, "W": 52, "M": 12, "Q": 4}

# category -> (الاسم العربي, English name)، بترتيب تبويبات الصفحة. الإسكان موزّع: بدء البناء في النمو، والرهن في
# الفيدرالي والفائدة. markets لا تبويب لها: مؤشرات الأسهم داخلية فقط (internal_only) وتظهر مشتقة في Market Gates.
CATEGORIES: dict[str, tuple[str, str]] = {
    "growth": ("النمو", "Growth"),
    "inflation": ("التضخم", "Inflation"),
    "labor": ("سوق العمل", "Labor"),
    "fed_rates": ("الفيدرالي والفائدة", "Fed & Rates"),
    "financial": ("الأوضاع المالية", "Financial Conditions"),
    "markets": ("الأسواق (داخلي)", "Markets (internal)"),
}


@dataclass(frozen=True)
class Indicator:
    id: str  # معرّف سلسلة FRED
    name_ar: str
    name_en: str
    category: str
    freq: str  # D | W | M | Q
    transform: str = "level"  # level | yoy | diff | diff_avg3
    unit: str = ""
    good_up: bool | None = None  # True: الارتفاع إيجابي للاقتصاد، False: سلبي، None: سياقي فقط
    # ترخيص طرف ثالث (S&P DJI وNasdaq في FRED): يُجلب ويُستعمل في الحساب فقط. لا مستويات ولا نقاط في data.json،
    # ولا حفظ في data/raw، ولا في لقطة التقرير العامة؛ ما يُنشر نتائج مشتقة (فرق نسبي، حالة بوابة).
    internal_only: bool = False
    # مصدر الجلب: fred (fredgraph.csv) أو cot (صفحات Tradingster العامة لتقرير CFTC، macro_platform/cot.py)
    source: str = "fred"


INDICATORS: tuple[Indicator, ...] = (
    # النمو
    Indicator("GDPC1", "الناتج المحلي الحقيقي", "Real GDP", "growth", "Q", "yoy", "%", True),
    Indicator("INDPRO", "الإنتاج الصناعي", "Industrial Production", "growth", "M", "yoy", "%", True),
    Indicator("RSAFS", "مبيعات التجزئة", "Retail Sales", "growth", "M", "yoy", "%", True),
    Indicator("HOUST", "بدء بناء المساكن", "Housing Starts", "growth", "M", "level", "ألف وحدة", True),
    # سوق العمل
    Indicator("PAYEMS", "الوظائف غير الزراعية (متوسط 3 أشهر)", "Nonfarm Payrolls (3m avg)",
              "labor", "M", "diff_avg3", "ألف", True),
    Indicator("UNRATE", "معدل البطالة", "Unemployment Rate", "labor", "M", "level", "%", False),
    Indicator("ICSA", "طلبات إعانة البطالة الأولية", "Initial Jobless Claims", "labor", "W", "level", "", False),
    Indicator("CCSA", "طلبات إعانة البطالة المستمرة", "Continuing Jobless Claims", "labor", "W", "level", "", False),
    Indicator("JTSJOL", "الوظائف الشاغرة (JOLTS)", "Job Openings", "labor", "M", "level", "ألف", True),
    Indicator("CES0500000003", "متوسط الأجر بالساعة (تغير سنوي)", "Average Hourly Earnings YoY",
              "labor", "M", "yoy", "%", None),
    # التضخم
    Indicator("CPIAUCSL", "CPI الرئيسي (تغير سنوي)", "Headline CPI YoY", "inflation", "M", "yoy", "%", False),
    Indicator("CPILFESL", "CPI الأساسي (تغير سنوي)", "Core CPI YoY", "inflation", "M", "yoy", "%", False),
    Indicator("PCEPILFE", "PCE الأساسي (تغير سنوي)", "Core PCE YoY", "inflation", "M", "yoy", "%", False),
    # الفائدة والعوائد
    # يومي (H.15) بدل المتوسط الشهري FEDFUNDS: المتوسط الشهري يبقى متقادماً حتى شهراً بعد أي قرار للفيدرالي
    Indicator("DFF", "سعر الفائدة الفيدرالي الفعلي (يومي)", "Effective Fed Funds Rate (daily)", "fed_rates", "D", "level", "%"),
    Indicator("DGS2", "عائد سندات 2 سنة", "2Y Treasury", "fed_rates", "D", "level", "%"),
    Indicator("DGS10", "عائد سندات 10 سنوات", "10Y Treasury", "fed_rates", "D", "level", "%"),
    Indicator("T10Y2Y", "فارق العائد 10Y-2Y", "10Y-2Y Spread", "fed_rates", "D", "level", "%"),
    Indicator("DFII10", "العائد الحقيقي 10 سنوات", "10Y Real Yield (TIPS)", "fed_rates", "D", "level", "%"),
    Indicator("T10YIE", "التوقعات التضخمية 10 سنوات", "10Y Breakeven Inflation", "fed_rates", "D", "level", "%"),
    Indicator("MORTGAGE30US", "الرهن العقاري 30 سنة", "30Y Mortgage Rate", "fed_rates", "W", "level", "%", False),
    # النقد والائتمان
    Indicator("M2SL", "M2 (تغير سنوي)", "M2 YoY", "financial", "M", "yoy", "%", True),
    Indicator("WALCL", "ميزانية الفيدرالي", "Fed Balance Sheet", "financial", "W", "level", "مليون $"),
    Indicator("BAMLH0A0HYM2", "فارق High Yield (OAS)", "HY Credit Spread", "financial", "D", "level", "%", False),
    # الأسواق والمخاطر
    Indicator("VIXCLS", "مؤشر VIX", "VIX", "financial", "D", "level", "", False),
    Indicator("DTWEXBGS", "مؤشر الدولار الواسع", "Broad USD Index", "financial", "D", "level", "", None),
    Indicator("DCOILWTICO", "نفط WTI", "WTI Crude", "financial", "D", "level", "$", None),
    Indicator("NASDAQ100", "مؤشر Nasdaq 100", "Nasdaq 100 Index", "markets", "D", "level", "", None, internal_only=True),
    Indicator("SP500", "مؤشر S&P 500", "S&P 500 Index", "markets", "D", "level", "", None, internal_only=True),
    Indicator("UMCSENT", "ثقة المستهلك (Michigan)", "UMich Sentiment", "growth", "M", "level", "", True),
    # تموضع الدولار: CFTC Commitments of Traders لعقود USD INDEX - ICE FUTURES U.S. (098662)، صافي العقود (Long − Short)
    # في مراكز الثلاثاء. تموضع لا بيانات اقتصادية: يقرأ ازدحام رهان الدولار بجوار الدولار الواسع. في آخر الكتالوج
    # كي لا تتغير بذور البيانات التجريبية للمؤشرات السابقة.
    Indicator("COT_DX_NC", "صافي المضاربين الكبار في عقود مؤشر الدولار (COT)", "COT USD Index: Non-Commercial net",
              "financial", "W", "level", "عقد", None, source="cot"),
    Indicator("COT_DX_LEV", "صافي Leveraged Funds في عقود مؤشر الدولار (COT)", "COT USD Index: Leveraged Funds net",
              "financial", "W", "level", "عقد", None, source="cot"),
    Indicator("COT_DX_AM", "صافي Asset Managers في عقود مؤشر الدولار (COT)", "COT USD Index: Asset Managers net",
              "financial", "W", "level", "عقد", None, source="cot"),
)

BY_ID: dict[str, Indicator] = {i.id: i for i in INDICATORS}
# اسم المصدر كما يظهر في الصفحة
SOURCES = {"fred": "FRED", "cot": "CFTC عبر Tradingster"}
# زخم محسوب بالكود (MoM و3m annualized) من سلاسل موجودة: يفصل مستوى التضخم والأجور عن تسارعها
MOMENTUM_IDS = ("CPIAUCSL", "CPILFESL", "PCEPILFE", "CES0500000003")
INTERNAL_IDS = tuple(i.id for i in INDICATORS if i.internal_only)


def validate_catalog() -> None:
    ids = [i.id for i in INDICATORS]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate FRED series id in catalog")
    for ind in INDICATORS:
        if ind.category not in CATEGORIES:
            raise ValueError(f"{ind.id}: unknown category {ind.category}")
        if ind.freq not in PERIODS_PER_YEAR:
            raise ValueError(f"{ind.id}: unknown freq {ind.freq}")
        if ind.transform not in {"level", "yoy", "diff", "diff_avg3"}:
            raise ValueError(f"{ind.id}: unknown transform {ind.transform}")
        if ind.source not in SOURCES:
            raise ValueError(f"{ind.id}: unknown source {ind.source}")
