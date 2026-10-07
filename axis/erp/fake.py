"""假数据适配器：生成 5 家店铺的产品、销量和广告数据，用于开发、测试和演示。

数据是确定性的（固定随机种子），并且故意埋了几类问题，方便验证规则和复盘：
- 高 ACOS 的关键词、点击很多却不出单的搜索词、值得收割的高转化搜索词
- 预算受限的广告活动、销量骤降的产品、库存不足的产品
"""

import random
from datetime import date, timedelta

from axis.models import AdCampaign, AdLevel, AdMetricRow, Product, SalesDaily, Shop

_SHOPS = [
    ("S1", "Homely US", "US", "USD"),
    ("S2", "Homely UK", "UK", "GBP"),
    ("S3", "Homely DE", "DE", "EUR"),
    ("S4", "KitchenPro US", "US", "USD"),
    ("S5", "KitchenPro CA", "CA", "CAD"),
]

_PRODUCTS = [
    # (后缀, 标题, 价格, 日均销量, 库存)
    ("A", "Silicone Spatula Set 5pcs", 19.99, 18, 900),
    ("B", "Bamboo Cutting Board Large", 29.99, 10, 120),  # 库存不足
    ("C", "Stainless Steel Measuring Cups", 15.99, 14, 700),  # 销量骤降
]

_SEARCH_TERMS = [
    "spatula set",
    "silicone spatula",
    "kitchen utensils",
    "cooking tools set",
    "heat resistant spatula",
    "rubber scraper",
]


def _asin(shop_id: str, suffix: str) -> str:
    return f"B0{shop_id}{suffix}TEST01"[:10]


