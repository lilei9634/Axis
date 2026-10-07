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

    stats: dict[str, int] = {}
    status, error = "failed", "interrupted"
    try:
        shops = [sh for sh in adapter.list_shops() if not shop_ids or sh.shop_id in shop_ids]
        with session_factory() as s:
            stats["shops"] = upsert(
                s, ShopRow, [sh.model_dump() | {"updated_at": utcnow()} for sh in shops], ["shop_id"]
            )
            s.commit()

        for shop in shops:
            log.info("同步店铺 %s (%s)", shop.name, shop.shop_id)
            # 每家店单独提交，某家店失败时前面的数据不会丢
            with session_factory() as s:
                products = adapter.list_products(shop.shop_id)
                _add(stats, "products", upsert(
                    s, ProductRow, [p.model_dump() | {"updated_at": utcnow()} for p in products],
                    ["shop_id", "asin"],
                ))
                sales = adapter.get_sales_daily(shop.shop_id, start, end)
                _add(stats, "sales_daily", upsert(
                    s, SalesDailyRow, [r.model_dump() for r in sales], ["shop_id", "asin", "date"]
                ))
                campaigns = adapter.list_ad_campaigns(shop.shop_id)
                _add(stats, "ad_campaigns", upsert(
                    s, AdCampaignRow, [c.model_dump() | {"updated_at": utcnow()} for c in campaigns],
                    ["shop_id", "campaign_id"],
                ))
                for level in AD_LEVELS:
                    rows = adapter.get_ad_metrics(shop.shop_id, level, start, end)
                    _add(stats, f"ad_{level.value}", upsert(
                        s, AdMetricDailyRow, [r.model_dump(mode="python") | {"level": level.value} for r in rows],
                        ["shop_id", "date", "level", "campaign_id", "ad_group_id", "entity_key"],
                    ))
                s.commit()
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
