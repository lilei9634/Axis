"""单品诊断 Agent：对一个在售产品做全面体检，输出诊断结论和优化方案。"""

import json
import logging
from datetime import date

from sqlalchemy.orm import Session, sessionmaker

from axis.agents.llm import LLM
from axis.agents.prompts import system_prompt
from axis.agents.report import rules_output, save_report
from axis.agents.schemas import REVIEW_OUTPUT
from axis.agents.tools import build_tools
from axis.config import Settings
from axis.db.schema import ReportRow
from axis.watchlist import load_scope
from axis.metrics.facts import product_facts
from axis.metrics.rules import Flag

log = logging.getLogger(__name__)

SKILLS = ["product-diagnosis-sop", "listing-optimization", "amazon-ads-playbook"]

PROMPT = """请对下面这个产品做一次完整诊断，日期截止到 {report_date}。

按 SOP 的诊断框架逐项检查（流量、转化、广告结构与效率、库存、Listing），
findings 写清楚每一项的现状和问题，recommendations 给出按优先级排序的优化方案。

<facts>
{facts}
</facts>
"""


def run_diagnosis(
    sf: sessionmaker[Session], settings: Settings, shop_id: str, asin: str, report_date: date, llm: LLM | None
) -> ReportRow:
    with sf() as s:
        scope = load_scope(s)
        facts = product_facts(s, shop_id, asin, report_date, settings.thresholds, scope)

    title = f"产品诊断 {shop_id} / {asin}（{facts['product']['title']}）{report_date.isoformat()}"
    if llm is None:
        output, model = rules_output([Flag(**f) for f in facts["flags"]]), "rules"
    else:
        result = llm.run(
            model=settings.diagnose_model,
            effort=settings.diagnose_effort,
            system=system_prompt(settings.skills_dir, SKILLS),
            prompt=PROMPT.format(
                report_date=report_date.isoformat(), facts=json.dumps(facts, ensure_ascii=False, default=str)
            ),
            output_schema=REVIEW_OUTPUT,
            tools=build_tools(sf, report_date, settings.thresholds, scope),
        )
        log.info("诊断完成：%s 轮，用量 %s", result.turns, result.usage)
        output, model = result.output, result.model
    return save_report(
        sf, kind="diagnosis", title=title, report_date=report_date, output=output, model=model,
        reports_dir=settings.reports_dir, shop_id=shop_id, asin=asin,
    )
