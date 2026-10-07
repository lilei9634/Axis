"""赛狐网页接口登记表。

这里是唯一需要根据抓包结果填写的地方。每个逻辑接口（店铺列表、产品列表、广告报表……）对应一个 Endpoint：
请求怎么发（路径、方法、参数模板、分页方式），响应怎么解析成统一模型。

只有登记在这里、并且 readonly=True 的接口才能被调用（见 client.py），这是 MVP 只读的硬性保证。

填写流程：
1. 在本机用 Chrome 打开赛狐对应页面，导出 HAR，运行 `axis har sanitize` 脱敏；
2. 运行 `axis har inspect` 找到对应的接口，确认路径、参数和响应结构；
3. 在 ENDPOINTS 里登记，并在 tests/ 里用脱敏后的响应样例写解析测试。
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import BaseModel

# 适配器需要的逻辑接口名
SHOPS = "shops"
PRODUCTS = "products"
SALES_DAILY = "sales_daily"
AD_CAMPAIGNS = "ad_campaigns"
AD_METRICS_CAMPAIGN = "ad_metrics_campaign"
AD_METRICS_PRODUCT_AD = "ad_metrics_product_ad"
AD_METRICS_KEYWORD = "ad_metrics_keyword"
AD_METRICS_SEARCH_TERM = "ad_metrics_search_term"

REQUIRED = [
    SHOPS, PRODUCTS, SALES_DAILY, AD_CAMPAIGNS,
    AD_METRICS_CAMPAIGN, AD_METRICS_PRODUCT_AD, AD_METRICS_KEYWORD, AD_METRICS_SEARCH_TERM,
]


@dataclass
class RequestContext:
    """构造请求参数时可用的上下文。"""

    shop_id: str = ""
    start: date | None = None
    end: date | None = None
    page: int = 1
    page_size: int = 100
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Endpoint:
    name: str
    method: str  # 很多 ERP 的查询接口也用 POST，所以只读与否不能靠 HTTP 方法判断
    path: str
    # 构造请求：返回 (query 参数, JSON 请求体)
    build: Callable[[RequestContext], tuple[dict, dict | None]]
    # 从响应里取出当页的记录列表
    items: Callable[[Any], list[dict]]
    # 把一条记录转换成统一模型（可以返回 None 跳过，或返回多个模型）
    parse: Callable[[dict, RequestContext], BaseModel | list[BaseModel] | None]
    # 从响应里取总条数，用于判断是否还有下一页；为 None 时按"当页条数 < page_size"判断
    total: Callable[[Any], int] | None = None
    paginated: bool = True
    # 报表接口如果只返回整个时间段的汇总（不分天），设为 True，适配器会逐天请求
    per_day: bool = False
    readonly: bool = True
    # 判断业务是否成功；默认检查常见的 code 字段
    ok: Callable[[Any], bool] | None = None


def dig(obj: Any, path: str, default: Any = None) -> Any:
    """按点号路径取值，例如 dig(payload, "data.list")。"""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return default
    return cur


def num(v: Any, default: float = 0.0) -> float:
    """把 "1,234.5"、"12.3%"、None 之类的值转成数字。"""
    if v is None or v == "" or v == "-":
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("%", "").strip()
    for sym in ("$", "£", "€", "¥", "C$", "A$"):
        s = s.replace(sym, "")
    try:
        return float(s)
    except ValueError:
        return default


# 抓包后在这里登记，例如：
#
# ENDPOINTS[PRODUCTS] = Endpoint(
#     name=PRODUCTS,
#     method="POST",
#     path="/api/xxx/product/list",
#     build=lambda ctx: ({}, {"shopIds": [ctx.shop_id], "pageNo": ctx.page, "pageSize": ctx.page_size}),
#     items=lambda p: dig(p, "data.rows", []),
#     total=lambda p: dig(p, "data.total", 0),
#     parse=lambda r, ctx: Product(shop_id=ctx.shop_id, asin=r["asin"], sku=r.get("sku", ""), ...),
# )
ENDPOINTS: dict[str, Endpoint] = {}
