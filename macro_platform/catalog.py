"""كتالوج المؤشرات: معرّفات سلاسل FRED مع أسمائها وطريقة تحويلها."""

from __future__ import annotations

from dataclasses import dataclass

PERIODS_PER_YEAR = {"D": 252, "W": 52, "M": 12, "Q": 4}

# category -> (الاسم العربي, English name)
CATEGORIES: dict[str, tuple[str, str]] = {
    "growth": ("النمو", "Growth"),
    "labor": ("سوق العمل", "Labor"),
    "inflation": ("التضخم", "Inflation"),
    "rates": ("الفائدة والعوائد", "Rates"),
    "money": ("النقد والائتمان", "Money & Credit"),
    "markets": ("الأسواق والمخاطر", "Markets & Risk"),
    "housing": ("الإسكان", "Housing"),
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


INDICATORS: tuple[Indicator, ...] = (
    # النمو
    Indicator("GDPC1", "الناتج المحلي الحقيقي", "Real GDP", "growth", "Q", "yoy", "%", True),
    Indicator("INDPRO", "الإنتاج الصناعي", "Industrial Production", "growth", "M", "yoy", "%", True),
    Indicator("RSAFS", "مبيعات التجزئة", "Retail Sales", "growth", "M", "yoy", "%", True),
    Indicator("HOUST", "بدء بناء المساكن", "Housing Starts", "housing", "M", "level", "ألف وحدة", True),
    # سوق العمل
    Indicator("PAYEMS", "الوظائف غير الزراعية (متوسط 3 أشهر)", "Nonfarm Payrolls (3m avg)",
              "labor", "M", "diff_avg3", "ألف", True),
    Indicator("UNRATE", "معدل البطالة", "Unemployment Rate", "labor", "M", "level", "%", False),
    Indicator("ICSA", "طلبات إعانة البطالة الأولية", "Initial Jobless Claims", "labor", "W", "level", "", False),
    Indicator("JTSJOL", "الوظائف الشاغرة (JOLTS)", "Job Openings", "labor", "M", "level", "ألف", True),
    # التضخم
    Indicator("CPIAUCSL", "CPI الرئيسي (تغير سنوي)", "Headline CPI YoY", "inflation", "M", "yoy", "%", False),
    Indicator("CPILFESL", "CPI الأساسي (تغير سنوي)", "Core CPI YoY", "inflation", "M", "yoy", "%", False),
    Indicator("PCEPILFE", "PCE الأساسي (تغير سنوي)", "Core PCE YoY", "inflation", "M", "yoy", "%", False),
    # الفائدة والعوائد
    Indicator("FEDFUNDS", "سعر الفائدة الفيدرالي", "Fed Funds Rate", "rates", "M", "level", "%"),
    Indicator("DGS2", "عائد سندات 2 سنة", "2Y Treasury", "rates", "D", "level", "%"),
    Indicator("DGS10", "عائد سندات 10 سنوات", "10Y Treasury", "rates", "D", "level", "%"),
    Indicator("T10Y2Y", "فارق العائد 10Y-2Y", "10Y-2Y Spread", "rates", "D", "level", "%"),
    Indicator("DFII10", "العائد الحقيقي 10 سنوات", "10Y Real Yield (TIPS)", "rates", "D", "level", "%"),
    Indicator("T10YIE", "التوقعات التضخمية 10 سنوات", "10Y Breakeven Inflation", "rates", "D", "level", "%"),
    Indicator("MORTGAGE30US", "الرهن العقاري 30 سنة", "30Y Mortgage Rate", "housing", "W", "level", "%", False),
    # النقد والائتمان
    Indicator("M2SL", "M2 (تغير سنوي)", "M2 YoY", "money", "M", "yoy", "%", True),
    Indicator("WALCL", "ميزانية الفيدرالي", "Fed Balance Sheet", "money", "W", "level", "مليون $"),
    Indicator("BAMLH0A0HYM2", "فارق High Yield (OAS)", "HY Credit Spread", "markets", "D", "level", "%", False),
    # الأسواق والمخاطر
    Indicator("VIXCLS", "مؤشر VIX", "VIX", "markets", "D", "level", "", False),
    Indicator("DTWEXBGS", "مؤشر الدولار الواسع", "Broad USD Index", "markets", "D", "level", "", None),
    Indicator("DCOILWTICO", "نفط WTI", "WTI Crude", "markets", "D", "level", "$", None),
    Indicator("UMCSENT", "ثقة المستهلك (Michigan)", "UMich Sentiment", "markets", "M", "level", "", True),
)

BY_ID: dict[str, Indicator] = {i.id: i for i in INDICATORS}


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
