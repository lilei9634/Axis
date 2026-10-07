"""HAR 抓包脱敏：去掉登录凭证和个人信息，只保留分析接口所需的请求结构和响应数据。

在你自己的电脑上运行，脱敏后的文件才可以发给别人或放进测试用例。
"""

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MASK = "***"
SENSITIVE_HEADER = re.compile(r"cookie|authorization|token|auth|session|sign|secret|ticket|jwt|csrf|x-api-key", re.I)
SENSITIVE_KEY = re.compile(
    r"token|password|passwd|pwd|secret|sign|cookie|session|phone|mobile|tel|email|mail|"
    r"idcard|bank|account_?no|address|receiver|buyer_?name|contact",
    re.I,
)
PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
JWT = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")
# 只保留这些类型的响应内容，其余（图片、脚本、样式）直接丢弃以减小文件
KEEP_MIME = ("json", "text/plain", "javascript+json")


def sanitize_value(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: (MASK if SENSITIVE_KEY.search(str(k)) and not isinstance(v, (dict, list)) else sanitize_value(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_value(v) for v in obj]
    if isinstance(obj, str):
        return EMAIL.sub(MASK, PHONE.sub(MASK, JWT.sub(MASK, obj)))
    return obj


def _sanitize_text(text: str) -> str:
    try:
        return json.dumps(sanitize_value(json.loads(text)), ensure_ascii=False)
    except (ValueError, TypeError):
        return sanitize_value(text)


def _sanitize_url(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, MASK if SENSITIVE_KEY.search(k) else sanitize_value(v)) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit(parts._replace(query=urlencode(query, safe="*")))


def _headers(headers: list[dict]) -> list[dict]:
    return [h | {"value": MASK} if SENSITIVE_HEADER.search(h.get("name", "")) else h for h in headers]


def sanitize_har(har: dict) -> dict:
    entries = []
    for e in har.get("log", {}).get("entries", []):
        req, resp = dict(e.get("request", {})), dict(e.get("response", {}))
        req["url"] = _sanitize_url(req.get("url", ""))
        req["headers"] = _headers(req.get("headers", []))
        req["cookies"] = []
        req["queryString"] = [
            q | {"value": MASK} if SENSITIVE_KEY.search(q.get("name", "")) else q | {"value": sanitize_value(q.get("value", ""))}
            for q in req.get("queryString", [])
        ]
        if req.get("postData"):
            pd = dict(req["postData"])
            if "text" in pd:
                pd["text"] = _sanitize_text(pd["text"])
            pd.pop("params", None)
            req["postData"] = pd
        resp["headers"] = _headers(resp.get("headers", []))
        resp["cookies"] = []
        content = dict(resp.get("content", {}))
        if any(m in content.get("mimeType", "") for m in KEEP_MIME) and content.get("encoding") != "base64":
            content["text"] = _sanitize_text(content.get("text", ""))
        else:
            content.pop("text", None)
        resp["content"] = content
        entries.append({k: v for k, v in e.items() if k not in ("request", "response")} | {"request": req, "response": resp})
    return {"log": {k: v for k, v in har.get("log", {}).items() if k != "entries"} | {"entries": entries}}


def sanitize_file(src: Path, dst: Path) -> int:
    har = json.loads(src.read_text(encoding="utf-8"))
    out = sanitize_har(har)
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(out["log"]["entries"])
