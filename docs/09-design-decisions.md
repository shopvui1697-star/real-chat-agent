# 09 — Design Decisions & Known Limits (ADR)

English ADR companion to [09-design-decisions-vi.md](09-design-decisions-vi.md).

## ADR-08: Fixed-depth ReAct (MVP)

**Context:** Monolithic `ReActAgent` runs an unbounded think → act → observe loop. YAML DAGs are static.

**Decision:**

| Phase | ReAct model | Max depth |
|-------|-------------|-----------|
| **MVP (Phase 1–2)** | `chat_react.yaml` workflow | **1–2 iterations** (`max_iterations: 2` in workflow config) |
| **Phase 3+** | **Temporal** (preferred) or equivalent | Unbounded loop with safety cap (e.g. 10); HITL signals |

MVP does **not** replicate `ReActAgent.run()` while-loop. One iteration ≈ `llm_plan → (mcp ∥ rag) → context_build → llm_observe`. Final answer via `llm_generate`.

Deep multi-tool reasoning → Phase 3 Temporal, not extended Celery DAG hacks.

**User-facing note:** *"ReAct mode supports up to 2 tool rounds (MVP). For deeper agent loops, use Research mode (Phase 3) or Temporal-backed workflows."*

See [05-chat-workflows.md](05-chat-workflows.md) § ReAct fixed-depth.

## Other ADRs (summary)

| ID | Decision |
|----|----------|
| ADR-02 | Workflow tiers: `simple`, `rag`, `attachment`, `full`, `react` |
| ADR-03 | Intent-based template selection via rules |
| ADR-04 | Parallel prefetch only when intent requires multiple sources |
| ADR-05 | Large payloads via S3/ref; ≤32KB inline in workflow state |
| ADR-06 | Straggler: per-node timeout + partial join for optional nodes |
| ADR-07 | MCP after `llm_plan` by default |
| ADR-10 | Tool Catalog: specs in `config/tools/`, resolve before `llm_plan` |
| ADR-09 | Phase 1 ships `chat_simple` first |

Full rationale (Vietnamese): [09-design-decisions-vi.md](09-design-decisions-vi.md).
