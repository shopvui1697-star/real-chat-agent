# 01 — Overview & Goals

## Problem statement

**mini-agent-orchestrator** provides production-grade workflow infrastructure: DAG execution, routing, rules, retries, DLQ, circuit breakers, and resync sweep. Its RFQ demo already runs **one Celery task per step** — the right pattern for observability and resilience.

**Real Chat Agent** applies that pattern to chat: the **workflow YAML is the agent**; each capability (`memory_load`, `rag_retrieve`, `context_build`, `llm_generate`, …) is an orchestrator DAG node; **LLM runs last** on assembled context.

## Vision

> A production chat platform where every turn is an observable workflow of explicit steps, and the LLM node produces the final answer from merged context.

Users see a chat UI. Under the hood: **rules pick a workflow tier** — simple messages use `chat_simple` (3 steps); complex turns use parallel prefetch or fixed-depth ReAct. Not every message runs the full DAG.

See [09-design-decisions-vi.md](09-design-decisions-vi.md) for ADRs and [05-chat-workflows.md](05-chat-workflows.md) for templates.

## Goals

1. **Real chat UX** — Sessions, streaming from `llm_generate`, per-step progress ("Searching KB…", "Calling maps…").
2. **Decomposed steps** — Memory, RAG, MCP, context, parse, LLM as separate orchestrator nodes — not a monolithic ReAct worker.
3. **Workflow tiers** — `chat_simple` for most traffic; full parallel / ReAct only when intent requires it.
4. **Orchestrator as the only runtime** — Scheduling, context merge, resilience unchanged from mini-agent-orchestrator.
5. **Production path** — Redis MVP → PostgreSQL + priority queues + full observability (Part A).

## Non-goals (initial release)

- Monolithic `ReActAgent.run()` as the primary execution path.
- **Unbounded ReAct loops** in MVP — fixed **1–2 iterations**; deep ReAct → Phase 3 / Temporal (ADR-08).
- Replacing hello-agent course repos or orchestrator assessment code.
- Agentic-RL training, GUI/Web agents, low-code builders.
- Temporal migration until long HITL steps are required.

## Target users

| Persona | Need |
|---------|------|
| Developer | Compose chat behavior in YAML; add steps without changing gateway |
| Operator | Per-node metrics, DLQ replay for failed `rag_retrieve` or `llm_generate` |
| End user | Reliable chat with memory, KB, and MCP-backed answers |

## Success criteria

| Category | Metric / outcome |
|----------|------------------|
| Functional | Message → workflow runs all nodes → streamed LLM reply |
| Decomposition | Each capability callable as isolated Celery step |
| Reliability | Per-node retry/DLQ; resync sweep recovers stuck `llm_generate` |
| Routing | Rules skip RAG, route senior LLM, require MCP nodes |
| Observability | Timeline shows every node (memory, rag, context, llm), not one "agent" box |

## Design principles

1. **Workflow is the agent** — YAML DAG defines behavior; no hidden tool loop inside one worker.
2. **One Celery task = one capability** — Memory, RAG, MCP, context, parse, LLM are separate steps.
3. **LLM last** — `llm_generate` consumes `context.prompt_messages` built by upstream nodes.
4. **Context accumulates in orchestrator** — Each node returns `context_delta`; orchestrator CAS-merges.
5. **ReAct is a bounded workflow** — `chat_react.yaml`, max 2 iterations MVP; not `ReActAgent` while-loop.
6. **Parallel when independent** — Prefetch fan-out only for tiers that need multiple sources (`chat_full`, `chat_attachment_rag`); not for `chat_simple`.
7. **Intent routing** — Rules select workflow template before submit.

## Glossary

| Term | Meaning |
|------|---------|
| **Step** | Single capability invocation (one DAG node) |
| **Capability** | Registry tag: `memory_load`, `rag_retrieve`, `context_build`, `mcp_invoke`, `llm_generate`, … |
| **Workflow** | YAML-defined DAG — the agent definition |
| **Context blob** | Shared workflow state merged after each step |
| **Turn** | One user message → one workflow instance through all steps |
| **Join node** | Step (usually `context_build`) that waits for all parallel predecessors |
| **Workflow tier** | Template family: `simple`, `rag`, `attachment`, `full`, `react` |
| **Tool Catalog** | MCP/builtin specs (description + JSON Schema) exposed to `llm_plan` |
| **Step Registry** | Celery worker pools by capability — not shown to LLM |
| **Fixed-depth ReAct** | MVP cap of 1–2 tool iterations; deep loops via Temporal (Phase 3) |
