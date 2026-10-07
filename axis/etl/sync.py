"""把 ERP 数据同步到本地数据库。幂等：同一时间段重复同步只会更新，不会重复插入。"""

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session, sessionmaker

from axis.db.schema import (
    AdCampaignRow,
    AdMetricDailyRow,
    ProductRow,
    RawSnapshotRow,
    SalesDailyRow,
    ShopRow,
    SyncRunRow,
    utcnow,
)
from axis.db.session import upsert
from axis.erp.base import ErpAdapter
from axis.models import AdLevel
from axis.watchlist import Watchlist

log = logging.getLogger(__name__)

AD_LEVELS = [AdLevel.CAMPAIGN, AdLevel.PRODUCT_AD, AdLevel.KEYWORD, AdLevel.SEARCH_TERM]


class RawArchive:
    """收集适配器吐出的原始响应，同步结束后统一写入 raw_snapshots。"""

    def __init__(self):
        self.items: list[dict] = []

    def __call__(self, source, endpoint, params, payload):
        self.items.append(dict(source=source, endpoint=endpoint, params=params, payload=payload))


def sync(
    adapter: ErpAdapter,
    session_factory: sessionmaker[Session],
    end: date,
    days: int,
    shop_ids: list[str] | None = None,
    raw_archive: RawArchive | None = None,
) -> SyncRunRow:
    start = end - timedelta(days=days - 1)
    with session_factory() as s:
        run = SyncRunRow(adapter=adapter.name, start_date=start, end_date=end)
        s.add(run)
        s.commit()

    stats: dict = {}
    status, error = "failed", "interrupted"
    try:
        with session_factory() as s:
            wl = Watchlist.load(s).require()
        found: set[tuple[str, str]] = set()
        shops = [
            sh for sh in adapter.list_shops()
            if (not shop_ids or sh.shop_id in shop_ids) and wl.asins_for_shop(sh.shop_id)
        ]
        for shop in shops:
            wanted = wl.asins_for_shop(shop.shop_id)
            products = [p for p in adapter.list_products(shop.shop_id, wanted) if wl.matches(shop.shop_id, p.asin)]
            if not products:
                log.info("店铺 %s (%s) 没有关注的产品，跳过", shop.name, shop.shop_id)
                continue
            tracked = {p.asin for p in products}
            found |= {(shop.shop_id, a) for a in tracked}
            log.info("同步店铺 %s (%s)：%s", shop.name, shop.shop_id, ", ".join(sorted(tracked)))
            asins = sorted(tracked)

            # 先拉"广告产品"粒度，确定推广这些产品的广告活动，其余粒度按它过滤
            ad_rows = {AdLevel.PRODUCT_AD: [
                r for r in adapter.get_ad_metrics(shop.shop_id, AdLevel.PRODUCT_AD, start, end, asins)
                if r.asin in tracked
            ]}
            camp_ids = {r.campaign_id for r in ad_rows[AdLevel.PRODUCT_AD]}

            def in_scope(r) -> bool:
                # 有 ASIN 的行按 ASIN 过滤；没有 ASIN 的行（如广告活动汇总）按所属广告活动过滤
                return r.asin in tracked if r.asin else r.campaign_id in camp_ids

            for level in AD_LEVELS:
                if level != AdLevel.PRODUCT_AD:
                    ad_rows[level] = [
                        r for r in adapter.get_ad_metrics(shop.shop_id, level, start, end, asins) if in_scope(r)
                    ]
            sales = [r for r in adapter.get_sales_daily(shop.shop_id, start, end, asins) if r.asin in tracked]
            campaigns = [c for c in adapter.list_ad_campaigns(shop.shop_id) if c.campaign_id in camp_ids]

            # 每家店单独提交，某家店失败时前面的数据不会丢
            with session_factory() as s:
                _add(stats, "shops", upsert(s, ShopRow, [shop.model_dump() | {"updated_at": utcnow()}], ["shop_id"]))
                _add(stats, "products", upsert(
                    s, ProductRow, [p.model_dump() | {"updated_at": utcnow()} for p in products],
                    ["shop_id", "asin"],
                ))
                _add(stats, "sales_daily", upsert(
                    s, SalesDailyRow, [r.model_dump() for r in sales], ["shop_id", "asin", "date"]
                ))
                _add(stats, "ad_campaigns", upsert(
                    s, AdCampaignRow, [c.model_dump() | {"updated_at": utcnow()} for c in campaigns],
                    ["shop_id", "campaign_id"],
                ))
                for level in AD_LEVELS:
                    _add(stats, f"ad_{level.value}", upsert(
                        s, AdMetricDailyRow,
                        [r.model_dump(mode="python") | {"level": level.value} for r in ad_rows[level]],
                        ["shop_id", "date", "level", "campaign_id", "ad_group_id", "entity_key"],
                    ))
                s.commit()

        stats["tracked_found"] = len(found)
        missing = [
            f"{sh or '*'}/{a}" for sh, a in wl.entries
            if not any(fa == a and (not sh or fs == sh) for fs, fa in found)
            and (not shop_ids or not sh or sh in shop_ids)
        ]
        if missing:
            stats["not_found"] = missing
            log.warning("这些关注的产品在 ERP 里没有找到，请检查 ASIN 和店铺 ID：%s", ", ".join(missing))
        status, error = "ok", ""
    except Exception as e:  # 记录失败原因后继续抛出，让调用方决定是否中止
        status, error = "failed", f"{type(e).__name__}: {e}"
        raise
    finally:
        with session_factory() as s:
            run = s.get(SyncRunRow, run.id)
            run.status, run.error, run.stats, run.finished_at = status, error, stats, utcnow()
            if raw_archive:
                s.add_all(RawSnapshotRow(sync_run_id=run.id, **item) for item in raw_archive.items)
            s.commit()
    return run


def _add(stats: dict, key: str, n: int):
    stats[key] = stats.get(key, 0) + n
