from axis.metrics.compute import AdTotals, PeriodSummary, SalesTotals, compare, pct_change, safe_div


def test_ratios_and_zero_division():
    t = AdTotals(impressions=1000, clicks=50, spend=25.0, orders=5, sales=100.0)
    assert t.acos == 0.25
    assert t.roas == 4.0
    assert t.ctr == 0.05
    assert t.cvr == 0.1
    assert t.cpc == 0.5
    empty = AdTotals()
    assert empty.acos is None and empty.cvr is None
    assert safe_div(1, 0) is None
    assert pct_change(5, 0) is None
    assert pct_change(5, 10) == -0.5


def test_tacos_and_compare():
    cur = PeriodSummary(SalesTotals(units=10, revenue=200.0, sessions=100), AdTotals(spend=20.0, sales=80.0))
    prev = PeriodSummary(SalesTotals(units=20, revenue=400.0, sessions=100), AdTotals(spend=20.0, sales=100.0))
    assert cur.tacos == 0.1
    assert cur.ad_sales_share == 0.4
    c = compare(cur, prev)
    assert c["units"] == -0.5
    assert c["ad_spend"] == 0.0
    assert c["tacos_delta"] == 0.05
