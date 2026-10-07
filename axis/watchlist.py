"""关注列表与分析范围。

系统只同步和分析你指定的产品。关注列表按 (店铺, ASIN) 记录，店铺留空表示"所有店铺里的这个 ASIN"。
同步时用它过滤 ERP 数据；分析时用 Scope 把所有统计限定在关注的产品及其广告活动内，
这样取消关注后，历史数据还在数据库里，但不会再出现在报告中。
"""

from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from axis.db.schema import AdMetricDailyRow, ProductRow, TrackedProductRow
from axis.models import AdLevel


class EmptyWatchlist(RuntimeError):
    pass


@dataclass
class Watchlist:
    entries: list[tuple[str, str]]  # (shop_id, asin)，shop_id 为空表示所有店铺

    @classmethod
    def load(cls, s: Session) -> "Watchlist":
        rows = s.execute(select(TrackedProductRow.shop_id, TrackedProductRow.asin)).all()
        return cls([(shop, asin.upper()) for shop, asin in rows])

    def __bool__(self) -> bool:
        return bool(self.entries)

    def matches(self, shop_id: str, asin: str) -> bool:
        asin = asin.upper()
        return any(a == asin and (not sh or sh == shop_id) for sh, a in self.entries)

    def asins_for_shop(self, shop_id: str) -> list[str]:
        """这家店可能关注的 ASIN（含"所有店铺"条目），用于让 ERP 接口按 ASIN 过滤。"""
        return sorted({a for sh, a in self.entries if not sh or sh == shop_id})

    def require(self) -> "Watchlist":
        if not self.entries:
            raise EmptyWatchlist("关注列表为空。请先用 `axis track add <ASIN> [--shop 店铺ID]` 添加要分析的产品")
        return self


def add(s: Session, asins: list[str], shop_id: str = "", note: str = "") -> int:
    existing = set(Watchlist.load(s).entries)
    added = 0
    for asin in asins:
        key = (shop_id, asin.strip().upper())
        if key not in existing:
            s.add(TrackedProductRow(shop_id=shop_id, asin=key[1], note=note))
            existing.add(key)
            added += 1
    s.commit()
    return added


def remove(s: Session, asins: list[str], shop_id: str | None = None) -> int:
    """取消关注。shop_id 为 None 时删除这个 ASIN 的所有条目。"""
    stmt = delete(TrackedProductRow).where(TrackedProductRow.asin.in_([a.strip().upper() for a in asins]))
    if shop_id is not None:
        stmt = stmt.where(TrackedProductRow.shop_id == shop_id)
    n = s.execute(stmt).rowcount
    s.commit()
    return n


@dataclass
class Scope:
    """一次分析的范围：关注的产品，以及推广这些产品的广告活动。"""

    asins: dict[str, list[str]] = field(default_factory=dict)  # shop_id -> 关注的 ASIN
    campaigns: dict[str, list[str]] = field(default_factory=dict)  # shop_id -> 相关广告活动

    @property
    def shop_ids(self) -> list[str]:
        return sorted(self.asins)

    def has(self, shop_id: str, asin: str) -> bool:
        return asin in self.asins.get(shop_id, [])


def load_scope(s: Session) -> Scope:
    wl = Watchlist.load(s).require()
    scope = Scope()
    for p in s.scalars(select(ProductRow).order_by(ProductRow.shop_id, ProductRow.asin)):
        if wl.matches(p.shop_id, p.asin):
            scope.asins.setdefault(p.shop_id, []).append(p.asin)
    if not scope.asins:
        raise EmptyWatchlist("数据库里还没有关注产品的数据。请先运行 `axis sync`，并用 `axis track list` 确认 ASIN 和店铺填写正确")
    t = AdMetricDailyRow
    for shop_id, asins in scope.asins.items():
        rows = s.execute(
            select(t.campaign_id).distinct()
            .where(t.shop_id == shop_id, t.level == AdLevel.PRODUCT_AD.value, t.asin.in_(asins))
        ).scalars()
        scope.campaigns[shop_id] = sorted(rows)
    return scope
