"""从数据库聚合指标的查询函数。规则引擎、事实汇总和 agent 工具都复用这里。"""

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from axis.db.schema import AdCampaignRow, AdMetricDailyRow, ProductRow, SalesDailyRow
from axis.metrics.compute import AdTotals, PeriodSummary, SalesTotals
from axis.models import AdLevel


def window(end: date, days: int, offset_days: int = 0) -> tuple[date, date]:
    """以 end 为最后一天、长度为 days 的时间窗口；offset_days 用于取更早的对比窗口。"""
    e = end - timedelta(days=offset_days)
    return e - timedelta(days=days - 1), e


def sales_totals(s: Session, start: date, end: date, shop_id: str | None = None, asin: str | None = None) -> SalesTotals:
    t = SalesDailyRow
    q = select(
        func.coalesce(func.sum(t.units), 0),
        func.coalesce(func.sum(t.orders), 0),
        func.coalesce(func.sum(t.revenue), 0.0),
        func.coalesce(func.sum(t.sessions), 0),
        func.coalesce(func.sum(t.refunds), 0),
    ).where(t.date.between(start, end))
    if shop_id:
        q = q.where(t.shop_id == shop_id)
    if asin:
        q = q.where(t.asin == asin)
    units, orders, revenue, sessions, refunds = s.execute(q).one()
    return SalesTotals(units=units, orders=orders, revenue=float(revenue), sessions=sessions, refunds=refunds)


def ad_totals(
    s: Session,
    start: date,
    end: date,
    level: AdLevel = AdLevel.CAMPAIGN,
    shop_id: str | None = None,
    asin: str | None = None,
    campaign_id: str | None = None,
) -> AdTotals:
    t = AdMetricDailyRow
    q = select(*_metric_sums(t)).where(t.date.between(start, end), t.level == level.value)
    if shop_id:
        q = q.where(t.shop_id == shop_id)
    if asin:
        q = q.where(t.asin == asin)
    if campaign_id:
        q = q.where(t.campaign_id == campaign_id)
    imp, clk, spend, orders, sales = s.execute(q).one()
    return AdTotals(impressions=imp, clicks=clk, spend=float(spend), orders=orders, sales=float(sales))


def period_summary(s: Session, start: date, end: date, shop_id: str | None = None, asin: str | None = None) -> PeriodSummary:
    # 产品维度的广告数据取"广告产品"粒度，店铺维度取"广告活动"粒度
    level = AdLevel.PRODUCT_AD if asin else AdLevel.CAMPAIGN
    return PeriodSummary(
        sales=sales_totals(s, start, end, shop_id, asin),
        ads=ad_totals(s, start, end, level, shop_id, asin),
    )


def ad_entity_stats(
    s: Session,
    shop_id: str,
    start: date,
    end: date,
    level: AdLevel,
    asin: str | None = None,
    campaign_id: str | None = None,
    order_by: str = "spend",
    limit: int | None = 50,
) -> list[dict]:
    """按实体（广告活动 / 投放词 / 搜索词）汇总一段时间的广告表现。"""
    t = AdMetricDailyRow
    q = (
        select(t.campaign_id, t.entity_key, t.entity_text, t.match_type, func.max(t.asin), *_metric_sums(t))
        .where(t.shop_id == shop_id, t.level == level.value, t.date.between(start, end))
        .group_by(t.campaign_id, t.entity_key, t.entity_text, t.match_type)
    )
    if asin:
        q = q.where(t.asin == asin)
    if campaign_id:
        q = q.where(t.campaign_id == campaign_id)
    names = campaign_names(s, shop_id)
    out = []
    for cid, key, text, match, row_asin, imp, clk, spend, orders, sales in s.execute(q):
        tot = AdTotals(impressions=imp, clicks=clk, spend=float(spend), orders=orders, sales=float(sales))
        out.append(
            {
                "campaign_id": cid,
                "campaign_name": names.get(cid, ""),
                "entity_key": key,
                "text": text,
                "match_type": match,
                "asin": row_asin,
                **tot.to_dict(),
            }
        )
    sort_key = {
        "spend": lambda r: -r["spend"],
        "sales": lambda r: -r["sales"],
        "clicks": lambda r: -r["clicks"],
        "acos": lambda r: -(r["acos"] if r["acos"] is not None else 99),
    }[order_by]
    out.sort(key=sort_key)
    return out[:limit] if limit else out


def campaign_names(s: Session, shop_id: str) -> dict[str, str]:
    q = select(AdCampaignRow.campaign_id, AdCampaignRow.name).where(AdCampaignRow.shop_id == shop_id)
    return dict(s.execute(q).all())


def campaigns(s: Session, shop_id: str) -> list[AdCampaignRow]:
    return list(s.scalars(select(AdCampaignRow).where(AdCampaignRow.shop_id == shop_id)))


def products(s: Session, shop_id: str | None = None) -> list[ProductRow]:
    q = select(ProductRow)
    if shop_id:
        q = q.where(ProductRow.shop_id == shop_id)
    return list(s.scalars(q.order_by(ProductRow.shop_id, ProductRow.asin)))


def daily_series(s: Session, shop_id: str, asin: str, start: date, end: date) -> list[dict]:
    """单个产品的逐日销售 + 广告数据。"""
    sales = {
        r.date: r
        for r in s.scalars(
            select(SalesDailyRow).where(
                SalesDailyRow.shop_id == shop_id, SalesDailyRow.asin == asin, SalesDailyRow.date.between(start, end)
            )
        )
    }
    t = AdMetricDailyRow
    ads = {
        d: (spend, ad_sales, clicks, orders)
        for d, spend, ad_sales, clicks, orders in s.execute(
            select(t.date, func.sum(t.spend), func.sum(t.sales), func.sum(t.clicks), func.sum(t.orders))
            .where(t.shop_id == shop_id, t.asin == asin, t.level == AdLevel.PRODUCT_AD.value, t.date.between(start, end))
            .group_by(t.date)
        )
    }
    out = []
    d = start
    while d <= end:
        r = sales.get(d)
        spend, ad_sales, clicks, orders = ads.get(d, (0.0, 0.0, 0, 0))
        out.append(
            {
                "date": d.isoformat(),
                "units": r.units if r else 0,
                "revenue": round(r.revenue, 2) if r else 0.0,
                "sessions": r.sessions if r else 0,
                "ad_spend": round(spend, 2),
                "ad_sales": round(ad_sales, 2),
                "ad_clicks": clicks,
                "ad_orders": orders,
            }
        )
        d += timedelta(days=1)
    return out


def latest_data_date(s: Session) -> date | None:
    return s.scalar(select(func.max(SalesDailyRow.date)))


def _metric_sums(t):
    return (
        func.coalesce(func.sum(t.impressions), 0),
        func.coalesce(func.sum(t.clicks), 0),
        func.coalesce(func.sum(t.spend), 0.0),
        func.coalesce(func.sum(t.orders), 0),
        func.coalesce(func.sum(t.sales), 0.0),
    )
