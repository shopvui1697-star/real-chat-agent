const STORAGE_KEY = "rca_session_id";

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

const TEMPORAL_STEPS = [
  "memory_load",
  "tool_catalog_resolve",
  "llm_plan",
  "mcp_invoke",
  "rag_retrieve",
  "context_build",
  "llm_observe",
  "llm_generate",
  "persist_turn",
];

const $ = (sel) => document.querySelector(sel);

const messagesEl = $("#messages");
const timelineEl = $("#timeline");
const workflowMetaEl = $("#workflow-meta");
const workflowBadgeEl = $("#workflow-badge");
const runtimeBadgeEl = $("#runtime-badge");
const sessionLabelEl = $("#session-label");
const inputEl = $("#input");
const attachmentEl = $("#attachment");
const formEl = $("#compose");
const sendBtn = $("#send-btn");
const newSessionBtn = $("#new-session-btn");
const optKb = $("#opt-kb");
const optTools = $("#opt-tools");
const optDeep = $("#opt-deep");
const optResearch = $("#opt-research");
const optHitl = $("#opt-hitl");
const optMaxIter = $("#opt-max-iter");
const optScopeAttachment = $("#opt-scope-attachment");
const hitlBar = $("#hitl-bar");
const hitlApprove = $("#hitl-approve");
const hitlReject = $("#hitl-reject");

let sessionId = localStorage.getItem(STORAGE_KEY);
let sessionConfig = {};
let busy = false;
let activeTurnId = null;

function api(path, options = {}) {
  return fetch(path, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  }).then(async (res) => {
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || res.statusText);
    }
    return res.json();
  });
}

function renderEmpty() {
  messagesEl.replaceChildren();
  const el = document.createElement("div");
  el.className = "empty-state";
  el.append("Chọn mode và gửi tin nhắn. Deep ReAct / Research dùng Temporal.");
  messagesEl.appendChild(el);
}

function appendMessage(role, content, extraClass = "") {
  const empty = messagesEl.querySelector(".empty-state");
  if (empty) empty.remove();
  const el = document.createElement("div");
  el.className = `msg ${role} ${extraClass}`.trim();
  el.textContent = content;
  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return el;
}

function removeThinking() {
  messagesEl.querySelectorAll(".msg.thinking").forEach((n) => n.remove());
}

function workflowFailureMessage(wf, turn) {
  const nodes = wf.workflow?.nodes || turn?.nodes || {};
  for (const [nid, n] of Object.entries(nodes)) {
    const status = typeof n === "string" ? n : n?.status;
    const error = typeof n === "object" && n ? n.error : null;
    if (status === "FAILED" || status === "DLQ") {
      return error || `Node ${nid} failed`;
    }
  }
  if (wf.workflow?.status === "FAILED" || turn?.workflow_status === "FAILED") {
    return "Workflow failed";
  }
  return null;
}

function statusClass(status) {
  const s = (status || "").toLowerCase();
  if (s === "done" || s === "completed") return "done";
  if (s === "running") return "active";
  if (s === "skipped") return "skipped";
  if (s === "failed" || s === "dlq") return "failed";
  return "";
}

function renderTimelineFromNodes(nodes = {}, workflowStatus = null, template = null) {
  timelineEl.replaceChildren();
  const ids = nodes && Object.keys(nodes).length ? Object.keys(nodes) : [];
  if (!ids.length) {
    const li = document.createElement("li");
    li.textContent = "Waiting…";
    timelineEl.appendChild(li);
    return;
  }
  for (const id of ids) {
    const status = typeof nodes[id] === "string" ? nodes[id] : nodes[id]?.status;
    const li = document.createElement("li");
    li.className = statusClass(status);
    li.dataset.node = id;
    const dot = document.createElement("span");
    dot.className = "dot";
    const label = document.createElement("span");
    label.textContent = NODE_LABELS[id] || id;
    li.append(dot, label);
    timelineEl.appendChild(li);
  }
  if (template) workflowBadgeEl.textContent = template;
  if (workflowStatus) workflowMetaEl.textContent = `workflow: ${workflowStatus}`;
}

function renderTemporalTimeline(query = {}, workflowStatus = null, template = null) {
  timelineEl.replaceChildren();
  const iter = query?.iteration || 0;
  for (const step of TEMPORAL_STEPS) {
    const li = document.createElement("li");
    li.className = query?.waiting_hitl && step === "llm_observe" ? "active" : "done";
    const dot = document.createElement("span");
    dot.className = "dot";
    const label = document.createElement("span");
    label.textContent = NODE_LABELS[step] || step;
    li.append(dot, label);
    timelineEl.appendChild(li);
  }
  if (template) workflowBadgeEl.textContent = template;
  const parts = [`temporal: ${workflowStatus || "RUNNING"}`];
  if (iter) parts.push(`iter=${iter}`);
  if (query?.waiting_hitl) parts.push("HITL waiting");
  workflowMetaEl.textContent = parts.join(" · ");
}

