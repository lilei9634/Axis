import json
from datetime import date

import httpx
import pytest

from axis.erp.sellfox import endpoints as E
from axis.erp.sellfox.adapter import SellfoxAdapter
from axis.erp.sellfox.client import (
    AuthState,
    EndpointNotConfigured,
    ReadOnlyViolation,
    SellfoxClient,
    SellfoxError,
    SessionExpired,
)
from axis.models import AdLevel, AdMetricRow, Product

PRODUCTS_EP = E.Endpoint(
    name=E.PRODUCTS,
    method="POST",
    path="/api/product/list",
    build=lambda ctx: ({}, {"shopId": ctx.shop_id, "pageNo": ctx.page, "pageSize": ctx.page_size}),
    items=lambda p: E.dig(p, "data.rows", []),
    total=lambda p: E.dig(p, "data.total", 0),
    parse=lambda r, ctx: Product(shop_id=ctx.shop_id, asin=r["asin"], price=E.num(r["price"])),
)


def make_client(handler, endpoints, **kw):
    return SellfoxClient(
        "https://api.example.com", AuthState(headers={"Authorization": "Bearer t"}), endpoints=endpoints,
        min_interval=0, jitter=0, transport=httpx.MockTransport(handler), sleep=lambda _: None, **kw,
    )


def test_pagination_auth_and_parsing():
    rows = [{"asin": f"B0{i:08d}", "price": "$1,234.50"} for i in range(5)]
    seen = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        seen.append((req.headers["authorization"], body["pageNo"]))
        page = rows[(body["pageNo"] - 1) * 2 : body["pageNo"] * 2]
        return httpx.Response(200, json={"code": 0, "data": {"rows": page, "total": len(rows)}})

    raw = []
    client = make_client(handler, {E.PRODUCTS: PRODUCTS_EP}, raw_sink=lambda *a: raw.append(a))
    adapter = SellfoxAdapter(client, page_size=2)
    products = adapter.list_products("S1")
    assert [p.asin for p in products] == [r["asin"] for r in rows]
    assert products[0].price == 1234.5
    assert seen == [("Bearer t", 1), ("Bearer t", 2), ("Bearer t", 3)]
    assert len(raw) == 3 and raw[0][1] == E.PRODUCTS


def test_unconfigured_endpoint_explains_next_step():
    client = make_client(lambda r: httpx.Response(200, json={}), {})
    with pytest.raises(EndpointNotConfigured, match="HAR"):
        SellfoxAdapter(client).list_shops()


def test_write_endpoint_is_blocked_before_any_request():
    called = []
    write_ep = E.Endpoint(name="update_bid", method="POST", path="/api/ad/bid/update", readonly=False,
                          build=lambda ctx: ({}, {}), items=lambda p: [], parse=lambda r, c: None)
    client = make_client(lambda r: called.append(r) or httpx.Response(200, json={}), {"update_bid": write_ep})
    with pytest.raises(ReadOnlyViolation):
        client.call("update_bid", E.RequestContext())
    assert called == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401),
        httpx.Response(200, text="<html>login</html>"),
        httpx.Response(200, json={"code": 401, "msg": "登录已过期，请重新登录"}),
    ],
)
def test_session_expired(response):
    client = make_client(lambda r: response, {E.PRODUCTS: PRODUCTS_EP})
    with pytest.raises(SessionExpired):
        client.call(E.PRODUCTS, E.RequestContext(shop_id="S1"))


def test_business_error_and_retry():
    attempts = []

    def handler(req):
        attempts.append(1)
        return httpx.Response(502) if len(attempts) < 3 else httpx.Response(200, json={"code": 500, "msg": "参数错误"})

    client = make_client(handler, {E.PRODUCTS: PRODUCTS_EP})
    with pytest.raises(SellfoxError, match="业务错误"):
        client.call(E.PRODUCTS, E.RequestContext(shop_id="S1"))
    assert len(attempts) == 3


def test_per_day_endpoint_fetches_each_day():
    days = []
    ep = E.Endpoint(
        name=E.AD_METRICS_CAMPAIGN, method="GET", path="/api/ad/campaign/report", per_day=True, paginated=False,
        build=lambda ctx: ({"date": ctx.start.isoformat()}, None),
        items=lambda p: p["data"],
        parse=lambda r, ctx: AdMetricRow(shop_id=ctx.shop_id, date=ctx.start, level=AdLevel.CAMPAIGN,
                                         campaign_id=r["id"], spend=E.num(r["cost"])),
    )

    def handler(req):
        days.append(req.url.params["date"])
        return httpx.Response(200, json={"code": 0, "data": [{"id": "C1", "cost": "1.5"}]})

    rows = SellfoxAdapter(make_client(handler, {ep.name: ep})).get_ad_metrics(
        "S1", AdLevel.CAMPAIGN, date(2026, 10, 1), date(2026, 10, 3))
    assert days == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert [r.date.day for r in rows] == [1, 2, 3]


def test_auth_state_roundtrip(tmp_path):
    path = tmp_path / "auth.json"
    AuthState(headers={"token": "x"}, cookies={"sid": "y"}).save(path)
    loaded = AuthState.load(path)
    assert loaded.headers == {"token": "x"} and loaded.cookies == {"sid": "y"}
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    with pytest.raises(SessionExpired):
        AuthState.load(tmp_path / "missing.json")
