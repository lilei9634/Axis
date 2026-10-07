"""数据库表结构。数据库是整个系统的唯一真实来源：AI 每次分析都从这里读取历史数据和过往决策。

默认用 SQLite；迁移到服务器时把 AXIS_DB_URL 改成 Postgres 即可。
注意：参与唯一约束的列一律用空字符串代替 NULL，否则 SQLite 会把多个 NULL 当成不同的值，导致重复写入。
"""

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ShopRow(Base):
    __tablename__ = "shops"
    shop_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    marketplace: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProductRow(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("shop_id", "asin"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[str] = mapped_column(String(64), index=True)
    asin: Mapped[str] = mapped_column(String(20))
    sku: Mapped[str] = mapped_column(String(128), default="")
    title: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fba_stock: Mapped[int] = mapped_column(Integer, default=0)
    inbound_stock: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="")
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    listing: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SalesDailyRow(Base):
    __tablename__ = "sales_daily"
    __table_args__ = (UniqueConstraint("shop_id", "asin", "date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[str] = mapped_column(String(64), index=True)
    asin: Mapped[str] = mapped_column(String(20))
    date: Mapped[date] = mapped_column(Date, index=True)
    units: Mapped[int] = mapped_column(Integer, default=0)
    orders: Mapped[int] = mapped_column(Integer, default=0)
    revenue: Mapped[float] = mapped_column(Float, default=0.0)
    sessions: Mapped[int] = mapped_column(Integer, default=0)
    page_views: Mapped[int] = mapped_column(Integer, default=0)
    refunds: Mapped[int] = mapped_column(Integer, default=0)


class AdCampaignRow(Base):
    __tablename__ = "ad_campaigns"
    __table_args__ = (UniqueConstraint("shop_id", "campaign_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[str] = mapped_column(String(64), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(Text)
    ad_type: Mapped[str] = mapped_column(String(8), default="SP")
    state: Mapped[str] = mapped_column(String(16), default="enabled")
    targeting_type: Mapped[str] = mapped_column(String(16), default="")
    daily_budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AdMetricDailyRow(Base):
    __tablename__ = "ad_metrics_daily"
    __table_args__ = (
        UniqueConstraint("shop_id", "date", "level", "campaign_id", "ad_group_id", "entity_key"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[str] = mapped_column(String(64), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    level: Mapped[str] = mapped_column(String(16), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64))
    ad_group_id: Mapped[str] = mapped_column(String(64), default="")
    entity_key: Mapped[str] = mapped_column(String(512), default="")
    entity_text: Mapped[str] = mapped_column(Text, default="")
    match_type: Mapped[str] = mapped_column(String(16), default="")
    asin: Mapped[str] = mapped_column(String(20), default="")
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    spend: Mapped[float] = mapped_column(Float, default=0.0)
    orders: Mapped[int] = mapped_column(Integer, default=0)
    sales: Mapped[float] = mapped_column(Float, default=0.0)
    units: Mapped[int] = mapped_column(Integer, default=0)


class RawSnapshotRow(Base):
    """ERP 原始响应存档。接口改版或数字对不上时，用来排查解析问题。"""

    __tablename__ = "raw_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sync_run_id: Mapped[int | None] = mapped_column(ForeignKey("sync_runs.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(32))
    endpoint: Mapped[str] = mapped_column(String(128))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    payload: Mapped[dict | list] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SyncRunRow(Base):
    __tablename__ = "sync_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    adapter: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running / ok / failed
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class ReportRow(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32))  # daily_review / diagnosis
    report_date: Mapped[date] = mapped_column(Date, index=True)
    shop_id: Mapped[str] = mapped_column(String(64), default="")
    asin: Mapped[str] = mapped_column(String(20), default="")
    model: Mapped[str] = mapped_column(String(64), default="")  # 生成报告的模型；离线规则模式为 "rules"
    summary: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[dict] = mapped_column(JSON, default=dict)  # agent 输出的完整结构化结果
    markdown_path: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RecommendationRow(Base):
    """调整建议。MVP 阶段只记录、不执行；后续执行模块只会执行 status=approved 的建议。"""

    __tablename__ = "recommendations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), index=True)
    shop_id: Mapped[str] = mapped_column(String(64), default="")
    asin: Mapped[str] = mapped_column(String(20), default="")
    campaign_id: Mapped[str] = mapped_column(String(64), default="")
    action_type: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(Text, default="")  # 关键词 / 搜索词 / 广告活动名等
    current_value: Mapped[str] = mapped_column(Text, default="")
    proposed_value: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    expected_impact: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[str] = mapped_column(String(8), default="medium")
    risk: Mapped[str] = mapped_column(String(8), default="low")
    status: Mapped[str] = mapped_column(String(16), default="proposed")  # proposed / approved / rejected / executed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
