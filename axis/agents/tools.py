"""给 agent 用的只读查询工具。agent 看完事实包后，可以用这些工具往下钻取明细。"""

from datetime import date

from sqlalchemy.orm import Session, sessionmaker

from axis.config import Thresholds
from axis.agents.llm import Tool
from axis.metrics import queries as q
from axis.metrics.facts import product_facts
from axis.models import AdLevel
from axis.watchlist import Scope


def _schema(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def build_tools(sf: sessionmaker[Session], report_date: date, th: Thresholds, scope: Scope) -> list[Tool]:
    """所有工具都只能查到关注范围内的产品和广告活动。"""

    def check(shop_id: str, asin: str = ""):
        if shop_id not in scope.asins:
            raise ValueError(f"店铺 {shop_id} 没有关注的产品；可用店铺：{', '.join(scope.shop_ids)}")
        if asin and not scope.has(shop_id, asin):
            raise ValueError(f"{asin} 不在关注列表里；店铺 {shop_id} 关注的产品：{', '.join(scope.asins[shop_id])}")

    def product_detail(inp: dict):
        check(inp["shop_id"], inp["asin"])
        with sf() as s:
            return product_facts(s, inp["shop_id"], inp["asin"], report_date, th, scope)

    def ad_breakdown(inp: dict):
        shop_id, asin = inp["shop_id"], inp["asin"]
        check(shop_id, asin)
        days = max(1, min(int(inp["days"]), 60))
        with sf() as s:
            start, end = q.window(report_date, days)
            return q.ad_entity_stats(
                s, shop_id, start, end, AdLevel(inp["level"]),
                campaign_id=inp["campaign_id"] or None,
                order_by=inp["order_by"], limit=max(1, min(int(inp["limit"]), 100)),
                scope_asins=[asin] if asin else scope.asins[shop_id],
                scope_campaigns=scope.campaigns.get(shop_id, []),
            )

    def product_daily(inp: dict):
        check(inp["shop_id"], inp["asin"])
        days = max(1, min(int(inp["days"]), 60))
        with sf() as s:
            return q.daily_series(s, inp["shop_id"], inp["asin"], *q.window(report_date, days))

    def list_campaigns(inp: dict):
        check(inp["shop_id"])
        with sf() as s:
            start, end = q.window(report_date, 14)
            out = []
            for c in q.campaigns(s, inp["shop_id"]):
                if c.campaign_id not in scope.campaigns.get(c.shop_id, []):
                    continue
                tot = q.ad_totals(s, start, end, AdLevel.CAMPAIGN, shop_id=c.shop_id, campaign_id=c.campaign_id)
                out.append({"campaign_id": c.campaign_id, "name": c.name, "ad_type": c.ad_type, "state": c.state,
                            "targeting_type": c.targeting_type, "daily_budget": c.daily_budget,
                            "last_14d": tot.to_dict()})
            return out

    str_ = {"type": "string"}
    return [
        Tool(
            "get_product_detail",
            "获取单个产品的完整诊断数据：listing、库存、近 7/30 天汇总、逐日数据、相关广告活动、主要投放词和搜索词、规则标记。",
            _schema({"shop_id": str_, "asin": str_}),
            product_detail,
        ),
        Tool(
            "get_ad_breakdown",
            "按粒度汇总某店铺最近 N 天的广告表现（截止到报告日期）。asin、campaign_id 填空字符串表示不过滤。",
            _schema({
                "shop_id": str_,
                "level": {"type": "string", "enum": [lv.value for lv in AdLevel]},
                "days": {"type": "integer", "description": "1-60"},
                "asin": str_,
                "campaign_id": str_,
                "order_by": {"type": "string", "enum": ["spend", "sales", "clicks", "acos"]},
                "limit": {"type": "integer", "description": "1-100"},
            }),
            ad_breakdown,
        ),
        Tool(
            "get_product_daily",
            "获取单个产品最近 N 天（1-60）的逐日销量、销售额、Sessions、广告花费和广告销售额。",
            _schema({"shop_id": str_, "asin": str_, "days": {"type": "integer"}}),
            product_daily,
        ),
        Tool(
            "list_campaigns",
            "列出某店铺里推广关注产品的广告活动，包含预算、状态和近 14 天表现。",
            _schema({"shop_id": str_}),
            list_campaigns,
        ),
    ]
