# 09 — Phản biện, quyết định thiết kế & giới hạn (ADR)

*Tài liệu ghi lại đánh giá sau review kiến trúc, các quyết định đã chốt, và giới hạn có chủ ý của MVP.*

---

## Tóm tắt quyết định

| ID | Quyết định | Trạng thái |
|----|------------|------------|
| ADR-01 | Capabilities tách thành orchestrator steps, không monolithic ReAct | ✅ Chốt |
| ADR-02 | **Workflow tiers** — `simple` / `rag` / `attachment` / `full` / `react` | ✅ Chốt |
| ADR-03 | **Intent routing** — rules chọn template, không một DAG cho mọi message | ✅ Chốt |
| ADR-04 | Parallel prefetch chỉ khi intent cần nhiều nguồn dữ liệu | ✅ Chốt |
| ADR-05 | **Context ref** — payload lớn qua S3/ref, không inline Redis | ✅ Chốt |
| ADR-06 | **Straggler policy** — timeout + partial join tại barrier | ✅ Chốt |
| ADR-07 | MCP **sau `llm_plan`**, không prefetch mù sau `rules_eval` | ✅ Chốt |
| ADR-08 | **Fixed-depth ReAct: max 1–2 iterations MVP**; ReAct sâu → Phase 3 / Temporal | ✅ Chốt |
| ADR-09 | Phase 1 bắt đầu `chat_simple`, không bắt buộc full parallel | ✅ Chốt |
| ADR-10 | **Tool Catalog** tách khỏi Step Registry — specs cho `llm_plan` | ✅ Chốt |
| ADR-11 | **9Router** (optional) làm LLM gateway OpenAI-compatible cho `llm_*` workers | ✅ Chốt |

---

## ADR-02: Workflow tiers

**Vấn đề:** Mọi message chạy full DAG (6–7 node) là over-engineering cho câu hỏi đơn giản.

**Quyết định:** Gateway / `rules_eval` chọn template theo intent:

| Template | Khi nào | Pipeline |
|----------|---------|----------|
| `chat_simple.yaml` | Chào hỏi, Q&A ngắn, không KB/attachment/MCP | `memory_load → llm_generate → persist_turn` |
| `chat_rag.yaml` | Cần KB, không attachment | `memory ∥ rag → context_build → llm → persist` |
| `chat_attachment.yaml` | Chỉ file đính kèm | `parse_docs → context_build → llm → persist` |
| `chat_attachment_rag.yaml` | File + so sánh KB | `memory ∥ rag ∥ parse → context_build → llm` |
| `chat_attachment_rag_sequential.yaml` | Search **trong** file vừa parse | `parse_docs → rag_on_parsed → context_build → llm` |
| `chat_react.yaml` | Cần tool/MCP | fixed-depth ReAct (xem ADR-08) |
| `chat_full.yaml` | Session phức tạp (travel, multi-source) | full parallel prefetch |

**Phase 1:** chỉ implement `chat_simple`. Các tier mở dần Phase 2+.

---

## ADR-03: Intent routing

**Vấn đề:** `parse_docs ∥ rag_retrieve` không đúng mọi lúc.

**Quyết định:** Rules set `context.intent` trước khi chọn workflow:

| Intent | Workflow |
|--------|----------|
| `simple` | `chat_simple` |
| `kb_only` | `chat_rag` |
| `attachment_only` | `chat_attachment` |
| `kb_and_attachment` | `chat_attachment_rag` (parallel) |
| `rag_on_attachment` | `chat_attachment_rag_sequential` |
| `needs_tools` | `chat_react` |

Ví dụ rule:

```json
{
  "id": "attachment_only",
  "condition": "has_attachments == true and needs_kb == false",
  "actions": ["intent:attachment_only", "workflow:chat_attachment"]
}
```

---

## ADR-08: Fixed-depth ReAct (MVP)

**Vấn đề:** ReActAgent trong hello-agent lặp **không giới hạn**: LLM → tool → observe → LLM → … YAML workflow tĩnh không mô phỏng được vòng lặp động sâu.

**Quyết định (C — thừa nhận giới hạn):**

### MVP (Phase 1–2)

- ReAct được mô hình hóa bằng workflow **`chat_react.yaml`** với **`max_iterations: 2`** (cấu hình workflow).
- Một “iteration” = `llm_plan → (mcp_invoke ∥ rag_retrieve) → context_build → llm_observe → [optional iteration 2]`.
- **Tối đa 2 lần gọi LLM cho planning/observe** + 1 lần `llm_generate` final = **tối đa 3 LLM calls/turn** trong react mode.
- **Không** cố port vòng `while` vô hạn của `ReActAgent.run()`.

### Phase 3+ (ReAct sâu)

- Chuyển sang **Temporal** (hoặc engine tương đương) cho:
  - Vòng lặp ReAct không giới hạn (có `max_iterations` cap an toàn, vd. 10)
  - Human-in-the-loop (approval hours/days)
  - Durable history + replay sau crash
