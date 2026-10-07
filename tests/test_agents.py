import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from axis.agents.daily_review import run_daily_review
from axis.agents.diagnose import run_diagnosis
from axis.agents.llm import LLM, AgentError, Tool
from axis.agents.schemas import REVIEW_OUTPUT
from axis.db.schema import RecommendationRow
from tests.conftest import ANCHOR, SAMPLE_OUTPUT, FakeClient, final_response, tool_use_response, usage


def test_llm_tool_loop_and_request_shape():
    seen = []
    tool = Tool("echo", "回显", {"type": "object", "properties": {"x": {"type": "string"}},
                                 "required": ["x"], "additionalProperties": False}, lambda i: seen.append(i) or {"ok": i["x"]})
    client = FakeClient([tool_use_response("echo", {"x": "hi"}), final_response({"a": 1})])
    result = LLM(client=client).run(model="claude-opus-5-5", effort="medium", system="sys", prompt="p",
                                    output_schema={"type": "object"}, tools=[tool])
    assert result.output == {"a": 1}
    assert result.turns == 2
    assert seen == [{"x": "hi"}]

    calls = client.beta.messages.calls
    first, second = calls
    assert first["fallbacks"] == "default"
    assert first["betas"] == ["server-side-fallback-2026-07-01"]
    assert first["output_config"]["effort"] == "medium"
    assert first["output_config"]["format"]["type"] == "json_schema"
    assert first["tools"][0]["strict"] is True
    assert "thinking" not in first and "tool_choice" not in first
    # 第二轮：assistant 原样追加，工具结果放在一条 user 消息里
    assert second["messages"][1]["role"] == "assistant"
    results = second["messages"][2]["content"]
    assert results[0]["tool_use_id"] == "toolu_1" and results[0]["is_error"] is False


def test_llm_tool_error_is_reported_not_raised():
    def boom(_):
        raise ValueError("bad")

    tool = Tool("boom", "", {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, boom)
    client = FakeClient([tool_use_response("boom", {}), final_response({"a": 1})])
    LLM(client=client).run(model="m", effort="low", system="", prompt="p", output_schema={}, tools=[tool])
    result_block = client.beta.messages.calls[1]["messages"][2]["content"][0]
    assert result_block["is_error"] is True and "bad" in result_block["content"]


def test_llm_refusal_raises():
    refusal = SimpleNamespace(stop_reason="refusal", usage=usage(), content=[],
                              stop_details=SimpleNamespace(category="cyber"))
    with pytest.raises(AgentError, match="cyber"):
        LLM(client=FakeClient([refusal])).run(model="m", effort="low", system="", prompt="p", output_schema={})


def test_llm_max_turns():
    responses = [tool_use_response("t", {}, id_=f"toolu_{i}") for i in range(3)]
    tool = Tool("t", "", {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, lambda _: 1)
    with pytest.raises(AgentError, match="最大轮数"):
        LLM(client=FakeClient(responses), max_turns=3).run(
            model="m", effort="low", system="", prompt="p", output_schema={}, tools=[tool])


def test_daily_review_with_llm_persists_recommendations(sf, settings):
    client = FakeClient([
        tool_use_response("get_ad_breakdown", {"shop_id": "S1", "level": "search_term", "days": 14, "asin": "",
                                               "campaign_id": "", "order_by": "spend", "limit": 5}),
        final_response(SAMPLE_OUTPUT),
    ])
    report = run_daily_review(sf, settings, ANCHOR, LLM(client=client))
    first_call = client.beta.messages.calls[0]
    assert "每日复盘 SOP" in first_call["system"]  # 加载了 skills
    assert "<facts>" in first_call["messages"][0]["content"]
    tool_result = json.loads(client.beta.messages.calls[1]["messages"][2]["content"][0]["content"])
    assert len(tool_result) == 5 and tool_result[0]["spend"] >= tool_result[-1]["spend"]

    md = open(report.markdown_path, encoding="utf-8").read()
    assert "rubber scraper" in md and "需要你确认的问题" in md
    with sf() as s:
        recs = list(s.scalars(select(RecommendationRow).where(RecommendationRow.report_id == report.id)))
    assert len(recs) == 1 and recs[0].status == "proposed"


def test_offline_review_and_diagnosis(sf, settings):
    report = run_daily_review(sf, settings, ANCHOR, None)
    assert report.model == "rules"
    assert report.content["recommendations"]
    diag = run_diagnosis(sf, settings, "S1", "B0HCTEST01", ANCHOR, None)
    assert diag.asin == "B0HCTEST01"
    assert any(f["title"] == "销量骤降" for f in diag.content["findings"])


def test_rules_output_matches_schema(sf, settings):
    """离线输出和 agent 输出必须是同一个结构，下游（入库、推送）才能统一处理。"""
    report = run_daily_review(sf, settings, ANCHOR, None)
    assert set(report.content) == set(REVIEW_OUTPUT["required"])
    rec_keys = set(REVIEW_OUTPUT["properties"]["recommendations"]["items"]["required"])
    assert all(set(r) == rec_keys for r in report.content["recommendations"])


def test_tools_refuse_untracked_products(sf, settings):
    from axis.agents.tools import build_tools
    from axis.watchlist import load_scope

    with sf() as s:
        scope = load_scope(s)
    tools = {t.name: t for t in build_tools(sf, ANCHOR, settings.thresholds, scope)}
    with pytest.raises(ValueError, match="不在关注列表"):
        tools["get_product_detail"].fn({"shop_id": "S4", "asin": "B0KATEST01"})
    with pytest.raises(ValueError, match="没有关注的产品"):
        tools["list_campaigns"].fn({"shop_id": "S5"})
    camps = tools["list_campaigns"].fn({"shop_id": "S4"})
    assert {c["campaign_id"] for c in camps} == {"S4-C-AUTO", "S4-C-EXACT"}
    rows = tools["get_ad_breakdown"].fn({"shop_id": "S4", "level": "search_term", "days": 14, "asin": "",
                                         "campaign_id": "", "order_by": "spend", "limit": 100})
    assert rows and all(r["asin"] == "B0KCTEST01" for r in rows)