function renderTimelineIdle() {
  timelineEl.replaceChildren();
  workflowBadgeEl.textContent = "idle";
  runtimeBadgeEl.textContent = "—";
  workflowMetaEl.textContent = "Chưa có workflow";
  hideHitl();
}

function hideHitl() {
  hitlBar.classList.add("hidden");
  activeTurnId = null;
}

function showHitl(turnId) {
  activeTurnId = turnId;
  hitlBar.classList.remove("hidden");
}

async function ensureSession() {
  if (sessionId) {
    try {
      const s = await api(`/v1/sessions/${sessionId}`);
      sessionLabelEl.textContent = sessionId;
      sessionConfig = s.config || {};
      optKb.checked = !!sessionConfig.rag_enabled;
      optTools.checked = !!sessionConfig.needs_tools;
      optDeep.checked = !!sessionConfig.deep_react;
      optResearch.checked = !!sessionConfig.research_mode;
      optHitl.checked = !!sessionConfig.hitl_enabled;
      if (sessionConfig.max_iterations) optMaxIter.value = sessionConfig.max_iterations;
      return sessionId;
    } catch {
      sessionId = null;
      localStorage.removeItem(STORAGE_KEY);
    }
  }
  const data = await api("/v1/sessions", {
    method: "POST",
    body: JSON.stringify({
      title: "Web chat",
      config: { rag_enabled: false, needs_tools: false, deep_react: false, research_mode: false },
    }),
  });
  sessionId = data.session_id;
  sessionConfig = data.config || {};
  localStorage.setItem(STORAGE_KEY, sessionId);
  sessionLabelEl.textContent = sessionId;
  return sessionId;
}

