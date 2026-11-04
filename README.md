# منصة الاقتصاد الكلي الأمريكي

تجلب سلاسل FRED الأمريكية، تحسب مؤشرات مشتقة، وتولّد لوحة HTML واحدة بالعربية (RTL) بلا CDN.

## الإعداد

```bash
pip install -r requirements.txt
export FRED_API_KEY=your_key   # مفتاح مجاني من fred.stlouisfed.org
```

## الاستخدام

```bash
python -m macro_platform list                 # كتالوج المؤشرات
python -m macro_platform fetch                # جلب من FRED وتخزين محلي في data/macro/
python -m macro_platform fetch --refresh      # إعادة الجلب بدل الكاش
python -m macro_platform report               # يكتب reports/macro/index.html من الكاش
python -m macro_platform report --demo        # بيانات تجريبية مولّدة (للتجربة فقط)
```

## ما الذي تحسبه المنصة

| الإشارة | التعريف |
|---|---|
| تحويلات المؤشرات | `level`، تغير سنوي `yoy`، فرق شهري، متوسط فرق 3 أشهر |
| Z-score و المئوي (5y) | موقع آخر قيمة داخل نافذة 5 سنوات |
| Sahm Rule | متوسط البطالة 3 أشهر ناقص أدنى متوسط خلال 12 شهراً؛ العتبة 0.5 pp |
| منحنى 10Y-2Y | قلب المنحنى (سالب) |
| العائد الحقيقي 10Y و Breakeven | شروط نقدية حقيقية وتوقعات تضخم |
| High Yield و VIX | ضغط ائتماني ومستوى الخوف |
| المربع: النمو × التضخم | النمو صاعد إذا كان الإنتاج الصناعي والتوظيف موجبين معاً؛ التضخم ساخن إذا تجاوز Core PCE 2.5% |

المربع تصنيف وصفي مبسّط وليس توقعاً.

## البنية

- `catalog.py`: قائمة المؤشرات ومعرّفات FRED وطريقة تحويل كل منها
- `fred.py`: عميل FRED مع إعادة المحاولة والتخزين المحلي (CSV)
- `analytics.py`: التحويلات والمؤشرات المشتقة والبطاقات
- `dashboard.py`: توليد HTML (sparklines بـSVG)
- `demo.py`: بيانات تجريبية لاختبار الخط كله دون مفتاح

## الاختبارات

```bash
python -m pytest -q tests/test_macro_platform.py
```
