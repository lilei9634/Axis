"""赛狐登录：打开一个真实浏览器让你手动登录（包括短信验证），然后保存登录态。

登录过程中会监听浏览器发往接口域名的请求，记录其中的鉴权请求头（如 Authorization / token），
连同 cookie 一起保存到 AXIS_SELLFOX_AUTH_PATH。这个文件等同于你的登录凭证，不要提交到代码仓库、不要发给别人。

需要安装浏览器依赖：uv sync --extra browser && uv run playwright install chromium
"""

from pathlib import Path
from urllib.parse import urlparse

from axis.erp.sellfox.client import AuthState

# 这些请求头不属于鉴权信息，由 httpx 自己生成
_SKIP_HEADERS = {
    "host", "content-length", "content-type", "accept-encoding", "connection", "cookie",
    "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site", "priority",
}


def login_interactive(login_url: str, api_base_url: str, auth_path: Path, profile_dir: Path) -> AuthState:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("未安装 playwright：uv sync --extra browser && uv run playwright install chromium") from e
    if not login_url or not api_base_url:
        raise RuntimeError("请先配置 AXIS_SELLFOX_LOGIN_URL 和 AXIS_SELLFOX_BASE_URL")

    api_host = urlparse(api_base_url).netloc
    captured: dict[str, str] = {}

    def on_request(req):
        if urlparse(req.url).netloc == api_host and req.resource_type in ("xhr", "fetch"):
            headers = {k: v for k, v in req.headers.items() if k.lower() not in _SKIP_HEADERS and not k.startswith(":")}
            captured.clear()
            captured.update(headers)

    with sync_playwright() as p:
        # 持久化浏览器目录：下次登录时多半不用再收短信
        ctx = p.chromium.launch_persistent_context(str(profile_dir), headless=False)
        ctx.on("request", on_request)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(login_url)
        input("请在打开的浏览器里登录赛狐，登录后随便点开一个页面（例如产品列表），然后回到这里按回车……")
        cookies = {c["name"]: c["value"] for c in ctx.cookies() if api_host.endswith(c["domain"].lstrip("."))}
        ctx.close()

    if not captured:
        raise RuntimeError(f"没有捕获到发往 {api_host} 的接口请求，请确认 AXIS_SELLFOX_BASE_URL 是否正确，并在登录后打开过业务页面")
    auth = AuthState(headers=captured, cookies=cookies)
    auth.save(auth_path)
    return auth
