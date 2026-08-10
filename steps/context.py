"""Assemble prompt_messages at join barrier."""

from __future__ import annotations

from steps.base import StepContext, StepExecutor, StepResult
from steps.context_ref import resolve_field


class ContextBuildExecutor(StepExecutor):
    capability = "context_build"

    def execute(self, ctx: StepContext) -> StepResult:
        user_message = ctx.context.get("user_message", "")
        memory = ctx.context.get("memory_snippets") or []
        rag_chunks = resolve_field(ctx.context, "rag_chunks") or []
        parsed = resolve_field(ctx.context, "parsed_documents") or []
        mcp_results = ctx.context.get("mcp_results") or []
        tool_observations = ctx.context.get("tool_observations") or []

        system_parts = ["You are a helpful assistant. Answer based on the provided context."]

        if memory:
            system_parts.append("Session memory:\n" + "\n".join(f"- {m}" for m in memory))

        if rag_chunks:
            kb_text = "\n\n".join(
                f"[{c.get('source', 'kb')}] {c.get('text', '')}" for c in rag_chunks if isinstance(c, dict)
            )
            system_parts.append(f"Knowledge base excerpts:\n{kb_text}")

        if parsed:
            doc_text = ctx.context.get("parsed_text") or "\n\n".join(
                f"## {d.get('name', 'doc')}\n{d.get('text', '')}" for d in parsed if isinstance(d, dict)
            )
            system_parts.append(f"Attached documents:\n{doc_text[:6000]}")

        if mcp_results:
            tool_text = "\n".join(
                f"- {r.get('tool', 'tool')}: {r.get('result', r)}" for r in mcp_results if isinstance(r, dict)
            )
            system_parts.append(f"Tool results:\n{tool_text}")

        if tool_observations:
            system_parts.append("Previous reasoning:\n" + "\n".join(str(o) for o in tool_observations))

        messages = [
            {"role": "system", "content": "\n".join(system_parts)},
            {"role": "user", "content": user_message},
        ]

        return StepResult(
            context_delta={"prompt_messages": messages},
            output={"message_count": len(messages), "system_chars": len(messages[0]["content"])},
        )
