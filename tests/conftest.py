"""لا اتصال شبكة حقيقي في اختبارات المنصة. deploy.yml يشغّل هذه الاختبارات قبل بناء الموقع ونشره.

كل محاولة تُسجَّل وتُرفض، ثم يفشل الاختبار عند انتهائه حتى لو ابتلع الكود الاستثناء
(fetch_news يحوّل أخطاء الجلب إلى سطر في السجل، فلا يكفي رفع الخطأ وحده). التفاصيل في _netguard.py.
"""
import pytest

from _netguard import network_guard


@pytest.fixture(autouse=True)
def _no_real_network():
    with network_guard() as attempts:
        yield attempts
    assert not attempts, f"test attempted real network access: {attempts}"
