"""每日复盘 Agent：汇总 5 家店的数据和规则标记，挑出重点问题，给出可执行的调整建议。"""

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
from axis.metrics.facts import daily_facts

log = logging.getLogger(__name__)

SKILLS = ["daily-review-sop", "amazon-ads-playbook"]

PROMPT = """请完成 {report_date} 的每日复盘。

下面是事实包（JSON）：各店铺昨日、近 7 天汇总及与前 7 天的对比，每个产品的近 7 天表现，以及规则引擎的标记（flags，共 {flags_total} 条，按严重程度排序）。
规则标记是候选问题，不一定都要采纳：请结合上下文判断，合并同类项，去掉噪音，必要时用工具查明细后再下结论。

<facts>
{facts}
</facts>
"""


def run_daily_review(
    sf: sessionmaker[Session], settings: Settings, report_date: date, llm: LLM | None
) -> ReportRow:
    with sf() as s:
        facts, flags = daily_facts(s, report_date, settings.thresholds)
    if not facts["shops"]:
        raise RuntimeError("数据库里没有店铺数据，请先运行 axis sync")

    title = f"每日复盘 {report_date.isoformat()}"
    if llm is None:
        output, model = rules_output(flags), "rules"
    else:
        result = llm.run(
            model=settings.review_model,
            effort=settings.review_effort,
            system=system_prompt(settings.skills_dir, SKILLS),
            prompt=PROMPT.format(
                report_date=report_date.isoformat(),
                flags_total=facts["flags_total"],
                facts=json.dumps(facts, ensure_ascii=False, default=str),
            ),
            output_schema=REVIEW_OUTPUT,
            tools=build_tools(sf, report_date, settings.thresholds),
        )
        log.info("复盘完成：%s 轮，用量 %s", result.turns, result.usage)
        output, model = result.output, result.model
    return save_report(
        sf, kind="daily_review", title=title, report_date=report_date, output=output,
        model=model, reports_dir=settings.reports_dir,
    )
