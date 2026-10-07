# Axis

AI 驱动的跨境电商"一人公司"运营系统。当前是 MVP 阶段：**通过赛狐 ERP 获取 5 家亚马逊店铺的数据，做产品诊断和每日复盘，输出调整建议，推送到飞书。只读，不执行任何操作。**

## 架构

```
 赛狐 ERP（网页接口）
        │  axis/erp/sellfox   只读白名单 · 限速 · 登录态复用
        ▼
 ERP 适配层 axis/erp/base.py   ← 以后换成赛狐官方 API / 亚马逊 SP-API 只需新写一个适配器
        │
        ▼
 数据同步 axis/etl            幂等写入，每次回补最近 14 天（广告归因数据会延迟更新）
        │
        ▼
 数据库 axis/db               唯一的真实来源：店铺、产品、销量、广告（活动/产品/投放词/搜索词）、报告、建议
        │
        ▼
 指标与规则 axis/metrics       ACOS / TACoS / CVR 等全部由代码计算；规则引擎标记异常和机会
        │
        ▼
 AI 分析 axis/agents           Claude 读取"事实包"和 SOP（skills/），按需调用查询工具钻取明细，
        │                     输出结构化的发现 + 建议 + 需要你确认的问题
        ▼
 报告与审批                    Markdown 报告 · 飞书卡片 · 建议入库（status=proposed，等待审批）
```

设计原则：

- **流程是确定性代码，AI 只负责判断。** 数字由代码算好，AI 不做算账。
- **业务经验写在 `skills/` 的 SOP 里。** 觉得 AI 建议不对时，直接改 SOP，不用改代码。
- **AI 不可用时自动降级。** 没有 API 密钥、调用失败时，用规则引擎生成报告，保证每天都有复盘。

## 快速开始（用假数据体验）

```bash
uv sync
uv run axis run-daily --adapter fake --no-llm --no-notify   # 不调用 AI，只用规则引擎
cat reports/*_daily_review.md
```

配置好 `ANTHROPIC_API_KEY` 后去掉 `--no-llm`，就会由 Claude 做综合分析：

```bash
cp .env.example .env   # 填入 ANTHROPIC_API_KEY，按需修改其他配置
uv run axis run-daily --adapter fake --no-notify
uv run axis diagnose --shop S1 --asin B0S1CTEST0
```

假数据里埋了几类问题（销量骤降、库存不足、只花钱不出单的搜索词、可收割的高转化搜索词、预算受限的广告活动），可以用来检验报告质量。

## 接入赛狐

赛狐的网页接口需要先抓包确认，才能在 `axis/erp/sellfox/endpoints.py` 里登记。在登记之前，`--adapter sellfox` 会提示具体缺哪个接口。

### 1. 抓包（在你自己的电脑上操作）

1. 用 Chrome 登录赛狐，按 F12 打开开发者工具，切到 **Network** 面板，勾选 **Preserve log**，筛选 **Fetch/XHR**。
2. 依次打开下面这些页面。每个页面都切换一次店铺、翻一次页、改一次日期范围，让请求参数完整出现：
   - 店铺列表（或店铺切换下拉框）
   - 产品列表 / Listing 列表，以及任意一个产品的详情
   - 销量统计（按 ASIN、按天）
   - 广告活动列表
   - 广告报表：广告活动、广告产品、投放（关键词/定位）、搜索词，最好能选"按天"
   - 库存 / FBA 库存
3. 在 Network 面板里右键 → **Save all as HAR with content**，保存为 `sellfox.har`。
4. 脱敏，并生成接口清单：

```bash
uv run axis har sanitize sellfox.har sellfox.sanitized.har
uv run axis har inspect sellfox.sanitized.har --out sellfox-apis.md
```

`sanitize` 会去掉 cookie、token、手机号、邮箱、收件人等信息，只保留请求结构和业务数据。**只发送脱敏后的文件，原始 HAR 里有你的登录凭证。** 发出去之前请自己打开检查一遍。

### 2. 登记接口

根据 `sellfox-apis.md` 在 `axis/erp/sellfox/endpoints.py` 里登记接口：路径、参数、分页方式，以及响应到统一模型的解析函数。每个接口都用脱敏样例在 `tests/` 里补解析测试。
所有接口默认 `readonly=True`；客户端只允许调用已登记的只读接口，在代码层面保证 MVP 不会改动任何数据。

### 3. 登录并同步

```bash
uv sync --extra browser && uv run playwright install chromium
uv run axis login            # 打开浏览器手动登录（含短信验证），登录态保存到 .axis/sellfox_auth.json
uv run axis sync --days 3    # 先同步 3 天，和赛狐页面核对几个数字
uv run axis run-daily        # 完整流程：同步 → 复盘 → 推送飞书
```

`.axis/sellfox_auth.json` 等同于你的赛狐登录凭证，已加入 `.gitignore`，不要外传。登录失效时，系统会提示重新运行 `axis login`。如果是定时任务失败，还会推送飞书告警。

**风控提示：** 请求之间默认间隔 1.5–2.5 秒（`AXIS_SELLFOX_MIN_INTERVAL` / `AXIS_SELLFOX_JITTER`），每天只同步一次。逆向网页接口不在赛狐官方支持范围内，接口改版后需要重新抓包调整。

## 飞书推送

在飞书群里添加"自定义机器人"，开启签名校验，把 webhook 和密钥填到 `.env` 的 `AXIS_FEISHU_WEBHOOK` / `AXIS_FEISHU_SECRET`。每日复盘会推送一张摘要卡片，内容包括总结、重点发现、待审批建议数量和需要你确认的问题；报告全文保存在 `reports/`。

## 常用命令

| 命令 | 作用 |
|---|---|
| `axis sync [--days N] [--adapter fake]` | 同步数据到本地数据库 |
| `axis review [--date YYYY-MM-DD] [--no-llm] [--no-notify]` | 基于已有数据生成每日复盘 |
| `axis diagnose --shop S --asin A` | 单品诊断，输出优化方案 |
| `axis run-daily` | 同步 + 复盘 + 推送 |
| `axis daemon` | 常驻运行，每天 `AXIS_DAILY_RUN_TIME` 执行一次 run-daily |
| `axis recs list` / `axis recs decide 1 2 --approve` | 查看 / 审批建议（MVP 只记录，不执行） |
| `axis har sanitize` / `axis har inspect` | 抓包脱敏 / 接口分析 |

## 部署到服务器

```bash
docker build -t axis .
docker run -d --name axis --env-file .env \
  -v $PWD/data:/app/data -v $PWD/reports:/app/reports -v $PWD/.axis:/app/.axis axis
```

登录在本机完成，把 `.axis/sellfox_auth.json` 放到服务器的挂载目录。如果服务器 IP 和登录时不同，赛狐可能要求重新验证，届时需要在本机重新登录后再更新这个文件。

## 开发

```bash
uv run pytest
```

## 路线图

- [x] MVP：只读数据接入、指标与规则、AI 每日复盘与单品诊断、飞书推送
- [ ] 赛狐接口登记（等抓包）
- [ ] 审批后执行：在飞书卡片上审批，系统通过赛狐执行广告调整。执行分级授权：小幅调整自动执行，超出阈值需要审批，并设置花费熔断
- [ ] 复盘闭环：跟踪已执行建议的效果，写入下一次复盘
- [ ] 选品与新品方案：市场调研、可行性评估、文案和图片/视频生成、广告投放方案
- [ ] 供应链：库存预测、补货计划、采购与物流
