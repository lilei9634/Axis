"""规则引擎：用确定的阈值找出异常和机会。

每条规则都附带一个"默认建议动作"。没有 LLM 时（离线模式）直接用这些建议出报告；
有 LLM 时，这些标记作为事实输入，由 agent 综合判断、排优先级并给出最终建议。
"""

from dataclasses import asdict, dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from axis.config import Thresholds
from axis.metrics import queries as q
from axis.metrics.compute import pct_change, safe_div
from axis.models import AdLevel
from axis.watchlist import Scope

SEVERITY_ORDER = {"critical": 0, "warning": 1, "opportunity": 2}

CODE_LABEL = {
    "wasted_search_term": "无效搜索词",
    "harvest_search_term": "可收割搜索词",
    "high_acos_target": "投放 ACOS 过高",
    "budget_capped": "预算受限",
    "sales_drop": "销量骤降",
    "low_stock": "库存不足",
    "high_tacos": "TACoS 过高",
}


@dataclass
class Flag:
    severity: str  # critical / warning / opportunity
    code: str
    shop_id: str
    message: str
    asin: str = ""
    campaign_id: str = ""
    campaign_name: str = ""
    target: str = ""  # 关键词 / 搜索词等
    metrics: dict = field(default_factory=dict)
    # 默认建议
    action_type: str = "investigate"
    current_value: str = ""
    proposed_value: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate(
    s: Session, report_date: date, th: Thresholds, scope: Scope, shop_ids: list[str] | None = None
) -> list[Flag]:
    """只检查关注范围内的产品和广告活动。"""
    flags: list[Flag] = []
    for shop_id in scope.shop_ids:
        if shop_ids and shop_id not in shop_ids:
            continue
        flags += _ad_rules(s, shop_id, report_date, th, scope)
        flags += _product_rules(s, shop_id, report_date, th, scope)
    flags.sort(key=lambda f: (SEVERITY_ORDER[f.severity], -f.metrics.get("spend", 0)))
    return flags


def _ad_rules(s: Session, shop_id: str, end: date, th: Thresholds, scope: Scope) -> list[Flag]:
    out = []
    start, _ = q.window(end, th.lookback_days)
    in_scope = dict(scope_asins=scope.asins[shop_id], scope_campaigns=scope.campaigns.get(shop_id, []))

    # 1. 只花钱不出单的搜索词 → 否定
    for r in q.ad_entity_stats(s, shop_id, start, end, AdLevel.SEARCH_TERM, limit=None, **in_scope):
        if r["orders"] == 0 and (r["clicks"] >= th.min_clicks_no_order or r["spend"] >= th.min_spend_no_order):
            out.append(Flag(
                severity="warning", code="wasted_search_term", shop_id=shop_id, asin=r["asin"],
                campaign_id=r["campaign_id"], campaign_name=r["campaign_name"], target=r["text"],
                message=f"搜索词「{r['text']}」近 {th.lookback_days} 天点击 {r['clicks']} 次、花费 {r['spend']:.2f}，0 出单",
                metrics=_pick(r), action_type="negate_search_term", proposed_value="否定精准",
            ))

        # 2. 高转化低 ACOS 的非精准搜索词 → 收割为精准词
        elif (
            r["match_type"] != "exact"
            and r["orders"] >= th.harvest_min_orders
            and r["acos"] is not None
            and r["acos"] <= th.target_acos * 0.7
        ):
            out.append(Flag(
                severity="opportunity", code="harvest_search_term", shop_id=shop_id, asin=r["asin"],
                campaign_id=r["campaign_id"], campaign_name=r["campaign_name"], target=r["text"],
                message=f"搜索词「{r['text']}」出单 {r['orders']}，ACOS {r['acos']:.0%}，可加为精准词单独投放",
                metrics=_pick(r), action_type="add_keyword_exact", proposed_value=f"CPC {r['cpc'] or 0:.2f}",
            ))

    # 3. ACOS 过高的投放词 → 降价
    for r in q.ad_entity_stats(s, shop_id, start, end, AdLevel.KEYWORD, limit=None, **in_scope):
        if r["orders"] > 0 and r["clicks"] >= th.min_clicks_no_order and r["acos"] and r["acos"] > th.target_acos * 1.5:
            # 让 ACOS 回到目标附近所需的 CPC
            target_cpc = (r["cpc"] or 0) * th.target_acos / r["acos"]
            out.append(Flag(
                severity="warning", code="high_acos_target", shop_id=shop_id, asin=r["asin"],
                campaign_id=r["campaign_id"], campaign_name=r["campaign_name"],
                target=f"{r['text']} [{r['match_type']}]",
                message=f"投放「{r['text']}」ACOS {r['acos']:.0%}，高于目标 {th.target_acos:.0%}",
                metrics=_pick(r), action_type="lower_bid",
                current_value=f"CPC {r['cpc'] or 0:.2f}", proposed_value=f"出价降到约 {target_cpc:.2f}",
            ))

    # 4. 预算受限但 ACOS 健康的广告活动 → 加预算
    s7, _ = q.window(end, 7)
    for c in q.campaigns(s, shop_id):
        if c.campaign_id not in in_scope["scope_campaigns"] or not c.daily_budget or c.state != "enabled":
            continue
        tot = q.ad_totals(s, s7, end, AdLevel.CAMPAIGN, shop_id=shop_id, campaign_id=c.campaign_id)
        avg_spend = tot.spend / 7
        if avg_spend >= c.daily_budget * th.budget_capped_ratio and (tot.acos is None or tot.acos <= th.target_acos):
            out.append(Flag(
                severity="opportunity", code="budget_capped", shop_id=shop_id,
                campaign_id=c.campaign_id, campaign_name=c.name,
                message=f"广告活动「{c.name}」近 7 天日均花费 {avg_spend:.2f}，接近预算 {c.daily_budget:.2f}，ACOS {_pct(tot.acos)}",
                metrics={**tot.to_dict(), "avg_daily_spend": round(avg_spend, 2), "daily_budget": c.daily_budget},
                action_type="raise_budget",
                current_value=f"{c.daily_budget:.2f}", proposed_value=f"{c.daily_budget * 1.3:.2f}",
            ))
    return out


