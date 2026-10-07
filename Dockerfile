# 服务器部署：登录在本机完成（axis login），把 .axis/sellfox_auth.json 挂载进容器即可
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy TZ=Asia/Shanghai
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY axis ./axis
COPY skills ./skills
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
VOLUME ["/app/data", "/app/reports", "/app/.axis"]
CMD ["axis", "daemon"]
