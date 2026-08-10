"""Parse document attachments (Phase 2 MVP — text/markdown inline)."""

from __future__ import annotations

from steps.base import StepContext, StepExecutor, StepResult
from steps.context_ref import store_payload


class ParseDocsExecutor(StepExecutor):
    capability = "parse_docs"

    def execute(self, ctx: StepContext) -> StepResult:
        attachments = ctx.context.get("attachments") or []
        parsed: list[dict[str, str]] = []

        for i, att in enumerate(attachments):
            if isinstance(att, str):
                parsed.append({"name": f"file_{i}.txt", "text": att})
            elif isinstance(att, dict):
                text = att.get("text") or att.get("content") or ""
                name = att.get("name") or att.get("filename") or f"file_{i}"
                parsed.append({"name": name, "text": text})

        combined = "\n\n".join(f"## {p['name']}\n{p['text']}" for p in parsed if p["text"])
        meta = store_payload(f"{ctx.workflow_id}:{ctx.node_id}:parsed", parsed if combined else [])

        delta: dict = {}
        if "inline" in meta:
            delta["parsed_documents"] = meta["inline"]
        else:
            delta["parsed_documents_ref"] = meta["parsed_documents_ref"]
            delta["parsed_documents_bytes"] = meta["parsed_documents_bytes"]
        if combined:
            delta["parsed_text"] = combined[:8000]

        return StepResult(context_delta=delta, output={"count": len(parsed), "chars": len(combined)})
