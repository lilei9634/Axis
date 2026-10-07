"""赛狐适配器：把赛狐网页接口的数据转换成统一模型。"""

from datetime import date, timedelta

from axis.config import get_settings
from axis.erp.base import RawSink
from axis.erp.sellfox import endpoints as E
from axis.erp.sellfox.client import AuthState, SellfoxClient
from axis.models import AdCampaign, AdLevel, AdMetricRow, Product, SalesDaily, Shop

_AD_ENDPOINT = {
    AdLevel.CAMPAIGN: E.AD_METRICS_CAMPAIGN,
    AdLevel.PRODUCT_AD: E.AD_METRICS_PRODUCT_AD,
    AdLevel.KEYWORD: E.AD_METRICS_KEYWORD,
    AdLevel.SEARCH_TERM: E.AD_METRICS_SEARCH_TERM,
}


class SellfoxAdapter:
    name = "sellfox"

    def __init__(self, client: SellfoxClient, page_size: int = 100):
        self.client = client
        self.page_size = page_size

    @classmethod
    def from_settings(cls, raw_sink: RawSink | None = None) -> "SellfoxAdapter":
        st = get_settings()
        client = SellfoxClient(
            base_url=st.sellfox_base_url,
            auth=AuthState.load(st.sellfox_auth_path),
            min_interval=st.sellfox_min_interval,
            jitter=st.sellfox_jitter,
            raw_sink=raw_sink,
        )
        return cls(client, page_size=st.sellfox_page_size)

    def _ctx(
        self, shop_id: str = "", start: date | None = None, end: date | None = None, asins: list[str] | None = None
    ) -> E.RequestContext:
        return E.RequestContext(shop_id=shop_id, start=start, end=end, page_size=self.page_size, asins=asins or [])

    def list_shops(self) -> list[Shop]:
        return self.client.fetch_all(E.SHOPS, self._ctx())

    def list_products(self, shop_id: str, asins: list[str] | None = None) -> list[Product]:
        return self.client.fetch_all(E.PRODUCTS, self._ctx(shop_id, asins=asins))

    def _fetch_range(self, name: str, shop_id: str, start: date, end: date, asins: list[str] | None) -> list:
        if not self.client.endpoint(name).per_day:
            return self.client.fetch_all(name, self._ctx(shop_id, start, end, asins))
        out = []
        d = start
        while d <= end:
            out += self.client.fetch_all(name, self._ctx(shop_id, d, d, asins))
            d += timedelta(days=1)
        return out

    def get_sales_daily(
        self, shop_id: str, start: date, end: date, asins: list[str] | None = None
    ) -> list[SalesDaily]:
        return self._fetch_range(E.SALES_DAILY, shop_id, start, end, asins)

    def list_ad_campaigns(self, shop_id: str) -> list[AdCampaign]:
        return self.client.fetch_all(E.AD_CAMPAIGNS, self._ctx(shop_id))

    def get_ad_metrics(
        self, shop_id: str, level: AdLevel, start: date, end: date, asins: list[str] | None = None
    ) -> list[AdMetricRow]:
        return self._fetch_range(_AD_ENDPOINT[level], shop_id, start, end, asins)
