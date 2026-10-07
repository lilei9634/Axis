"""统一数据模型。不管数据来自赛狐网页、赛狐官方 API 还是亚马逊 SP-API，适配器都要转换成这些模型。"""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class AdLevel(StrEnum):
    CAMPAIGN = "campaign"
    PRODUCT_AD = "product_ad"  # 广告产品（按被推广的 ASIN 汇总）
    KEYWORD = "keyword"  # 投放词 / 投放目标
    SEARCH_TERM = "search_term"  # 用户搜索词


class Shop(BaseModel):
    shop_id: str
    name: str
    marketplace: str  # 站点，如 US / UK / DE
    currency: str = "USD"


class Product(BaseModel):
    shop_id: str
    asin: str
    sku: str = ""
    title: str = ""
    price: float | None = None
    fba_stock: int = 0  # FBA 可售库存
    inbound_stock: int = 0  # 在途库存
    status: str = ""
    rating: float | None = None
    review_count: int | None = None
    # listing 详情（标题、五点、图片数量、A+ 等），字段不固定，原样保存
    listing: dict = Field(default_factory=dict)


class SalesDaily(BaseModel):
    shop_id: str
    asin: str
    date: date
    units: int = 0
    orders: int = 0
    revenue: float = 0.0
    sessions: int = 0
    page_views: int = 0
    refunds: int = 0


class AdCampaign(BaseModel):
    shop_id: str
    campaign_id: str
    name: str
    ad_type: str = "SP"  # SP / SB / SD
    state: str = "enabled"
    targeting_type: str = ""  # auto / manual
    daily_budget: float | None = None


class AdMetricRow(BaseModel):
    """某一天、某个粒度的一行广告数据。"""

    shop_id: str
    date: date
    level: AdLevel
    campaign_id: str
    ad_group_id: str = ""
    # 这一行实体的唯一键：campaign 层为空；product_ad 层为 ASIN；keyword 层为投放 ID 或 "词|匹配方式"；
    # search_term 层为 "搜索词|投放 ID"
    entity_key: str = ""
    entity_text: str = ""  # 可读文本：关键词 / 搜索词 / ASIN
    match_type: str = ""
    asin: str = ""  # 关联的推广 ASIN（能拿到时填写）
    impressions: int = 0
    clicks: int = 0
    spend: float = 0.0
    orders: int = 0
    sales: float = 0.0
    units: int = 0
