"""ERP 适配层接口。上层（同步、分析）只依赖这个协议，换数据源时只需要新写一个适配器。"""

from collections.abc import Callable
from datetime import date
from typing import Any, Protocol

from axis.models import AdCampaign, AdLevel, AdMetricRow, Product, SalesDaily, Shop

# 原始数据回调：(数据源, 接口名, 请求参数, 原始响应)，用于把原始 JSON 存档以便排查
RawSink = Callable[[str, str, dict[str, Any], Any], None]


class ErpAdapter(Protocol):
    name: str

    def list_shops(self) -> list[Shop]: ...

    def list_products(self, shop_id: str) -> list[Product]: ...

    def get_sales_daily(self, shop_id: str, start: date, end: date) -> list[SalesDaily]: ...

    def list_ad_campaigns(self, shop_id: str) -> list[AdCampaign]: ...

    def get_ad_metrics(
        self, shop_id: str, level: AdLevel, start: date, end: date
    ) -> list[AdMetricRow]: ...


def get_adapter(name: str, raw_sink: RawSink | None = None, anchor: date | None = None) -> ErpAdapter:
    if name == "fake":
        from axis.erp.fake import FakeAdapter

        return FakeAdapter(anchor=anchor)
    if name == "sellfox":
        from axis.erp.sellfox.adapter import SellfoxAdapter

        return SellfoxAdapter.from_settings(raw_sink=raw_sink)
    raise ValueError(f"未知的数据源: {name}（可选 fake / sellfox）")
