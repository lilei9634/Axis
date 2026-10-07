"""指标计算。所有数字都由代码算出来，AI 只负责解读，不负责算账。"""

from dataclasses import asdict, dataclass, field


def safe_div(a: float, b: float) -> float | None:
    return a / b if b else None


def pct_change(cur: float, prev: float) -> float | None:
    """环比变化。前值为 0 时没有意义，返回 None。"""
    return (cur - prev) / prev if prev else None


@dataclass
class AdTotals:
    impressions: int = 0
    clicks: int = 0
    spend: float = 0.0
    orders: int = 0
    sales: float = 0.0

    def add(self, row) -> "AdTotals":
        self.impressions += row.impressions
        self.clicks += row.clicks
        self.spend += row.spend
        self.orders += row.orders
        self.sales += row.sales
        return self

    @property
    def acos(self) -> float | None:
        return safe_div(self.spend, self.sales)

    @property
    def roas(self) -> float | None:
        return safe_div(self.sales, self.spend)

    @property
    def ctr(self) -> float | None:
        return safe_div(self.clicks, self.impressions)

    @property
    def cvr(self) -> float | None:
        return safe_div(self.orders, self.clicks)

    @property
    def cpc(self) -> float | None:
        return safe_div(self.spend, self.clicks)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(acos=self.acos, roas=self.roas, ctr=self.ctr, cvr=self.cvr, cpc=self.cpc)
        return _round(d)


@dataclass
class SalesTotals:
    units: int = 0
    orders: int = 0
    revenue: float = 0.0
    sessions: int = 0
    refunds: int = 0

    def add(self, row) -> "SalesTotals":
        self.units += row.units
        self.orders += row.orders
        self.revenue += row.revenue
        self.sessions += row.sessions
        self.refunds += row.refunds
        return self

    @property
    def unit_session_pct(self) -> float | None:
        """转化率（Unit Session Percentage）"""
        return safe_div(self.units, self.sessions)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["unit_session_pct"] = self.unit_session_pct
        return _round(d)


@dataclass
class PeriodSummary:
    """某个对象（店铺 / 产品）在一个时间窗口内的销售 + 广告汇总。"""

    sales: SalesTotals = field(default_factory=SalesTotals)
    ads: AdTotals = field(default_factory=AdTotals)

    @property
    def tacos(self) -> float | None:
        return safe_div(self.ads.spend, self.sales.revenue)

    @property
    def ad_sales_share(self) -> float | None:
        """广告销售额占比，过高说明自然流量弱"""
        return safe_div(self.ads.sales, self.sales.revenue)

    def to_dict(self) -> dict:
        return {
            "sales": self.sales.to_dict(),
            "ads": self.ads.to_dict(),
            "tacos": _r(self.tacos),
            "ad_sales_share": _r(self.ad_sales_share),
        }


def compare(cur: PeriodSummary, prev: PeriodSummary) -> dict:
    """两个时间窗口对比，返回关键指标的变化率。"""
    return _round(
        {
            "units": pct_change(cur.sales.units, prev.sales.units),
            "revenue": pct_change(cur.sales.revenue, prev.sales.revenue),
            "sessions": pct_change(cur.sales.sessions, prev.sales.sessions),
            "ad_spend": pct_change(cur.ads.spend, prev.ads.spend),
            "ad_sales": pct_change(cur.ads.sales, prev.ads.sales),
            "acos_delta": _delta(cur.ads.acos, prev.ads.acos),
            "tacos_delta": _delta(cur.tacos, prev.tacos),
        }
    )


def _delta(a, b):
    return a - b if a is not None and b is not None else None


def _r(v):
    return round(v, 4) if isinstance(v, float) else v


def _round(d: dict) -> dict:
    return {k: _r(v) for k, v in d.items()}