def _product_rules(s: Session, shop_id: str, end: date, th: Thresholds, scope: Scope) -> list[Flag]:
    out = []
    cur_s, cur_e = q.window(end, 7)
    prev_s, prev_e = q.window(end, 7, offset_days=7)
    s14, _ = q.window(end, 14)
    for p in q.products(s, shop_id):
        if not scope.has(shop_id, p.asin):
            continue
        cur = q.period_summary(s, cur_s, cur_e, shop_id, p.asin)
        prev = q.period_summary(s, prev_s, prev_e, shop_id, p.asin)
        base = dict(shop_id=shop_id, asin=p.asin, target=p.title)

        # 5. 销量骤降
        chg = pct_change(cur.sales.units, prev.sales.units)
        if prev.sales.units >= th.sales_drop_min_prev_units and chg is not None and chg <= th.sales_drop_pct:
            sess_chg = pct_change(cur.sales.sessions, prev.sales.sessions)
            out.append(Flag(
                severity="critical", code="sales_drop", **base,
                message=f"{p.asin} 近 7 天销量 {cur.sales.units}，较前 7 天 {prev.sales.units} 下降 {-chg:.0%}",
                metrics={
                    "units_7d": cur.sales.units, "units_prev_7d": prev.sales.units, "units_change": round(chg, 4),
                    "sessions_change": round(sess_chg, 4) if sess_chg is not None else None,
                    "cvr_7d": cur.sales.to_dict()["unit_session_pct"],
                    "cvr_prev_7d": prev.sales.to_dict()["unit_session_pct"],
                },
                action_type="investigate", proposed_value="排查流量（Sessions）还是转化下滑：价格、评论、Listing、Buy Box、广告位",
            ))

        # 6. 库存不足
        units14 = q.sales_totals(s, s14, end, shop_id, p.asin).units
        avg_daily = units14 / 14
        cover = safe_div(p.fba_stock + p.inbound_stock, avg_daily)
        if cover is not None and cover < th.stock_days_min:
            out.append(Flag(
                severity="critical" if cover < th.stock_days_min / 2 else "warning",
                code="low_stock", **base,
                message=f"{p.asin} 可售约 {cover:.0f} 天（库存 {p.fba_stock}+在途 {p.inbound_stock}，日均 {avg_daily:.1f}）",
                metrics={"fba_stock": p.fba_stock, "inbound_stock": p.inbound_stock,
                         "avg_daily_units_14d": round(avg_daily, 2), "days_of_cover": round(cover, 1)},
                action_type="restock",
                proposed_value=f"至少补 {max(0, round(avg_daily * 60 - p.fba_stock - p.inbound_stock))} 件（按 60 天覆盖）",
            ))

        # 7. TACoS 过高
        if cur.sales.revenue > 0 and cur.tacos is not None and cur.tacos > th.target_tacos * 1.5:
            out.append(Flag(
                severity="warning", code="high_tacos", **base,
                message=f"{p.asin} 近 7 天 TACoS {cur.tacos:.0%}，高于目标 {th.target_tacos:.0%}",
                metrics={"tacos_7d": round(cur.tacos, 4), "ad_spend_7d": round(cur.ads.spend, 2),
                         "revenue_7d": round(cur.sales.revenue, 2)},
                action_type="investigate", proposed_value="检查广告结构和自然排名，避免过度依赖广告",
            ))
    return out


def _pick(r: dict) -> dict:
    return {k: r[k] for k in ("impressions", "clicks", "spend", "orders", "sales", "acos", "cpc", "cvr")}


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v:.0%}"
