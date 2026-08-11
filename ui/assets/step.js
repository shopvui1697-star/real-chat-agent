const NODE_LABELS = {
  rules_eval: "Rules",
  memory_load: "Memory",
  memory_store: "Memory store",
  parse_docs: "Parse",
  rag_retrieve: "RAG",
  rag_on_parsed: "RAG on file",
  context_build: "Context",
  tool_catalog_resolve: "Tool catalog",
  llm_plan: "LLM plan",
  llm_observe: "LLM observe",
  mcp_invoke: "MCP",
  llm_generate: "LLM",
  persist_turn: "Persist",
  persist_reply: "Persist",
};

const params = new URLSearchParams(window.location.search);
const workflowId = params.get("workflow_id") || "";
const stepId = params.get("step") || "";

const titleEl = document.getElementById("step-title");
const subtitleEl = document.getElementById("step-subtitle");
const badgeEl = document.getElementById("step-status-badge");
const metaEl = document.getElementById("step-meta");
const errorEl = document.getElementById("step-error");
const resultEl = document.getElementById("step-result");
const contextSection = document.getElementById("context-section");
const contextEl = document.getElementById("step-context");
const outputSection = document.getElementById("output-section");
const outputEl = document.getElementById("step-output");
const extraSection = document.getElementById("extra-section");
const extraEl = document.getElementById("step-extra");

function pretty(value) {
  return JSON.stringify(value, null, 2);
}

function showError(message) {
  errorEl.textContent = message;
  errorEl.classList.remove("hidden");
  resultEl.textContent = "—";
}

function addMetaRow(label, value, mono = false) {
  const row = document.createElement("div");
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value ?? "—";
  if (mono) dd.classList.add("mono");
  row.append(dt, dd);
  metaEl.appendChild(row);
}

function statusBadgeClass(status) {
  const s = (status || "").toLowerCase();
  if (s === "done" || s === "completed") return "badge ok";
  if (s === "running") return "badge runtime";
  if (s === "failed" || s === "dlq") return "badge fail";
  return "badge";
}

function renderCelery(data) {
  const step = data.step || {};
  const result = step.result || {};
  const status = step.status || "UNKNOWN";

  document.title = `${NODE_LABELS[stepId] || stepId} — Real Chat Agent`;
  titleEl.textContent = NODE_LABELS[stepId] || stepId;
  subtitleEl.textContent = `${data.workflow_name || "workflow"} · ${stepId}`;
  badgeEl.className = statusBadgeClass(status);
  badgeEl.textContent = status;

  addMetaRow("Workflow ID", data.workflow_id, true);
  addMetaRow("Workflow status", data.workflow_status);
  addMetaRow("Runtime", data.runtime);
  addMetaRow("Capability", data.capability || result.capability);
  addMetaRow("Agent", step.agent || result.agent);
  addMetaRow("Attempts", step.attempts ?? result.attempt);
  addMetaRow("Started", step.started_at);
  addMetaRow("Finished", step.finished_at);

  if (step.error) {
    showError(step.error);
  }

  resultEl.textContent = pretty(result);

  const delta = result.context_delta;
  if (delta && Object.keys(delta).length) {
    contextSection.classList.remove("hidden");
    contextEl.textContent = pretty(delta);
  }

  const output = result.output;
  if (output && Object.keys(output).length) {
    outputSection.classList.remove("hidden");
    outputEl.textContent = pretty(output);
  }
}

function renderTemporal(data) {
  const step = data.step || {};
  const status = step.status || data.workflow_status || "UNKNOWN";
  const activities = data.activities || [];

  document.title = `${NODE_LABELS[stepId] || stepId} — Real Chat Agent`;
  titleEl.textContent = NODE_LABELS[stepId] || stepId;
  subtitleEl.textContent = `${data.workflow_type || "temporal"} · ${stepId}`;
  badgeEl.className = statusBadgeClass(status);
  badgeEl.textContent = status;

  addMetaRow("Workflow ID", data.workflow_id, true);
  addMetaRow("Workflow status", data.workflow_status);
  addMetaRow("Runtime", data.runtime);
  addMetaRow("Temporal UI", data.temporal_ui_url);

  if (data.query) {
    extraSection.classList.remove("hidden");
    extraEl.textContent = pretty({ query: data.query, history_note: data.history_note });
  }

  if (activities.length) {
    const last = activities[activities.length - 1];
    addMetaRow("Matched activities", String(activities.length));
    addMetaRow("Last node", last.node_id, true);
    addMetaRow("Capability", last.capability);

    const result = last.result || {};
    resultEl.textContent = pretty(result);

    if (result.context_delta) {
      contextSection.classList.remove("hidden");
      contextEl.textContent = pretty(result.context_delta);
    }
    if (result.output) {
      outputSection.classList.remove("hidden");
      outputEl.textContent = pretty(result.output);
    }
    return;
  }

  if (data.workflow_result) {
    resultEl.textContent = pretty({
      note: "No matching activity yet — showing workflow result snapshot",
      workflow_result: data.workflow_result,
    });
    return;
  }

  resultEl.textContent = pretty({
    note: "Step has not produced activity output yet",
    history_note: data.history_note,
    query: data.query,
  });
}

async function loadStep() {
  if (!workflowId || !stepId) {
    titleEl.textContent = "Missing parameters";
    showError("Cần workflow_id và step trong URL, ví dụ: /step.html?workflow_id=...&step=llm_generate");
    return;
  }

  try {
    const res = await fetch(`/v1/workflows/${encodeURIComponent(workflowId)}/steps/${encodeURIComponent(stepId)}`);
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || res.statusText);
    }
    const data = await res.json();
    if (data.runtime === "temporal") {
      renderTemporal(data);
    } else {
      renderCelery(data);
    }
  } catch (err) {
    titleEl.textContent = NODE_LABELS[stepId] || stepId;
    subtitleEl.textContent = workflowId;
    showError(err.message || "Không tải được step detail");
  }
}

loadStep();
