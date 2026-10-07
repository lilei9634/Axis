"""飞书自定义机器人推送。

在飞书群里添加"自定义机器人"，复制 webhook 地址；建议开启"签名校验"并把密钥填到 AXIS_FEISHU_SECRET。
"""

import base64
import hashlib
import hmac
import time

import httpx

from axis.agents.report import ACTION_LABEL, SEVERITY_LABEL
from axis.db.schema import ReportRow


class FeishuError(RuntimeError):
    pass


def sign(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}".encode()
    digest = hmac.new(string_to_sign, b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def build_card(report: ReportRow, max_items: int = 8) -> dict:
    out = report.content
    findings = out.get("findings", [])
    recs = out.get("recommendations", [])
    has_critical = any(f["severity"] == "critical" for f in findings)

    elements: list[dict] = [{"tag": "markdown", "content": out.get("summary", "")}]
    if findings:
        lines = []
        for f in findings[:max_items]:
            where = " ".join(x for x in (f["shop_id"], f["asin"]) if x)
            lines.append(f"- {SEVERITY_LABEL.get(f['severity'], f['severity'])}｜{f['title']}"
                         + (f"（{where}）" if where else ""))
        if len(findings) > max_items:
            lines.append(f"- ……共 {len(findings)} 项")
        elements += [{"tag": "hr"}, {"tag": "markdown", "content": "**重点发现**\n" + "\n".join(lines)}]
    if recs:
        by_type: dict[str, int] = {}
        for r in recs:
            by_type[r["action_type"]] = by_type.get(r["action_type"], 0) + 1
        summary = "、".join(f"{ACTION_LABEL.get(k, k)} {v}" for k, v in by_type.items())
        elements += [{"tag": "hr"}, {"tag": "markdown", "content": f"**待审批建议 {len(recs)} 条**：{summary}"}]
    questions = out.get("questions_for_owner", [])
    if questions:
        elements += [{"tag": "hr"}, {"tag": "markdown",
                                     "content": "**需要你确认**\n" + "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))}]
    elements.append({"tag": "note", "elements": [
        {"tag": "plain_text", "content": f"报告 #{report.id}｜{report.model}｜全文：{report.markdown_path}"}
    ]})

    title = {"daily_review": "每日复盘", "diagnosis": "产品诊断"}.get(report.kind, report.kind)
    if report.asin:
        title += f" {report.shop_id}/{report.asin}"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"Axis {title} {report.report_date.isoformat()}"},
            "template": "red" if has_critical else "blue",
        },
        "elements": elements,
    }


def send_report(webhook: str, secret: str, report: ReportRow, client: httpx.Client | None = None) -> None:
    _post(webhook, secret, {"msg_type": "interactive", "card": build_card(report)}, client)


def send_text(webhook: str, secret: str, text: str, client: httpx.Client | None = None) -> None:
    _post(webhook, secret, {"msg_type": "text", "content": {"text": text}}, client)


def _post(webhook: str, secret: str, payload: dict, client: httpx.Client | None) -> None:
    if not webhook:
        raise FeishuError("未配置 AXIS_FEISHU_WEBHOOK")
    if secret:
        ts = int(time.time())
        payload.update(timestamp=str(ts), sign=sign(secret, ts))
    http = client or httpx.Client(timeout=15)
    try:
        resp = http.post(webhook, json=payload)
        resp.raise_for_status()
        data = resp.json()
    finally:
        if client is None:
            http.close()
    code = data.get("code", data.get("StatusCode", 0))
    if code != 0:
        raise FeishuError(f"飞书返回错误: {data}")
