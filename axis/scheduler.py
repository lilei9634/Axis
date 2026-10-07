"""本地常驻定时任务：每天固定时间跑一次 run-daily。部署到服务器后也可以改用系统 cron。"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from axis.config import Settings
from axis.notify.feishu import send_text
from axis.pipeline import run_daily

log = logging.getLogger(__name__)


def start(settings: Settings, adapter_name: str, use_llm: bool = True, notify: bool = True) -> None:
    hour, minute = (int(x) for x in settings.daily_run_time.split(":"))
    sched = BlockingScheduler(timezone=settings.timezone)

    def job():
        try:
            report = run_daily(settings, adapter_name, use_llm, notify)
            log.info("每日复盘完成：%s", report.markdown_path)
        except Exception as e:
            log.exception("每日任务失败")
            # 最常见的原因是赛狐登录失效，必须让人知道
            if notify and settings.feishu_webhook:
                try:
                    send_text(settings.feishu_webhook, settings.feishu_secret,
                              f"⚠️ Axis 每日任务失败：{type(e).__name__}: {e}")
                except Exception:
                    log.exception("飞书告警发送失败")

    sched.add_job(job, CronTrigger(hour=hour, minute=minute, timezone=settings.timezone),
                  id="run_daily", misfire_grace_time=3600, coalesce=True)
    log.info("定时任务已启动：每天 %s（%s）运行，数据源 %s", settings.daily_run_time, settings.timezone, adapter_name)
    sched.start()
