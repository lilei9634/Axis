"""把各模块串成完整流程，命令行和定时任务都调用这里。"""

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import anthropic

from axis.agents.daily_review import run_daily_review
from axis.agents.diagnose import run_diagnosis
from axis.agents.llm import LLM, AgentError
from axis.config import Settings
from axis.db.schema import ReportRow, SyncRunRow
from axis.db.session import make_session_factory
from axis.erp.base import get_adapter
from axis.etl.sync import RawArchive, sync
from axis.notify.feishu import send_report

log = logging.getLogger(__name__)


def yesterday(settings: Settings) -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date() - timedelta(days=1)


def make_llm(settings: Settings, use_llm: bool) -> LLM | None:
    if not use_llm:
        return None
    return LLM(fallbacks=settings.llm_fallbacks, max_turns=settings.llm_max_turns)


def do_sync(settings: Settings, adapter_name: str, end: date, days: int) -> SyncRunRow:
    sf = make_session_factory(settings.db_url)
    archive = RawArchive()
    adapter = get_adapter(adapter_name, raw_sink=archive, anchor=end)
    return sync(adapter, sf, end, days, raw_archive=archive)


def _with_fallback(run, llm: LLM | None) -> ReportRow:
    """AI 分析失败时退回规则报告，保证每天至少有一份复盘。"""
    if llm is None:
        return run(None)
    try:
        return run(llm)
    except (AgentError, anthropic.APIError) as e:
        log.error("AI 分析失败，改用规则报告：%s", e)
        return run(None)


def do_review(settings: Settings, report_date: date, use_llm: bool, notify: bool) -> ReportRow:
    sf = make_session_factory(settings.db_url)
    report = _with_fallback(lambda llm: run_daily_review(sf, settings, report_date, llm), make_llm(settings, use_llm))
    if notify:
        send_report(settings.feishu_webhook, settings.feishu_secret, report)
    return report


def do_diagnose(settings: Settings, shop_id: str, asin: str, report_date: date, use_llm: bool, notify: bool) -> ReportRow:
    sf = make_session_factory(settings.db_url)
    report = _with_fallback(
        lambda llm: run_diagnosis(sf, settings, shop_id, asin, report_date, llm), make_llm(settings, use_llm)
    )
    if notify:
        send_report(settings.feishu_webhook, settings.feishu_secret, report)
    return report


def run_daily(settings: Settings, adapter_name: str, use_llm: bool = True, notify: bool = True) -> ReportRow:
    end = yesterday(settings)
    run = do_sync(settings, adapter_name, end, settings.sync_days)
    log.info("同步完成：%s", run.stats)
    return do_review(settings, end, use_llm, notify)
