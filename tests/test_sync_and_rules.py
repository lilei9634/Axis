from collections import Counter

import pytest

from sqlalchemy import func, select

from axis import watchlist
from axis.db.schema import AdCampaignRow, AdMetricDailyRow, ProductRow, SalesDailyRow, ShopRow, SyncRunRow
from axis.erp.fake import FakeAdapter
from axis.etl.sync import sync
from axis.metrics import queries as q
from axis.metrics.facts import daily_facts, product_facts
from axis.metrics.rules import evaluate
from axis.models import AdLevel
from axis.watchlist import EmptyWatchlist, load_scope
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
        flags = evaluate(s, ANCHOR, settings.thresholds, load_scope(s))
    codes = Counter(f.code for f in flags)
    assert codes["sales_drop"] == 4  # S1-S3 和 S4 的 C 产品
    assert codes["low_stock"] == 3  # S1-S3 的 B 产品（S4 的 B 没关注）
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
        scope = load_scope(s)
        facts, flags = daily_facts(s, ANCHOR, settings.thresholds, scope)
        pf = product_facts(s, "S1", "B0HCTEST01", ANCHOR, settings.thresholds, scope)
    assert [sh["shop_id"] for sh in facts["shops"]] == ["S1", "S2", "S3", "S4"]
    assert [p["asin"] for p in facts["shops"][3]["products"]] == ["B0KCTEST01"]
    assert facts["flags_total"] == len(flags)
    assert len(pf["daily_last_30d"]) == 30
    assert any(f["code"] == "sales_drop" for f in pf["flags"])
    assert pf["top_search_terms_14d"] and pf["campaigns"]
    json.dumps(facts, ensure_ascii=False)
    json.dumps(pf, ensure_ascii=False, default=str)


def test_sync_only_pulls_tracked_products(sf):
    with sf() as s:
        shops = s.scalars(select(ShopRow.shop_id).order_by(ShopRow.shop_id)).all()
        products = s.execute(select(ProductRow.shop_id, ProductRow.asin)).all()
        sales_asins = set(s.scalars(select(SalesDailyRow.asin).distinct()))
        camp_ids = set(s.scalars(select(AdCampaignRow.campaign_id)))
        ad_asins = set(s.scalars(select(AdMetricDailyRow.asin).distinct()))
    assert shops == ["S1", "S2", "S3", "S4"]  # S5 没有关注的产品，整家店跳过
    assert len(products) == 10 and ("S4", "B0KCTEST01") in products and ("S4", "B0KATEST01") not in products
    assert sales_asins == {"B0HATEST01", "B0HBTEST01", "B0HCTEST01", "B0KCTEST01"}
    assert {c for c in camp_ids if c.startswith("S4")} == {"S4-C-AUTO", "S4-C-EXACT"}
    assert ad_asins == sales_asins


def test_missing_tracked_product_is_reported(sf):
    with sf() as s:
        watchlist.add(s, ["B0NOTEXIST"], shop_id="S2")
    run = sync(FakeAdapter(anchor=ANCHOR), sf, ANCHOR, 3)
    assert run.status == "ok" and run.stats["not_found"] == ["S2/B0NOTEXIST"]


def test_untracked_product_disappears_from_analysis(sf, settings):
    with sf() as s:
        watchlist.remove(s, ["B0KCTEST01"])
        scope = load_scope(s)
        facts, flags = daily_facts(s, ANCHOR, settings.thresholds, scope)
        assert "S4" not in scope.asins
        assert all(f.shop_id != "S4" for f in flags)
        with pytest.raises(ValueError, match="不在关注列表"):
            product_facts(s, "S4", "B0KCTEST01", ANCHOR, settings.thresholds, scope)


def test_empty_watchlist_blocks_sync(settings):
    from axis.db.session import make_session_factory

    factory = make_session_factory(settings.db_url)
    with pytest.raises(EmptyWatchlist):
        sync(FakeAdapter(anchor=ANCHOR), factory, ANCHOR, 3)
