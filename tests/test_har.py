import json

from axis.har.inspect import inspect_har, to_markdown
from axis.har.sanitize import MASK, sanitize_har

HAR = {
    "log": {
        "version": "1.2",
        "entries": [
            {
                "request": {
                    "method": "POST",
                    "url": "https://api.example.com/api/ad/campaign/list?token=abc&shopId=1",
                    "headers": [{"name": "Authorization", "value": "Bearer secret"}, {"name": "Accept", "value": "json"}],
                    "cookies": [{"name": "sid", "value": "xyz"}],
                    "queryString": [{"name": "token", "value": "abc"}, {"name": "shopId", "value": "1"}],
                    "postData": {"mimeType": "application/json", "text": json.dumps({"pageNo": 1, "phone": "13800138000"})},
                },
                "response": {
                    "status": 200,
                    "headers": [{"name": "Set-Cookie", "value": "sid=xyz"}],
                    "cookies": [{"name": "sid", "value": "xyz"}],
                    "content": {"mimeType": "application/json", "text": json.dumps({
                        "code": 0,
                        "data": {"rows": [{"campaignName": "A", "contact": "张三", "note": "call 13900139000 or a@b.com"}],
                                 "total": 1},
                    })},
                },
            },
            {
                "request": {"method": "GET", "url": "https://cdn.example.com/logo.png", "headers": [], "queryString": []},
                "response": {"status": 200, "headers": [], "content": {"mimeType": "image/png", "text": "iVBOR..."}},
            },
        ],
    }
}


def test_sanitize_removes_credentials_and_pii():
    out = sanitize_har(HAR)
    text = json.dumps(out, ensure_ascii=False)
    for secret in ("Bearer secret", "xyz", "abc", "13800138000", "13900139000", "a@b.com", "张三"):
        assert secret not in text, secret
    e = out["log"]["entries"][0]
    assert e["request"]["cookies"] == [] and e["response"]["cookies"] == []
    assert {"name": "Accept", "value": "json"} in e["request"]["headers"]
    # 业务数据保留下来，供分析接口结构
    assert "campaignName" in e["response"]["content"]["text"]
    assert MASK in e["request"]["url"]
    # 非 JSON 响应内容被丢弃
    assert "text" not in out["log"]["entries"][1]["response"]["content"]


def test_inspect_lists_json_apis():
    apis = inspect_har(sanitize_har(HAR))
    assert len(apis) == 1
    a = apis[0]
    assert a["path"] == "/api/ad/campaign/list" and a["method"] == "POST"
    assert "广告" in a["category"]
    assert a["body_sample"]["pageNo"] == 1
    assert a["response_structure"]["data"]["rows"][0] == "list[1]"
    assert "/api/ad/campaign/list" in to_markdown(apis)