- **Không** mở rộng Celery DAG tự chế thành vòng lặp động — tránh split-brain giữa orchestrator và Temporal.

### Ghi rõ trong docs / UI

> *“Chế độ ReAct hỗ trợ tối đa 2 bước suy luận + gọi tool (MVP). Tác vụ cần nhiều vòng hơn dùng chế độ Research (Phase 3) hoặc Temporal workflow.”*

---

## ADR-10: Tool & MCP Catalog (LLM-facing)

**Vấn đề:** `llm_plan` cần biết tool nào tồn tại, mô tả, tham số — hiện chưa có nơi lưu đặc tả để LLM quyết định.

**Quyết định:**

- **Step Registry** (`mcp_amap_v1`) = worker Celery thực thi.
- **Tool Catalog** (`config/tools/`, MCP `tools/list`) = **đặc tả** LLM đọc khi plan.
- Node **`tool_catalog_resolve`** sau `memory_load`: lọc theo tenant/session → ghi `context.tool_catalog`.
- **`llm_plan`** output `planned_tools[]`; **`mcp_invoke`** validate ⊆ allowlist + JSON Schema.
- RAG/parse/memory = bước pipeline, **không** đưa vào catalog (trừ builtin `search_kb` tương lai).

---

## ADR-05: Context ref (payload lớn)

**Vấn đề:** Merge full `parsed_documents` text vào Redis workflow state → chậm, tốn RAM.

**Quyết định:**

- Worker trả `context_delta` **nhỏ** + `*_ref` cho dữ liệu lớn.
- `context_build` worker fetch refs (S3 / Redis side store) khi assemble prompt.

```json
{
  "context_delta": {
    "parsed_documents_ref": "s3://bucket/tenant/turn_456/parsed.json",
    "parsed_documents_bytes": 89000
  }
}
```

Ngưỡng inline: **≤ 32KB** trong state; trên ngưỡng → ref bắt buộc.

---

## ADR-06: Straggler tại join barrier

**Vấn đề:** `context_build` chờ `max(T_memory, T_rag, T_parse)` — một PDF lớn chặn cả turn.

**Quyết định:**

| Policy | Hành vi |
|--------|---------|
| `node_timeout` | Mỗi prefetch node: default 30s (parse: 60s) |
| `partial_join` | Nếu optional node timeout → mark `FAILED_SKIPPED`, join với empty delta + user warning |
| `hard_fail` | Required node timeout → fail turn, DLQ |
| `async_parse` | File > N MB → upload async; turn dùng `chat_simple` + thông báo “đang xử lý file” |

---

## ADR-07: Vị trí MCP

**Quyết định:** MCP **mặc định sau `llm_plan`**, không `mcp_prefetch` song song `rules_eval`.

Ngoại lệ: session config cố định (`session.mcp_prefetch: ["amap_weather"]`) → rules có thể thêm prefetch song song memory/rag.

---

## ADR-09: Lộ trình implement

```
Phase 1: chat_simple (sequential, ít node)
Phase 2: chat_rag, chat_attachment, parallel prefetch, chat_react (max 2 iter)
Phase 3: chat_full, Temporal cho ReAct sâu + HITL
```

---

## ADR-11: 9Router làm LLM gateway (optional)

**Bối cảnh:** Worker `llm_generate` / `llm_plan` / `llm_observe` gọi API qua `HelloAgentsLLM` (OpenAI-compatible).

**Quyết định:** Có thể trỏ `OPENAI_BASE_URL` tới [9Router](https://9router.com/) (`http://localhost:20128/v1`) để quản lý & xoay vòng provider:

| 9Router | Real Chat Agent |
|---------|-----------------|
| 3-tier fallback (subscription → cheap → free) | Giảm 429 khi stream `llm_generate` |
| 60+ providers, một endpoint | Worker không đổi code — chỉ đổi env |
| Combos / multi-account | Alias model khác nhau cho tier tenant |
| Dashboard quota | Dev/staging observability |

**Hai tầng routing (tách bạch):**

1. **Orchestrator router** — chọn profile worker (`llm_senior_v1` vs `llm_default_v1`) theo business rules.
2. **9Router** — chọn provider thực tế cho HTTP request; fallback khi hết quota.

Orchestrator retry/DLQ/CB **vẫn giữ** — 9Router không thay thế resilience workflow.

**Production:** Chạy 9Router như **internal service** (`http://9router.llm.svc:20128/v1`), không expose ra internet. Alternative production: nhiều profile LLM + Part A CB, không phụ thuộc 9Router.

Chi tiết cấu hình: [07-deployment.md](07-deployment.md) § LLM gateway.

---

## Liên kết tài liệu kỹ thuật

- Workflow YAML chi tiết: [05-chat-workflows.md](05-chat-workflows.md)
- Kiến trúc adaptive: [02-architecture.md](02-architecture.md)
- Context ref contract: [04-integration-design.md](04-integration-design.md)
- Roadmap: [08-implementation-roadmap.md](08-implementation-roadmap.md)
