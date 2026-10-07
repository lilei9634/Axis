"""命令行入口：uv run axis --help"""

import json
import logging
from datetime import date
from pathlib import Path

import typer
from sqlalchemy import select

from axis import pipeline
from axis.config import get_settings

app = typer.Typer(help="Axis：基于赛狐 ERP 的亚马逊运营助手（MVP：只读诊断 + 每日复盘）", no_args_is_help=True)
har_app = typer.Typer(help="赛狐抓包（HAR）工具", no_args_is_help=True)
recs_app = typer.Typer(help="查看和审批调整建议", no_args_is_help=True)
app.add_typer(har_app, name="har")
app.add_typer(recs_app, name="recs")

AdapterOpt = typer.Option("sellfox", "--adapter", "-a", help="数据源：sellfox / fake")
LlmOpt = typer.Option(True, "--llm/--no-llm", help="是否调用 Claude 分析；--no-llm 只用规则引擎出报告")
NotifyOpt = typer.Option(True, "--notify/--no-notify", help="是否推送到飞书")


@app.callback()
def _main(verbose: bool = typer.Option(False, "--verbose", "-v")):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # 依赖库的请求日志太吵
    for noisy in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _date(value: str | None) -> date:
    return date.fromisoformat(value) if value else pipeline.yesterday(get_settings())


@app.command()
def login():
    """打开浏览器手动登录赛狐，保存登录态（在你自己的电脑上运行）。"""
    from axis.erp.sellfox.session import login_interactive

    st = get_settings()
    login_interactive(st.sellfox_login_url, st.sellfox_base_url, st.sellfox_auth_path, st.data_dir / "browser-profile")
    typer.echo(f"登录态已保存到 {st.sellfox_auth_path}（等同于登录凭证，请勿外传）")


@app.command("sync")
def sync_cmd(
    days: int = typer.Option(None, help="回补最近 N 天，默认取 AXIS_SYNC_DAYS"),
    end: str = typer.Option(None, help="最后一天 YYYY-MM-DD，默认昨天"),
    adapter: str = AdapterOpt,
):
    """从 ERP 同步数据到本地数据库。"""
    st = get_settings()
    run = pipeline.do_sync(st, adapter, _date(end), days or st.sync_days)
    typer.echo(f"同步 {run.status}：{run.start_date} ~ {run.end_date}\n{json.dumps(run.stats, ensure_ascii=False)}")


@app.command()
def review(
    report_date: str = typer.Option(None, "--date", help="复盘日期 YYYY-MM-DD，默认昨天"),
    llm: bool = LlmOpt,
    notify: bool = NotifyOpt,
):
    """生成每日复盘报告（基于本地数据库，不会重新同步）。"""
    report = pipeline.do_review(get_settings(), _date(report_date), llm, notify)
    _print_report(report)


@app.command()
def diagnose(
    shop: str = typer.Option(..., "--shop", help="店铺 ID"),
    asin: str = typer.Option(..., "--asin"),
    report_date: str = typer.Option(None, "--date", help="截止日期 YYYY-MM-DD，默认昨天"),
    llm: bool = LlmOpt,
    notify: bool = typer.Option(False, "--notify/--no-notify", help="是否推送到飞书"),
):
    """对单个在售产品做完整诊断，输出优化方案。"""
    report = pipeline.do_diagnose(get_settings(), shop, asin, _date(report_date), llm, notify)
    _print_report(report)


@app.command("run-daily")
def run_daily(adapter: str = AdapterOpt, llm: bool = LlmOpt, notify: bool = NotifyOpt):
    """同步数据 → 计算指标 → AI 复盘 → 推送飞书。"""
    _print_report(pipeline.run_daily(get_settings(), adapter, llm, notify))


@app.command()
def daemon(adapter: str = AdapterOpt, llm: bool = LlmOpt, notify: bool = NotifyOpt):
    """常驻运行，每天定时执行 run-daily。"""
    from axis.scheduler import start

    start(get_settings(), adapter, llm, notify)


@har_app.command("sanitize")
def har_sanitize(src: Path, dst: Path):
    """脱敏 HAR 文件：去掉 cookie、token、手机号等，脱敏后的文件才可以外发。"""
    from axis.har.sanitize import sanitize_file

    n = sanitize_file(src, dst)
    typer.echo(f"已脱敏 {n} 条请求 → {dst}")


@har_app.command("inspect")
def har_inspect(src: Path, out: Path = typer.Option(None, help="把接口清单写入 Markdown 文件")):
    """列出 HAR 里的 JSON 接口、参数和响应结构。"""
    from axis.har.inspect import inspect_file, to_markdown

    apis = inspect_file(src)
    if out:
        out.write_text(to_markdown(apis), encoding="utf-8")
        typer.echo(f"共 {len(apis)} 个接口，详情已写入 {out}")
    for a in apis:
        typer.echo(f"{a['method']:6} {a['path']:60} ×{a['count']:<3} {a['category']}")


@recs_app.command("list")
def recs_list(
    status: str = typer.Option("proposed", help="proposed / approved / rejected / all"),
    limit: int = 50,
):
    """列出调整建议。"""
    from axis.db.schema import RecommendationRow
    from axis.db.session import make_session_factory

    sf = make_session_factory(get_settings().db_url)
    with sf() as s:
        stmt = select(RecommendationRow).order_by(RecommendationRow.id.desc()).limit(limit)
        if status != "all":
            stmt = stmt.where(RecommendationRow.status == status)
        for r in s.scalars(stmt):
            typer.echo(f"#{r.id:<5} [{r.status}] {r.action_type:20} {r.shop_id}/{r.asin} {r.target} "
                       f"{r.current_value} → {r.proposed_value}｜{r.reason[:60]}")


@recs_app.command("decide")
def recs_decide(ids: list[int], approve: bool = typer.Option(..., "--approve/--reject")):
    """审批建议。MVP 阶段只记录审批结果，不会执行任何操作。"""
    from axis.db.schema import RecommendationRow, utcnow
    from axis.db.session import make_session_factory

    sf = make_session_factory(get_settings().db_url)
    with sf() as s:
        rows = list(s.scalars(select(RecommendationRow).where(RecommendationRow.id.in_(ids))))
        for r in rows:
            r.status, r.decided_at = ("approved" if approve else "rejected"), utcnow()
        s.commit()
    typer.echo(f"已{'批准' if approve else '拒绝'} {len(rows)} 条建议（MVP 阶段不会自动执行）")


def _print_report(report) -> None:
    typer.echo(f"\n报告 #{report.id}（{report.model}）：{report.markdown_path}\n")
    typer.echo(report.summary)
    typer.echo(f"\n建议 {len(report.content.get('recommendations', []))} 条，待审批：axis recs list")


def main() -> None:
    """命令行入口：已知的业务错误只打印一行提示，不打印堆栈。"""
    from axis.agents.llm import AgentError
    from axis.erp.sellfox.client import SellfoxError
    from axis.notify.feishu import FeishuError

    try:
        app()
    except (SellfoxError, FeishuError, AgentError, ValueError, RuntimeError) as e:
        typer.secho(f"错误：{e}", fg=typer.colors.RED, err=True)
        raise SystemExit(1)
