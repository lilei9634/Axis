"""Claude 调用封装：带工具调用循环 + 结构化 JSON 输出。

- 工具（tools）让 agent 按需往下钻取数据库里的明细；
- output_config.format 保证最终输出是符合 schema 的 JSON，方便直接入库；
- 默认开启服务端 fallbacks：模型因安全分类器拒答时，自动换推荐模型重试。
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import anthropic

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AgentError(RuntimeError):
    pass


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[[dict], Any]

    def spec(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "strict": True,
        }


@dataclass
class AgentResult:
    output: dict
    model: str
    turns: int
    usage: dict = field(default_factory=dict)


class LLM:
    def __init__(self, client: Any = None, fallbacks: bool = True, max_turns: int = 12):
        self.client = client or anthropic.Anthropic()
        self.fallbacks = fallbacks
        self.max_turns = max_turns

    def run(
        self,
        *,
        model: str,
        effort: str,
        system: str,
        prompt: str,
        output_schema: dict,
        tools: list[Tool] = (),
        max_tokens: int = 16000,
    ) -> AgentResult:
        tool_map = {t.name: t for t in tools}
        messages: list[dict] = [{"role": "user", "content": prompt}]
        kwargs: dict[str, Any] = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": output_schema}},
            # 多轮工具调用会反复发送同一份长上下文，开启自动缓存
            cache_control={"type": "ephemeral"},
        )
        if tools:
            kwargs["tools"] = [t.spec() for t in tools]
        if self.fallbacks:
            kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")

        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
        for turn in range(1, self.max_turns + 1):
            resp = self.client.beta.messages.create(messages=messages, **kwargs)
            for k in usage:
                usage[k] += getattr(resp.usage, k, 0) or 0

            if resp.stop_reason == "refusal":
                category = getattr(getattr(resp, "stop_details", None), "category", None)
                raise AgentError(f"模型拒绝了这次请求（category={category}）")
            if resp.stop_reason == "max_tokens":
                raise AgentError("输出超过 max_tokens 被截断，请调大 max_tokens 或缩小分析范围")

            # 原样追加模型输出（包括 thinking 块），保持对话只追加不修改
            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason == "pause_turn":
                continue
            if resp.stop_reason == "tool_use":
                results = [self._call_tool(tool_map, b) for b in resp.content if b.type == "tool_use"]
                # 所有工具结果放在同一条 user 消息里返回
                messages.append({"role": "user", "content": results})
                continue

            text = "".join(b.text for b in resp.content if b.type == "text")
            try:
                output = json.loads(text)
            except json.JSONDecodeError as e:
                raise AgentError(f"模型输出不是合法 JSON: {e}") from e
            return AgentResult(output=output, model=getattr(resp, "model", model), turns=turn, usage=usage)

        raise AgentError(f"超过最大轮数 {self.max_turns} 仍未完成分析")

    @staticmethod
    def _call_tool(tool_map: dict[str, Tool], block) -> dict:
        tool = tool_map.get(block.name)
        try:
            if tool is None:
                raise KeyError(f"未知工具: {block.name}")
            result = tool.fn(block.input)
            content, is_error = json.dumps(result, ensure_ascii=False, default=str), False
        except Exception as e:
            log.warning("工具 %s 执行失败: %s", block.name, e)
            content, is_error = f"Error: {type(e).__name__}: {e}", True
        return {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error}
