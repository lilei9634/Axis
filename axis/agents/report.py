"""报告的持久化与渲染：结构化结果入库，Markdown 全文落盘。"""

from collections import Counter
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from axis.db.schema import RecommendationRow, ReportRow
from axis.metrics.rules import CODE_LABEL, Flag

SEVERITY_LABEL = {"critical": "🔴 严重", "warning": "🟠 警告", "opportunity": "🟢 机会", "info": "ℹ️ 信息"}
ACTION_LABEL = {
    "negate_search_term": "否定搜索词",
    "add_keyword_exact": "收割为精准词",
    "raise_bid": "提高出价",
    "lower_bid": "降低出价",
    "raise_budget": "提高预算",
    "lower_budget": "降低预算",
    "pause_target": "暂停投放",
    "listing_change": "优化 Listing",
    "price_change": "调整价格",
    "coupon_promo": "优惠券/促销",
    "restock": "补货",
    "investigate": "需排查",
    "other": "其他",
}


def rules_output(flags: list[Flag], max_findings: int = 30) -> dict:
    """离线模式：不调用 LLM，直接把规则标记整理成和 agent 相同结构的输出。"""
    cnt = Counter(f.severity for f in flags)
    summary = (
        f"规则引擎共发现 {len(flags)} 项：严重 {cnt['critical']}、警告 {cnt['warning']}、机会 {cnt['opportunity']}。"
        "（离线模式，未经 AI 综合分析，建议逐条人工确认）"
    )
    findings = [
        {"severity": f.severity, "title": CODE_LABEL.get(f.code, f.code), "detail": f.message, "shop_id": f.shop_id, "asin": f.asin}
        for f in flags[:max_findings]
    ]
    recs = [
        {
            "action_type": f.action_type,
            "shop_id": f.shop_id,
            "asin": f.asin,
            "campaign_id": f.campaign_id,
            "target": f.target or f.campaign_name,
            "current_value": f.current_value,
            "proposed_value": f.proposed_value,
            "reason": f.message,
            "expected_impact": "",
            "confidence": "medium",
            "risk": "low" if f.action_type in ("negate_search_term", "add_keyword_exact") else "medium",
        }
        for f in flags
    ]
    return {"summary": summary, "findings": findings, "recommendations": recs, "questions_for_owner": []}


def save_report(
    sf: sessionmaker[Session],
    *,
    kind: str,
    title: str,
    report_date: date,
    output: dict,
    model: str,
    reports_dir: Path,
    shop_id: str = "",
    asin: str = "",
) -> ReportRow:
    reports_dir.mkdir(parents=True, exist_ok=True)
    name = f"{report_date.isoformat()}_{kind}" + (f"_{shop_id}_{asin}" if asin else "") + ".md"
    path = reports_dir / name
    path.write_text(render_markdown(title, output, model), encoding="utf-8")

    with sf() as s:
        report = ReportRow(
            kind=kind, report_date=report_date, shop_id=shop_id, asin=asin, model=model,
            summary=output["summary"], content=output, markdown_path=str(path),
        )
        s.add(report)
        s.flush()
        s.add_all(
            RecommendationRow(report_id=report.id, **{k: r[k] for k in _REC_FIELDS})
            for r in output["recommendations"]
        )
        s.commit()
    return report


_REC_FIELDS = [
    "shop_id", "asin", "campaign_id", "action_type", "target", "current_value",
    "proposed_value", "reason", "expected_impact", "confidence", "risk",
]


def render_markdown(title: str, output: dict, model: str) -> str:
    lines = [f"# {title}", "", f"> 生成方式：{model}", "", "## 总结", "", output["summary"], ""]

    if output["findings"]:
        lines += ["## 发现", ""]
        for f in output["findings"]:
            where = " / ".join(x for x in (f["shop_id"], f["asin"]) if x)
            lines.append(f"- **{SEVERITY_LABEL.get(f['severity'], f['severity'])}｜{f['title']}**"
                         + (f"（{where}）" if where else ""))
            lines.append(f"  {f['detail']}")
        lines.append("")

    recs = output["recommendations"]
    if recs:
        lines += ["## 调整建议（待审批，未执行）", "",
                  "| # | 动作 | 店铺 | ASIN | 对象 | 当前 | 建议 | 理由 | 信心 | 风险 |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for i, r in enumerate(recs, 1):
            cells = [str(i), ACTION_LABEL.get(r["action_type"], r["action_type"]), r["shop_id"], r["asin"],
                     r["target"], r["current_value"], r["proposed_value"],
                     r["reason"] + (f"；预期：{r['expected_impact']}" if r["expected_impact"] else ""),
                     r["confidence"], r["risk"]]
            lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
        lines.append("")

    if output["questions_for_owner"]:
        lines += ["## 需要你确认的问题", ""]
        lines += [f"{i}. {qq}" for i, qq in enumerate(output["questions_for_owner"], 1)]
        lines.append("")
    return "\n".join(lines)


def _cell(v: str) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")
