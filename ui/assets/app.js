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

const ALLOWED_EXTENSIONS = new Set([
  ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".log", ".html", ".xml",
]);
const MAX_FILE_SIZE = 512 * 1024;
const MAX_FILES = 5;

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
const uploadZone = $("#upload-zone");
const fileInput = $("#file-input");
const browseBtn = $("#browse-btn");
const fileListEl = $("#file-list");
const uploadErrorEl = $("#upload-error");
const previewIntent = $("#preview-intent");
const previewWorkflow = $("#preview-workflow");
const previewRuntime = $("#preview-runtime");
const previewLlm = $("#preview-llm");
const previewRules = $("#preview-rules");

/** @type {{ id: string, name: string, text: string, size: number }[]} */
let uploadedFiles = [];
let sessionId = localStorage.getItem(STORAGE_KEY);
let sessionConfig = {};
let busy = false;
let activeTurnId = null;
let activeWorkflowId = null;

function stepDetailHref(stepId) {
  if (!activeWorkflowId || !stepId) return null;
  const url = new URL("/step.html", window.location.origin);
  url.searchParams.set("workflow_id", activeWorkflowId);
  url.searchParams.set("step", stepId);
  return url.toString();
}

function appendStepDetailLink(li, stepId) {
  const href = stepDetailHref(stepId);
  if (!href) return;
  const link = document.createElement("a");
  link.className = "step-detail-link";
  link.href = href;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.title = "Chi tiết step (tab mới)";
  link.setAttribute("aria-label", `Chi tiết ${NODE_LABELS[stepId] || stepId}`);
  link.textContent = "↗";
  li.appendChild(link);
}

function createTimelineItem(stepId, status) {
  const li = document.createElement("li");
  li.className = statusClass(status);
  li.dataset.node = stepId;
  const dot = document.createElement("span");
  dot.className = "dot";
  const label = document.createElement("span");
  label.className = "step-label";
  label.textContent = NODE_LABELS[stepId] || stepId;
  li.append(dot, label);
  appendStepDetailLink(li, stepId);
  return li;
}
let previewTimer = null;

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

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  return `${(n / 1024).toFixed(1)} KB`;
}

function fileExtension(name) {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i).toLowerCase() : "";
}

function showUploadError(msg) {
  uploadErrorEl.textContent = msg;
  uploadErrorEl.classList.remove("hidden");
}

function clearUploadError() {
  uploadErrorEl.textContent = "";
  uploadErrorEl.classList.add("hidden");
}

function renderFileList() {
  fileListEl.replaceChildren();
  if (!uploadedFiles.length) {
    uploadZone.classList.remove("has-files");
    return;
  }
  uploadZone.classList.add("has-files");
  for (const file of uploadedFiles) {
    const li = document.createElement("li");
    li.className = "file-chip";
    const name = document.createElement("span");
    name.className = "file-name";
    name.textContent = file.name;
    const meta = document.createElement("span");
    meta.className = "file-meta";
    meta.textContent = formatBytes(file.size);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "file-remove";
    remove.setAttribute("aria-label", `Remove ${file.name}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      uploadedFiles = uploadedFiles.filter((f) => f.id !== file.id);
      renderFileList();
      scheduleRulesPreview();
    });
    li.append(name, meta, remove);
    fileListEl.appendChild(li);
  }
  if (uploadedFiles.length && !optScopeAttachment.checked) {
    optScopeAttachment.checked = true;
  }
}

async function readFileAsText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("Cannot read file"));
    reader.readAsText(file);
  });
}

async function addFiles(fileList) {
  clearUploadError();
  const incoming = Array.from(fileList || []);
  if (!incoming.length) return;

  for (const file of incoming) {
    if (uploadedFiles.length >= MAX_FILES) {
      showUploadError(`Tối đa ${MAX_FILES} file mỗi lần gửi.`);
      break;
    }
    const ext = fileExtension(file.name);
    if (ext && !ALLOWED_EXTENSIONS.has(ext)) {
      showUploadError(`Không hỗ trợ ${file.name}. Dùng: ${[...ALLOWED_EXTENSIONS].join(", ")}`);
      continue;
    }
    if (file.size > MAX_FILE_SIZE) {
      showUploadError(`${file.name} vượt 512KB.`);
      continue;
    }
    if (uploadedFiles.some((f) => f.name === file.name)) {
      showUploadError(`${file.name} đã có trong danh sách.`);
      continue;
    }
    try {
      const text = await readFileAsText(file);
      if (!text.trim()) {
        showUploadError(`${file.name} rỗng hoặc không đọc được.`);
        continue;
      }
      uploadedFiles.push({
        id: `${file.name}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        name: file.name,
        text,
        size: file.size,
      });
    } catch {
      showUploadError(`Không đọc được ${file.name}.`);
    }
  }
  renderFileList();
  scheduleRulesPreview();
}

function collectAttachments() {
  const items = uploadedFiles.map(({ name, text }) => ({ name, text }));
  const pasted = attachmentEl.value.trim();
  if (pasted) {
    items.push({ name: "paste.txt", text: pasted });
  }
  return items;
}