class FakeAdapter:
    name = "fake"

    def __init__(self, anchor: date | None = None, days_of_history: int = 60, seed: int = 7):
        # anchor 是"最新有数据的日期"，默认昨天
        self.anchor = anchor or (date.today() - timedelta(days=1))
        self.start = self.anchor - timedelta(days=days_of_history - 1)
        self.seed = seed

    def _rng(self, *key) -> random.Random:
        return random.Random(f"{self.seed}|" + "|".join(map(str, key)))

    def _days(self, start: date, end: date):
        d = max(start, self.start)
        while d <= min(end, self.anchor):
            yield d
            d += timedelta(days=1)

    def list_shops(self) -> list[Shop]:
        return [Shop(shop_id=s, name=n, marketplace=m, currency=c) for s, n, m, c in _SHOPS]

    def list_products(self, shop_id: str) -> list[Product]:
        out = []
        for suffix, title, price, _, stock in _PRODUCTS:
            out.append(
                Product(
                    shop_id=shop_id,
                    asin=_asin(shop_id, suffix),
                    sku=f"{shop_id}-{suffix}",
                    title=title,
                    price=price,
                    fba_stock=stock,
                    inbound_stock=0,
                    status="Active",
                    rating=4.4 if suffix != "C" else 3.9,
                    review_count=1200 if suffix == "A" else 260,
                    listing={
                        "title": title,
                        "bullet_points": 5 if suffix != "B" else 3,
                        "images": 7 if suffix != "B" else 4,
                        "has_a_plus": suffix == "A",
                    },
                )
            )
        return out

    def _units(self, shop_id: str, suffix: str, base: int, d: date) -> int:
        rng = self._rng("units", shop_id, suffix, d)
        units = base * rng.uniform(0.75, 1.25)
        # 产品 C 在最近 7 天销量腰斩
        if suffix == "C" and (self.anchor - d).days < 7:
            units *= 0.45
        return max(0, round(units))

    def get_sales_daily(self, shop_id: str, start: date, end: date) -> list[SalesDaily]:
        out = []
        for suffix, _, price, base, _ in _PRODUCTS:
            for d in self._days(start, end):
                units = self._units(shop_id, suffix, base, d)
                sessions = round(units / 0.12) if units else 30
                out.append(
                    SalesDaily(
                        shop_id=shop_id,
                        asin=_asin(shop_id, suffix),
                        date=d,
                        units=units,
                        orders=max(0, units - units // 10),
                        revenue=round(units * price, 2),
                        sessions=sessions,
                        page_views=round(sessions * 1.3),
                        refunds=1 if units and d.day % 9 == 0 else 0,
                    )
                )
        return out

    def list_ad_campaigns(self, shop_id: str) -> list[AdCampaign]:
        out = []
        for suffix, title, *_ in _PRODUCTS:
            out.append(
                AdCampaign(
                    shop_id=shop_id,
                    campaign_id=f"{shop_id}-{suffix}-AUTO",
                    name=f"{title} - Auto",
                    targeting_type="auto",
                    # 产品 A 的自动广告预算偏低，每天都会花完
                    daily_budget=20.0 if suffix == "A" else 40.0,
                )
            )
            out.append(
                AdCampaign(
                    shop_id=shop_id,
                    campaign_id=f"{shop_id}-{suffix}-EXACT",
                    name=f"{title} - Exact",
                    targeting_type="manual",
                    daily_budget=30.0,
                )
            )
        return out

    def get_ad_metrics(self, shop_id: str, level: AdLevel, start: date, end: date) -> list[AdMetricRow]:
        # 先在搜索词粒度生成数据，其余粒度由它汇总，保证各粒度之间数字一致
        st_rows = self._search_term_rows(shop_id, start, end)
        if level == AdLevel.SEARCH_TERM:
            return st_rows
        groups: dict[tuple, AdMetricRow] = {}
        for r in st_rows:
            if level == AdLevel.CAMPAIGN:
                key = (r.date, r.campaign_id)
                proto = dict(entity_key="", entity_text="", match_type="", asin=r.asin)
            elif level == AdLevel.PRODUCT_AD:
                key = (r.date, r.campaign_id, r.asin)
                proto = dict(entity_key=r.asin, entity_text=r.asin, match_type="", asin=r.asin)
            else:  # KEYWORD：投放词就是搜索词拆分前的那一层
                target = r.entity_key.split("|", 1)[1]
                key = (r.date, r.campaign_id, target)
                kw_text, match = target.rsplit(":", 1)
                proto = dict(entity_key=target, entity_text=kw_text, match_type=match, asin=r.asin)
            if key not in groups:
                groups[key] = AdMetricRow(
                    shop_id=shop_id, date=r.date, level=level, campaign_id=r.campaign_id,
                    ad_group_id=r.ad_group_id, **proto,
                )
            g = groups[key]
            g.impressions += r.impressions
            g.clicks += r.clicks
            g.spend = round(g.spend + r.spend, 2)
            g.orders += r.orders
            g.sales = round(g.sales + r.sales, 2)
            g.units += r.units
        return list(groups.values())

    def _search_term_rows(self, shop_id: str, start: date, end: date) -> list[AdMetricRow]:
        out = []
        for suffix, _, price, *_ in _PRODUCTS:
            asin = _asin(shop_id, suffix)
            for camp in ("AUTO", "EXACT"):
                campaign_id = f"{shop_id}-{suffix}-{camp}"
                for i, term in enumerate(_SEARCH_TERMS):
                    if camp == "EXACT":
                        if i > 1:
                            continue
                        target = f"{term}:exact"
                    else:
                        target = "close-match:auto"
                    for d in self._days(start, end):
                        rng = self._rng("ad", shop_id, campaign_id, term, d)
                        clicks = rng.randint(1, 6)
                        cpc = rng.uniform(0.6, 1.2)
                        cvr = 0.12
                        if i == 5:  # "rubber scraper"：只花钱不出单
                            cvr = 0.0
                        elif i == 4 and camp == "AUTO":  # 高转化，适合收割
                            cvr = 0.30
                        elif i == 1 and camp == "EXACT":  # 竞争激烈，出价过高，ACOS 偏高
                            cpc *= 2.6
                            cvr = 0.08
                        orders = sum(1 for _ in range(clicks) if rng.random() < cvr)
                        out.append(
                            AdMetricRow(
                                shop_id=shop_id,
                                date=d,
                                level=AdLevel.SEARCH_TERM,
                                campaign_id=campaign_id,
                                ad_group_id=f"{campaign_id}-AG",
                                entity_key=f"{term}|{target}",
                                entity_text=term,
                                match_type=target.rsplit(":", 1)[1],
                                asin=asin,
                                impressions=clicks * rng.randint(40, 90),
                                clicks=clicks,
                                spend=round(clicks * cpc, 2),
                                orders=orders,
                                sales=round(orders * price, 2),
                                units=orders,
                            )
                        )
        return out
