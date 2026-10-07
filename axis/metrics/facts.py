"""把数据库里的数据整理成给 AI 看的"事实包"：数字都已算好，AI 只做判断。"""

from datetime import date

from sqlalchemy.orm import Session

from axis.config import Thresholds
from axis.db.schema import ShopRow
from axis.metrics import queries as q
from axis.metrics.compute import compare, safe_div
from axis.metrics.rules import Flag, evaluate
from axis.models import AdLevel


def daily_facts(s: Session, report_date: date, th: Thresholds, max_flags: int = 60) -> tuple[dict, list[Flag]]:
    flags = evaluate(s, report_date, th)
    d1 = (report_date, report_date)
    cur7, prev7 = q.window(report_date, 7), q.window(report_date, 7, offset_days=7)
    shops = []
    for shop in s.query(ShopRow).order_by(ShopRow.shop_id):
        c7 = q.period_summary(s, *cur7, shop_id=shop.shop_id)
        p7 = q.period_summary(s, *prev7, shop_id=shop.shop_id)
        prods = []
        for p in q.products(s, shop.shop_id):
            pc7 = q.period_summary(s, *cur7, shop_id=shop.shop_id, asin=p.asin)
            pp7 = q.period_summary(s, *prev7, shop_id=shop.shop_id, asin=p.asin)
            prods.append({
                "asin": p.asin, "sku": p.sku, "title": p.title, "price": p.price,
                "fba_stock": p.fba_stock, "inbound_stock": p.inbound_stock,
                "rating": p.rating, "review_count": p.review_count,
                "last_7d": pc7.to_dict(), "change_vs_prev_7d": compare(pc7, pp7),
            })
        shops.append({
            "shop_id": shop.shop_id, "name": shop.name, "marketplace": shop.marketplace, "currency": shop.currency,
            "yesterday": q.period_summary(s, *d1, shop_id=shop.shop_id).to_dict(),
            "last_7d": c7.to_dict(),
            "change_vs_prev_7d": compare(c7, p7),
            "products": prods,
        })
    facts = {
        "report_date": report_date.isoformat(),
        "thresholds": th.model_dump(),
        "shops": shops,
        "flags_total": len(flags),
        "flags": [f.to_dict() for f in flags[:max_flags]],
    }
    return facts, flags


def product_facts(s: Session, shop_id: str, asin: str, report_date: date, th: Thresholds) -> dict:
    product = next((p for p in q.products(s, shop_id) if p.asin == asin), None)
    if product is None:
        raise ValueError(f"数据库里没有产品 {shop_id}/{asin}，请先同步数据")
    shop = s.get(ShopRow, shop_id)
    w = {n: q.window(report_date, n) for n in (7, 30)}
    s14, _ = q.window(report_date, 14)
    c7 = q.period_summary(s, *w[7], shop_id=shop_id, asin=asin)
    p7 = q.period_summary(s, *q.window(report_date, 7, offset_days=7), shop_id=shop_id, asin=asin)
    c30 = q.period_summary(s, *w[30], shop_id=shop_id, asin=asin)
    camp_ids = {r["campaign_id"] for r in q.ad_entity_stats(s, shop_id, *w[30], AdLevel.PRODUCT_AD, asin=asin, limit=None)}
    camps = []
    for c in q.campaigns(s, shop_id):
        if c.campaign_id in camp_ids:
            tot = q.ad_totals(s, s14, report_date, AdLevel.CAMPAIGN, shop_id=shop_id, campaign_id=c.campaign_id)
            camps.append({"campaign_id": c.campaign_id, "name": c.name, "state": c.state,
                          "targeting_type": c.targeting_type, "daily_budget": c.daily_budget,
                          "last_14d": tot.to_dict()})
    units30 = c30.sales.units
    return {
        "report_date": report_date.isoformat(),
        "shop": {"shop_id": shop_id, "name": shop.name if shop else "", "marketplace": shop.marketplace if shop else ""},
        "product": {
            "asin": asin, "sku": product.sku, "title": product.title, "price": product.price,
            "status": product.status, "rating": product.rating, "review_count": product.review_count,
            "fba_stock": product.fba_stock, "inbound_stock": product.inbound_stock,
            "days_of_cover_30d": _round(safe_div(product.fba_stock + product.inbound_stock, units30 / 30)),
            "listing": product.listing,
        },
        "thresholds": th.model_dump(),
        "last_7d": c7.to_dict(),
        "change_vs_prev_7d": compare(c7, p7),
        "last_30d": c30.to_dict(),
        "daily_last_30d": q.daily_series(s, shop_id, asin, *w[30]),
        "campaigns": camps,
        "top_targets_14d": q.ad_entity_stats(s, shop_id, s14, report_date, AdLevel.KEYWORD, asin=asin, limit=30),
        "top_search_terms_14d": q.ad_entity_stats(s, shop_id, s14, report_date, AdLevel.SEARCH_TERM, asin=asin, limit=40),
        "flags": [f.to_dict() for f in evaluate(s, report_date, th, [shop_id]) if f.asin == asin],
    }


def _round(v):
    return round(v, 1) if v is not None else None
