from collections import Counter

from sqlalchemy import func, select

from axis.db.schema import AdMetricDailyRow, SalesDailyRow, SyncRunRow
from axis.erp.fake import FakeAdapter
from axis.etl.sync import sync
from axis.metrics import queries as q
from axis.metrics.facts import daily_facts, product_facts
from axis.metrics.rules import evaluate
from axis.models import AdLevel
from tests.conftest import ANCHOR


def _counts(sf):
    with sf() as s:
        return (
            s.scalar(select(func.count()).select_from(SalesDailyRow)),
            s.scalar(select(func.count()).select_from(AdMetricDailyRow)),
        )


def test_sync_is_idempotent(sf):
    before = _counts(sf)
    run = sync(FakeAdapter(anchor=ANCHOR), sf, ANCHOR, 30)
    assert run.status == "ok"
    assert _counts(sf) == before
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(SyncRunRow)) == 2


def test_ad_levels_are_consistent(sf):
    """各粒度汇总后的花费应一致，保证报表口径统一。"""
    with sf() as s:
        start, end = q.window(ANCHOR, 14)
        spends = {lv: round(q.ad_totals(s, start, end, lv, shop_id="S1").spend, 2) for lv in AdLevel}
    assert len(set(spends.values())) == 1


def test_rules_find_planted_problems(sf, settings):
    with sf() as s:
        flags = evaluate(s, ANCHOR, settings.thresholds)
    codes = Counter(f.code for f in flags)
    assert codes["sales_drop"] == 5  # 每家店的 C 产品
    assert codes["low_stock"] >= 5  # 每家店的 B 产品
    assert codes["budget_capped"] >= 1
    wasted = {f.target for f in flags if f.code == "wasted_search_term"}
    assert "rubber scraper" in wasted
    harvest = {f.target for f in flags if f.code == "harvest_search_term"}
    assert "heat resistant spatula" in harvest
    # 严重问题排在最前面
    assert flags[0].severity == "critical"


def test_facts_are_json_ready(sf, settings):
    import json

    with sf() as s:
        facts, flags = daily_facts(s, ANCHOR, settings.thresholds)
        pf = product_facts(s, "S1", "B0S1CTEST0", ANCHOR, settings.thresholds)
    assert len(facts["shops"]) == 5
    assert facts["flags_total"] == len(flags)
    assert len(pf["daily_last_30d"]) == 30
    assert any(f["code"] == "sales_drop" for f in pf["flags"])
    json.dumps(facts, ensure_ascii=False)
    json.dumps(pf, ensure_ascii=False, default=str)
