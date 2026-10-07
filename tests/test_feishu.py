import base64
import hashlib
import hmac
import json
from datetime import date

import httpx
import pytest

from axis.db.schema import ReportRow
from axis.notify.feishu import FeishuError, build_card, send_report, sign
from tests.conftest import SAMPLE_OUTPUT


def _report():
    return ReportRow(id=7, kind="daily_review", report_date=date(2026, 10, 6), model="claude-opus-5-5",
                     summary=SAMPLE_OUTPUT["summary"], content=SAMPLE_OUTPUT, markdown_path="reports/x.md",
                     shop_id="", asin="")


def test_sign_matches_feishu_algorithm():
    expected = base64.b64encode(hmac.new(b"1700000000\nsecret", b"", hashlib.sha256).digest()).decode()
    assert sign("secret", 1700000000) == expected


def test_card_content():
    card = build_card(_report())
    assert card["header"]["template"] == "red"  # 有严重问题
    text = json.dumps(card, ensure_ascii=False)
    assert "销量骤降" in text and "否定搜索词 1" in text and "采购成本" in text


def test_send_report_signed_and_checks_code():
    sent = []

    def handler(req):
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={"code": 0, "msg": "success"})

    send_report("https://open.feishu.cn/hook/x", "secret", _report(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert sent[0]["msg_type"] == "interactive" and sent[0]["sign"] and sent[0]["timestamp"]

    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"code": 19021, "msg": "sign match fail"})))
    with pytest.raises(FeishuError):
        send_report("https://open.feishu.cn/hook/x", "secret", _report(), client=bad)
