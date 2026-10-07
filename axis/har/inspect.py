"""HAR 抓包分析：列出页面调用了哪些 JSON 接口、带什么参数、返回什么结构，用来登记 endpoints.py。"""

import json
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

HINTS = {
    "店铺": ("shop", "store", "seller", "account", "marketplace"),
    "产品/Listing": ("product", "listing", "asin", "sku", "goods", "item"),
    "销量/订单": ("sale", "order", "profit", "statistic", "report", "daily"),
    "广告": ("ad", "advert", "campaign", "ppc", "sp", "keyword", "target", "searchterm", "search-term", "query"),
    "库存/FBA": ("inventory", "stock", "fba", "warehouse"),
}


def structure(obj: Any, depth: int = 0, max_depth: int = 4) -> Any:
    """把 JSON 压缩成结构描述：字典保留键和值类型，列表只看第一个元素。"""
    if depth >= max_depth:
        return type(obj).__name__
    if isinstance(obj, dict):
        return {k: structure(v, depth + 1, max_depth) for k, v in list(obj.items())[:60]}
    if isinstance(obj, list):
        return [f"list[{len(obj)}]", structure(obj[0], depth + 1, max_depth)] if obj else "list[0]"
    if isinstance(obj, str):
        return f"str: {obj[:40]}"
    return obj


def _json(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def guess_category(path: str) -> str:
    low = path.lower()
    hits = [name for name, kws in HINTS.items() if any(k in low for k in kws)]
    return "、".join(hits) or "-"


def inspect_har(har: dict) -> list[dict]:
    apis: "OrderedDict[tuple[str, str, str], dict]" = OrderedDict()
    for e in har.get("log", {}).get("entries", []):
        req, resp = e.get("request", {}), e.get("response", {})
        mime = resp.get("content", {}).get("mimeType", "")
        if "json" not in mime:
            continue
        url = urlsplit(req.get("url", ""))
        key = (req.get("method", "GET"), url.netloc, url.path)
        body = _json(req.get("postData", {}).get("text"))
        payload = _json(resp.get("content", {}).get("text"))
        item = apis.setdefault(key, {
            "method": key[0], "host": key[1], "path": key[2], "category": guess_category(key[2]),
            "count": 0, "status": set(), "query_keys": set(), "body_sample": None, "response_structure": None,
        })
        item["count"] += 1
        item["status"].add(resp.get("status"))
        item["query_keys"].update(q["name"] for q in req.get("queryString", []))
        if body is not None and item["body_sample"] is None:
            item["body_sample"] = body
        if payload is not None and item["response_structure"] is None:
            item["response_structure"] = structure(payload)
    out = []
    for item in apis.values():
        item["status"] = sorted(s for s in item["status"] if s is not None)
        item["query_keys"] = sorted(item["query_keys"])
        out.append(item)
    return out


def inspect_file(path: Path) -> list[dict]:
    return inspect_har(json.loads(path.read_text(encoding="utf-8")))


def to_markdown(apis: list[dict]) -> str:
    lines = ["# HAR 接口清单", "", f"共 {len(apis)} 个 JSON 接口", ""]
    for a in apis:
        lines += [
            f"## {a['method']} {a['path']}",
            "",
            f"- 域名：`{a['host']}`　推测类别：{a['category']}　调用次数：{a['count']}　状态码：{a['status']}",
            f"- Query 参数：{', '.join(a['query_keys']) or '无'}",
        ]
        if a["body_sample"] is not None:
            lines += ["- 请求体样例：", "```json", json.dumps(a["body_sample"], ensure_ascii=False, indent=1)[:1500], "```"]
        if a["response_structure"] is not None:
            lines += ["- 响应结构：", "```json", json.dumps(a["response_structure"], ensure_ascii=False, indent=1)[:3000], "```"]
        lines.append("")
    return "\n".join(lines)
