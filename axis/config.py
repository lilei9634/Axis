"""全局配置。所有配置都可以通过环境变量（前缀 AXIS_）或项目根目录的 .env 覆盖。"""

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Thresholds(BaseModel):
    """规则引擎阈值。按自己的品类和利润结构调整。"""

    target_acos: float = 0.30  # 广告 ACOS 目标
    target_tacos: float = 0.12  # 总广告花费 / 总销售额 目标
    lookback_days: int = 14  # 关键词/搜索词判断的回看窗口
    min_clicks_no_order: int = 15  # 点击达到这个数还没出单，判定为浪费
    min_spend_no_order: float = 10.0  # 或者花费达到这个金额还没出单
    harvest_min_orders: int = 2  # 搜索词出单达到这个数，考虑收割为精准词
    budget_capped_ratio: float = 0.95  # 7 日平均花费 / 日预算 达到这个比例，判定为预算受限
    sales_drop_pct: float = -0.30  # 近 7 天销量较前 7 天下降超过这个比例
    sales_drop_min_prev_units: int = 7  # 前 7 天销量太少时不判断下滑
    stock_days_min: int = 21  # 可售天数低于这个值预警


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AXIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # 存储
    data_dir: Path = Path("data")
    db_url: str = "sqlite:///data/axis.db"
    reports_dir: Path = Path("reports")
    skills_dir: Path = Path("skills")
    timezone: str = "Asia/Shanghai"

    # 数据同步
    sync_days: int = 14  # 每次回补最近 N 天（亚马逊广告归因数据会在几天内持续更新）

    # LLM（ANTHROPIC_API_KEY 由 SDK 自己从环境变量读取）
    review_model: str = "claude-opus-5-5"
    review_effort: str = "medium"
    diagnose_model: str = "claude-opus-5-5"
    diagnose_effort: str = "high"
    llm_max_turns: int = 12  # 单次分析最多的工具调用轮数
    llm_fallbacks: bool = True  # 模型拒答时由服务端自动换模型重试

    # 飞书自定义机器人
    feishu_webhook: str = ""
    feishu_secret: str = ""

    # 赛狐 ERP（网页逆向）
    sellfox_base_url: str = ""  # 赛狐网页调用的接口域名，从抓包结果里确认
    sellfox_login_url: str = ""  # 赛狐登录页地址
    sellfox_auth_path: Path = Path(".axis/sellfox_auth.json")
    sellfox_min_interval: float = 1.5  # 两次请求的最小间隔（秒）
    sellfox_jitter: float = 1.0  # 额外随机间隔上限（秒），降低被风控的概率
    sellfox_page_size: int = 100

    # 定时任务
    daily_run_time: str = "09:05"  # 每天几点跑 run-daily（按 timezone）

    thresholds: Thresholds = Thresholds()


@lru_cache
def get_settings() -> Settings:
    return Settings()
