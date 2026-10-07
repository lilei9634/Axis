"""赛狐网页接口的 HTTP 客户端。

- 复用 `axis login` 保存的登录态（请求头 + cookie）；
- 只能调用 endpoints.py 里登记过、并且标记为只读的接口；
- 请求之间强制间隔并加随机抖动，失败自动重试；登录失效时提示重新登录。
"""

import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from axis.erp.base import RawSink
from axis.erp.sellfox.endpoints import ENDPOINTS, Endpoint, RequestContext

log = logging.getLogger(__name__)

_SUCCESS_CODES = {0, 200, "0", "200", "success", "SUCCESS"}


class SellfoxError(RuntimeError):
    pass


class SessionExpired(SellfoxError):
    pass


class EndpointNotConfigured(SellfoxError):
    pass


class ReadOnlyViolation(SellfoxError):
    pass


@dataclass
class AuthState:
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "AuthState":
        if not path.exists():
            raise SessionExpired(f"找不到登录凭证 {path}，请先在本机运行 `axis login`")
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(headers=data.get("headers", {}), cookies=data.get("cookies", {}))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"headers": self.headers, "cookies": self.cookies}, ensure_ascii=False, indent=2))
        path.chmod(0o600)


def default_ok(payload: Any) -> bool:
    if isinstance(payload, dict):
        for key in ("code", "status", "errCode"):
            if key in payload:
                return payload[key] in _SUCCESS_CODES
        if payload.get("success") is False:
            return False
    return True


def _looks_logged_out(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    text = json.dumps(payload, ensure_ascii=False)[:500].lower()
    return any(k in text for k in ("未登录", "登录过期", "登录失效", "token失效", "token expired", "unauthorized", "请重新登录"))


class SellfoxClient:
    def __init__(
        self,
        base_url: str,
        auth: AuthState,
        endpoints: dict[str, Endpoint] | None = None,
        min_interval: float = 1.5,
        jitter: float = 1.0,
        max_retries: int = 3,
        raw_sink: RawSink | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ):
        if not base_url:
            raise SellfoxError("未配置 AXIS_SELLFOX_BASE_URL（赛狐网页调用的接口域名，从抓包结果里确认）")
        self.endpoints = ENDPOINTS if endpoints is None else endpoints
        self.min_interval = min_interval
        self.jitter = jitter
        self.max_retries = max_retries
        self.raw_sink = raw_sink
        self._sleep = sleep
        self._last_call = 0.0
        self.http = httpx.Client(
            base_url=base_url,
            headers=auth.headers,
            cookies=auth.cookies,
            timeout=30,
            transport=transport,
        )

    def endpoint(self, name: str) -> Endpoint:
        ep = self.endpoints.get(name)
        if ep is None:
            raise EndpointNotConfigured(
                f"赛狐接口「{name}」尚未配置。请在赛狐对应页面抓包（导出 HAR），"
                "用 `axis har sanitize` 脱敏后提供，再在 axis/erp/sellfox/endpoints.py 中登记"
            )
        if not ep.readonly:
            raise ReadOnlyViolation(f"接口「{name}」不是只读接口，MVP 阶段禁止调用")
        return ep

    def call(self, name: str, ctx: RequestContext) -> Any:
        ep = self.endpoint(name)
        params, body = ep.build(ctx)
        payload = self._request(ep, params, body)
        if self.raw_sink:
            self.raw_sink("sellfox", name, {"params": params, "body": body}, payload)
        return payload

    def fetch_all(self, name: str, ctx: RequestContext, max_pages: int = 200) -> list[Any]:
        """自动翻页，返回所有记录解析后的统一模型。"""
        ep = self.endpoint(name)
        out: list[Any] = []
        page = 1
        while page <= max_pages:
            ctx.page = page
            payload = self.call(name, ctx)
            items = ep.items(payload) or []
            for item in items:
                parsed = ep.parse(item, ctx)
                if parsed is None:
                    continue
                out.extend(parsed if isinstance(parsed, list) else [parsed])
            if not ep.paginated or not items:
                break
            if ep.total is not None:
                if page * ctx.page_size >= ep.total(payload):
                    break
            elif len(items) < ctx.page_size:
                break
            page += 1
        else:
            log.warning("接口 %s 翻页超过 %d 页，已截断", name, max_pages)
        return out

    def _throttle(self) -> None:
        wait = self._last_call + self.min_interval + random.uniform(0, self.jitter) - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        self._last_call = time.monotonic()

    def _request(self, ep: Endpoint, params: dict, body: dict | None) -> Any:
        method = ep.method.upper()
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                resp = self.http.request(method, ep.path, params=params or None, json=body)
            except httpx.TransportError as e:
                if attempt == self.max_retries:
                    raise SellfoxError(f"请求 {ep.name} 网络错误: {e}") from e
                self._sleep(2**attempt)
                continue
            if resp.status_code in (401, 403):
                raise SessionExpired("赛狐登录已失效，请在本机重新运行 `axis login`")
            if resp.status_code >= 500 or resp.status_code == 429:
                if attempt == self.max_retries:
                    raise SellfoxError(f"请求 {ep.name} 失败: HTTP {resp.status_code}")
                self._sleep(2**attempt * 2)
                continue
            if resp.status_code >= 400:
                raise SellfoxError(f"请求 {ep.name} 失败: HTTP {resp.status_code} {resp.text[:200]}")
            try:
                payload = resp.json()
            except ValueError as e:
                # 登录失效时很多系统会返回登录页 HTML
                raise SessionExpired(f"接口 {ep.name} 返回的不是 JSON，可能登录已失效，请重新运行 `axis login`") from e
            if not (ep.ok or default_ok)(payload):
                if _looks_logged_out(payload):
                    raise SessionExpired("赛狐登录已失效，请在本机重新运行 `axis login`")
                raise SellfoxError(f"接口 {ep.name} 返回业务错误: {json.dumps(payload, ensure_ascii=False)[:300]}")
            return payload
        raise SellfoxError(f"请求 {ep.name} 失败")

    def close(self) -> None:
        self.http.close()