async function syncSessionConfig() {
  const deep = optDeep.checked;
  const research = optResearch.checked;
  sessionConfig = {
    ...sessionConfig,
    rag_enabled: optKb.checked,
    needs_tools: optTools.checked || deep,
    deep_react: deep,
    research_mode: research,
    hitl_enabled: optHitl.checked,
    max_iterations: parseInt(optMaxIter.value, 10) || 5,
    enabled_tools: ["amap.maps_weather", "amap.maps_text_search"],
  };
  await api(`/v1/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify({ config: sessionConfig }),
  });
}

async function loadHistory() {
  if (!sessionId) return;
  const data = await api(`/v1/sessions/${sessionId}/messages`);
  messagesEl.replaceChildren();
  if (!data.messages?.length) {
    renderEmpty();
    return;
  }
  for (const m of data.messages) appendMessage(m.role, m.content);
}

function openSSE(sessionId, turnId, onToken, onDone) {
  const es = new EventSource(`/v1/sessions/${sessionId}/turns/${turnId}/stream`);
  let buffer = "";
  es.addEventListener("token", (e) => {
    try {
      const p = JSON.parse(e.data);
      buffer += p.token || "";
      onToken(buffer);
    } catch { /* ignore */ }
  });
  es.addEventListener("done", (e) => {
    try {
      const p = JSON.parse(e.data);
      onDone(p.content || buffer);
    } catch {
      onDone(buffer);
    }
    es.close();
  });
  es.onerror = () => es.close();
  return es;
}

async function pollWorkflow(workflowId, turnId, template, runtime) {
  for (let i = 0; i < 40; i++) {
    const wf = await api(`/v1/workflows/${workflowId}/status`);
    const turn = await api(`/v1/sessions/${sessionId}/turns/${turnId}?wait=false`);

    if (runtime === "temporal" || wf.runtime === "temporal") {
      runtimeBadgeEl.textContent = "temporal";
      renderTemporalTimeline(wf.query || turn.temporal_query, wf.status, template);
      if (turn.temporal_query?.waiting_hitl || wf.query?.waiting_hitl) {
        showHitl(turnId);
      } else {
        hideHitl();
      }
      if (turn.status === "completed" && turn.assistant_message) return turn;
      if (wf.status === "COMPLETED" && turn.assistant_message) return turn;
      if (wf.status === "FAILED" || wf.status === "TERMINATED") {
        throw new Error("Temporal workflow failed");
      }
    } else {
      runtimeBadgeEl.textContent = "celery";
      const nodes = {};
      for (const [nid, n] of Object.entries(wf.workflow?.nodes || {})) {
        nodes[nid] = typeof n === "string" ? n : n.status;
      }
      renderTimelineFromNodes(nodes, wf.workflow?.status, template);
      const failure = workflowFailureMessage(wf, turn);
      if (failure) throw new Error(failure);
      if (turn.status === "completed" && turn.assistant_message) return turn;
      if (turn.status === "failed" || wf.workflow?.status === "FAILED") {
        throw new Error(failure || "Workflow failed");
      }
    }

    await new Promise((r) => setTimeout(r, 1000));
  }
  return api(`/v1/sessions/${sessionId}/turns/${turnId}?wait=true`);
}

async function sendMessage(text) {
  if (busy || !text.trim()) return;
  busy = true;
  sendBtn.disabled = true;
  hideHitl();
  await syncSessionConfig();

  appendMessage("user", text.trim());
  inputEl.value = "";

  const attText = attachmentEl.value.trim();
  const attachments = attText ? [{ name: "paste.txt", text: attText }] : [];

  const thinkingEl = appendMessage("assistant", "…", "thinking");
  renderTimelineIdle();
  workflowBadgeEl.textContent = "routing…";

  let streamEl = thinkingEl;
  let es = null;

  try {
    const accepted = await api(`/v1/sessions/${sessionId}/messages`, {
      method: "POST",
      body: JSON.stringify({
        content: text.trim(),
        attachments,
        needs_kb: optKb.checked,
        needs_tools: optTools.checked || optDeep.checked,
        deep_react: optDeep.checked,
        research_mode: optResearch.checked,
        query_scope: optScopeAttachment.checked ? "attachment" : "",
      }),
    });

    workflowBadgeEl.textContent = accepted.workflow_template || "—";
    runtimeBadgeEl.textContent = accepted.runtime || "celery";
    workflowMetaEl.textContent = accepted.workflow_id;

    es = openSSE(sessionId, accepted.turn_id, (partial) => {
      streamEl.textContent = partial;
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }, (final) => {
      streamEl.textContent = final;
      streamEl.classList.remove("thinking");
    });

    const turn = await pollWorkflow(
      accepted.workflow_id,
      accepted.turn_id,
      accepted.workflow_template,
      accepted.runtime,
    );

    if (es) es.close();
    removeThinking();
    hideHitl();

    if (turn.assistant_message && streamEl.classList.contains("thinking")) {
      appendMessage("assistant", turn.assistant_message);
    } else if (turn.assistant_message && !streamEl.textContent) {
      streamEl.textContent = turn.assistant_message;
      streamEl.classList.remove("thinking");
    } else if (!turn.assistant_message) {
      throw new Error("Không nhận được phản hồi từ assistant");
    }

    if (turn.react_iteration != null) {
      workflowMetaEl.textContent += ` · iter=${turn.react_iteration}`;
    }
  } catch (err) {
    if (es) es.close();
    removeThinking();
    hideHitl();
    appendMessage("assistant", `Lỗi: ${err.message}`, "error");
    renderTimelineIdle();
  } finally {
    busy = false;
    sendBtn.disabled = false;
    inputEl.focus();
  }
}

hitlApprove.addEventListener("click", async () => {
  if (!activeTurnId || !sessionId) return;
  try {
    await api(`/v1/sessions/${sessionId}/turns/${activeTurnId}/approve`, { method: "POST" });
    hitlBar.querySelector("span").textContent = "Approved — workflow tiếp tục…";
  } catch (err) {
    appendMessage("assistant", `HITL approve lỗi: ${err.message}`, "error");
  }
});

hitlReject.addEventListener("click", async () => {
  if (!activeTurnId || !sessionId) return;
  try {
    await api(`/v1/sessions/${sessionId}/turns/${activeTurnId}/reject`, { method: "POST" });
    hideHitl();
    appendMessage("assistant", "Turn bị reject bởi reviewer (HITL).", "error");
  } catch (err) {
    appendMessage("assistant", `HITL reject lỗi: ${err.message}`, "error");
  }
});

optDeep.addEventListener("change", () => {
  if (optDeep.checked) {
    optTools.checked = false;
    optResearch.checked = false;
  }
});

optResearch.addEventListener("change", () => {
  if (optResearch.checked) {
    optDeep.checked = false;
    optTools.checked = false;
  }
});

formEl.addEventListener("submit", (e) => {
  e.preventDefault();
  sendMessage(inputEl.value);
});

inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    formEl.requestSubmit();
  }
});

newSessionBtn.addEventListener("click", async () => {
  if (busy) return;
  localStorage.removeItem(STORAGE_KEY);
  sessionId = null;
  await ensureSession();
  renderEmpty();
  renderTimelineIdle();
});

async function init() {
  renderTimelineIdle();
  try {
    await ensureSession();
    await loadHistory();
  } catch (err) {
    messagesEl.replaceChildren();
    const el = document.createElement("div");
    el.className = "empty-state msg error";
    el.textContent = `Không kết nối gateway: ${err.message}`;
    messagesEl.appendChild(el);
  }
  inputEl.focus();
}

init();