function buildRulesPreviewPayload() {
  const attachments = collectAttachments();
  return {
    attachments,
    needs_kb: optKb.checked,
    needs_tools: optTools.checked || optDeep.checked,
    deep_react: optDeep.checked,
    research_mode: optResearch.checked,
    query_scope: optScopeAttachment.checked ? "attachment" : "",
    max_iterations: parseInt(optMaxIter.value, 10) || 5,
    workflow_template: sessionConfig.workflow_template || null,
  };
}

function renderRulesPreview(data) {
  previewIntent.textContent = data.intent || "—";
  previewWorkflow.textContent = data.workflow_template || "—";
  previewRuntime.textContent = data.runtime || "—";
  previewLlm.textContent = data.llm_agent === "llm_senior_v1" ? "Senior (mạnh)" : "Default (nhanh)";
  previewRules.textContent = (data.rule_ids || []).join(", ") || "—";
  workflowBadgeEl.textContent = data.workflow_template || "idle";
  runtimeBadgeEl.textContent = data.runtime || "—";
}

async function refreshRulesPreview() {
  try {
    const data = await api("/v1/rules/preview", {
      method: "POST",
      body: JSON.stringify(buildRulesPreviewPayload()),
    });
    renderRulesPreview(data);
  } catch {
    previewIntent.textContent = "—";
    previewWorkflow.textContent = "—";
    previewRuntime.textContent = "—";
    previewLlm.textContent = "—";
    previewRules.textContent = "—";
  }
}

function scheduleRulesPreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(refreshRulesPreview, 250);
}

function renderEmpty() {
  messagesEl.replaceChildren();
  const el = document.createElement("div");
  el.className = "empty-state";
  el.append("Upload file, chọn rules, rồi đặt câu hỏi phân tích.");
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
    timelineEl.appendChild(createTimelineItem(id, status));
  }
  if (template) workflowBadgeEl.textContent = template;
  if (workflowStatus) workflowMetaEl.textContent = `workflow: ${workflowStatus}`;
}

function renderTemporalTimeline(query = {}, workflowStatus = null, template = null) {
  timelineEl.replaceChildren();
  const iter = query?.iteration || 0;
  for (const step of TEMPORAL_STEPS) {
    const status = query?.waiting_hitl && step === "llm_observe" ? "RUNNING" : "DONE";
    timelineEl.appendChild(createTimelineItem(step, status));
  }
  if (template) workflowBadgeEl.textContent = template;
  const parts = [`temporal: ${workflowStatus || "RUNNING"}`];
  if (iter) parts.push(`iter=${iter}`);
  if (query?.waiting_hitl) parts.push("HITL waiting");
  workflowMetaEl.textContent = parts.join(" · ");
}

function renderTimelineIdle() {
  timelineEl.replaceChildren();
  workflowMetaEl.textContent = "Chưa có workflow";
  hideHitl();
  scheduleRulesPreview();
}

function setActiveWorkflow(workflowId) {
  activeWorkflowId = workflowId || null;
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

function formatUserBubble(text, attachments) {
  const trimmed = text.trim();
  if (!attachments.length) return trimmed;
  const names = attachments.map((a) => a.name).join(", ");
  return `${trimmed}\n\n📎 ${names}`;
}

async function sendMessage(text) {
  if (busy || !text.trim()) return;
  busy = true;
  sendBtn.disabled = true;
  hideHitl();
  await syncSessionConfig();

  const attachments = collectAttachments();
  appendMessage("user", formatUserBubble(text, attachments));
  inputEl.value = "";

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
    setActiveWorkflow(accepted.workflow_id);

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

    uploadedFiles = [];
    attachmentEl.value = "";
    renderFileList();

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
    scheduleRulesPreview();
  }
}

uploadZone.addEventListener("click", (e) => {
  if (e.target === browseBtn || e.target.closest(".file-remove")) return;
  fileInput.click();
});

browseBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  fileInput.click();
});

fileInput.addEventListener("change", () => {
  addFiles(fileInput.files);
  fileInput.value = "";
});

uploadZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  uploadZone.classList.add("dragover");
});

uploadZone.addEventListener("dragleave", () => {
  uploadZone.classList.remove("dragover");
});

uploadZone.addEventListener("drop", (e) => {
  e.preventDefault();
  uploadZone.classList.remove("dragover");
  addFiles(e.dataTransfer?.files);
});

uploadZone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    fileInput.click();
  }
});

for (const el of [optKb, optTools, optDeep, optResearch, optScopeAttachment, optMaxIter]) {
  el.addEventListener("change", scheduleRulesPreview);
}
attachmentEl.addEventListener("input", scheduleRulesPreview);

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
  scheduleRulesPreview();
});

optResearch.addEventListener("change", () => {
  if (optResearch.checked) {
    optDeep.checked = false;
    optTools.checked = false;
  }
  scheduleRulesPreview();
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
  setActiveWorkflow(null);
  uploadedFiles = [];
  attachmentEl.value = "";
  renderFileList();
  await ensureSession();
  renderEmpty();
  renderTimelineIdle();
});

async function init() {
  renderTimelineIdle();
  renderFileList();
  try {
    await ensureSession();
    await loadHistory();
    await refreshRulesPreview();
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
