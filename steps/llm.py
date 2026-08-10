"""LLM step — final_answer, plan_tools, observe modes + SSE stream."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import redis

from steps.base import StepContext, StepExecutor, StepResult


def _redis() -> redis.Redis:
    return redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


def _stream_channel(ctx: StepContext) -> str | None:
    ch = ctx.context.get("stream_channel")
    if ch:
        return ch
    sid = ctx.context.get("session_id")
    tid = ctx.context.get("turn_id")
    if sid and tid:
        return f"stream:{sid}:{tid}"
    return None


def _publish_stream(channel: str | None, event: str, data: dict[str, Any]) -> None:
    if not channel:
        return
    _redis().publish(channel, json.dumps({"event": event, **data}))


def _publish_tokens(channel: str | None, text: str) -> None:
    if not channel or not text:
        return
    chunk_size = 12
    for i in range(0, len(text), chunk_size):
        _publish_stream(channel, "token", {"token": text[i : i + chunk_size]})


def _messages_from_context(ctx: StepContext) -> list[dict[str, str]]:
    if ctx.context.get("prompt_messages"):
        return list(ctx.context["prompt_messages"])
    user_message = ctx.context.get("user_message", "")
    memory = ctx.context.get("memory_snippets") or []
    system_parts = ["You are a helpful assistant."]
    if memory:
        system_parts.append("Session memory:\n" + "\n".join(f"- {m}" for m in memory))
    return [
        {"role": "system", "content": "\n".join(system_parts)},
        {"role": "user", "content": user_message},
    ]


def _mock_reply(user_message: str, mode: str = "final_answer") -> str:
    if mode == "plan_tools":
        tools = []
        if re.search(r"weather|thời tiết", user_message, re.I):
            tools.append({"name": "maps_weather", "arguments": {"city": "Hanoi"}})
        if re.search(r"search|tìm|restaurant|nhà hàng", user_message, re.I):
            tools.append({"name": "maps_text_search", "arguments": {"keywords": "restaurant"}})
        if not tools:
            tools.append({"name": "maps_weather", "arguments": {"city": "Hanoi"}})
        return json.dumps({"planned_tools": tools, "reasoning": "Mock plan based on keywords"})
    if mode == "observe":
        return "Tools executed successfully. Summarize results for the user."
    return f"[mock-llm] Echo: {user_message}"


def _call_openai(messages: list[dict[str, str]], model: str) -> str:
    from openai import OpenAI

    client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL") or None,
    )
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.7")),
    )
    return response.choices[0].message.content or ""


class LlmGenerateExecutor(StepExecutor):
    capability = "llm_generate"

    def execute(self, ctx: StepContext) -> StepResult:
        mode = ctx.params.get("mode", "final_answer")
        messages = _messages_from_context(ctx)
        model = os.getenv("LLM_MODEL", "gpt-4o-mini")
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        channel = _stream_channel(ctx) if ctx.params.get("stream", mode == "final_answer") else None

        if mode == "plan_tools":
            catalog = ctx.context.get("tool_catalog") or []
            messages.append({
                "role": "system",
                "content": f"Available tools: {json.dumps(catalog, ensure_ascii=False)[:4000]}. "
                "Respond with JSON: {{\"planned_tools\": [{{\"name\": ..., \"arguments\": {{...}}}}]}}",
            })

        if not api_key or api_key == "mock":
            content = _mock_reply(ctx.context.get("user_message", ""), mode)
            model_used = "mock"
        else:
            content = _call_openai(messages, model)
            model_used = model

        delta: dict[str, Any] = {"llm_usage": {"model": model_used, "mode": mode}}

        if mode == "plan_tools":
            try:
                parsed = json.loads(content)
                delta["planned_tools"] = parsed.get("planned_tools", [])
                delta["plan_reasoning"] = parsed.get("reasoning", "")
            except json.JSONDecodeError:
                delta["planned_tools"] = [{"name": "maps_weather", "arguments": {"city": "Hanoi"}}]
            iteration = int(ctx.context.get("react_iteration", 0))
            delta["react_iteration"] = iteration + 1
            return StepResult(context_delta=delta, output={"planned": len(delta.get("planned_tools", []))})

        if mode == "observe":
            obs = list(ctx.context.get("tool_observations") or [])
            obs.append(content)
            delta["tool_observations"] = obs
            iteration = int(ctx.context.get("react_iteration", 0)) + 1
            max_it = int(ctx.context.get("max_iterations", 2))
            if ctx.context.get("runtime") == "temporal" or ctx.context.get("deep_react"):
                delta["needs_another_round"] = iteration < max_it
            else:
                delta["needs_another_round"] = False
            return StepResult(context_delta=delta, output={"observe": True})

        _publish_stream(channel, "start", {"mode": mode})
        _publish_tokens(channel, content)
        _publish_stream(channel, "done", {"content": content})

        delta["assistant_message"] = content
        if not ctx.context.get("prompt_messages"):
            delta["prompt_messages"] = messages
        return StepResult(context_delta=delta, output={"chars": len(content), "model": model_used})
