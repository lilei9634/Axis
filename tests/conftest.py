import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from axis.config import Settings
from axis.db.session import make_session_factory
from axis.erp.fake import FakeAdapter
from axis.etl.sync import sync

ANCHOR = date(2026, 10, 6)
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        db_url=f"sqlite:///{tmp_path}/axis.db",
        data_dir=tmp_path / "data",
        reports_dir=tmp_path / "reports",
        skills_dir=ROOT / "skills",
    )


@pytest.fixture
def sf(settings):
    factory = make_session_factory(settings.db_url)
    sync(FakeAdapter(anchor=ANCHOR), factory, ANCHOR, 30)
    return factory


class FakeMessages:
    """模拟 client.beta.messages：按顺序返回预设的响应，并记录每次请求。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        # 记录当时的消息快照（之后列表还会被追加）
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.beta = SimpleNamespace(messages=FakeMessages(responses))


def usage():
    return SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=0)


def tool_use_response(name: str, inp: dict, id_: str = "toolu_1"):
    return SimpleNamespace(
        stop_reason="tool_use", model="claude-opus-5-5", usage=usage(),
        content=[SimpleNamespace(type="tool_use", id=id_, name=name, input=inp)],
    )


def final_response(output: dict):
    return SimpleNamespace(
        stop_reason="end_turn", model="claude-opus-5-5", usage=usage(),
        content=[SimpleNamespace(type="text", text=json.dumps(output, ensure_ascii=False))],
    )


SAMPLE_OUTPUT = {
    "summary": "S1 的 C 产品销量腰斩，需要优先排查。",
    "findings": [
        {"severity": "critical", "title": "销量骤降", "detail": "近 7 天销量下降 55%", "shop_id": "S1", "asin": "B0S1CTEST0"}
    ],
    "recommendations": [
        {
            "action_type": "negate_search_term", "shop_id": "S1", "asin": "B0S1ATEST0", "campaign_id": "S1-A-AUTO",
            "target": "rubber scraper", "current_value": "", "proposed_value": "否定精准",
            "reason": "14 天 0 出单", "expected_impact": "每周节省约 30 美元", "confidence": "high", "risk": "low",
        }
    ],
    "questions_for_owner": ["C 产品的采购成本是多少？"],
}
