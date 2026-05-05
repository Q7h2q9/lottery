// multidraw v2 front-end: project / test / draw + per-agent SSE.

const STATE_LABELS = {
  pending: "待启动", queued: "排队中", running: "运行中", retrying: "重试中",
  completed: "已完成", failed: "失败", cancelled: "已取消", cancelling: "取消中",
  skipped: "已跳过", won: "胜出", exhausted: "全部结束", done: "已结束",
};
const stateLabel = (s) => STATE_LABELS[s] || s;

// ---------- generic dialog wiring ----------
document.querySelectorAll("dialog").forEach((dlg) => {
  dlg.addEventListener("click", (e) => {
    if (e.target.closest("[data-close]")) {
      e.preventDefault();
      dlg.close();
    }
  });
});

// ---------- top-level click delegation ----------
document.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;

  if (action === "new-project") {
    document.getElementById("new-project-dialog")?.showModal();
    return;
  }

  if (action === "edit-project") {
    document.getElementById("edit-project-dialog")?.showModal();
    return;
  }

  if (action === "new-test") {
    document.getElementById("new-test-dialog")?.showModal();
    return;
  }

  if (action === "delete-project") {
    const projectId = target.dataset.project;
    if (!confirm(`确定删除项目 "${projectId}" 吗？所有测试 + 抽卡历史都会一起丢。`)) return;
    const r = await fetch(`/api/projects/${projectId}`, { method: "DELETE" });
    if (r.ok) window.location.href = "/";
    return;
  }

  if (action === "delete-test") {
    const projectId = target.dataset.project;
    const testId = target.dataset.test;
    if (!confirm(`确定删除测试 "${testId}" 吗？抽卡历史也会一起丢。`)) return;
    const r = await fetch(`/api/projects/${projectId}/tests/${testId}`, { method: "DELETE" });
    if (r.ok) window.location.href = `/projects/${projectId}`;
    return;
  }

  if (action === "cancel-draw") {
    const drawId = target.dataset.draw;
    if (!confirm("确定要取消这次抽卡吗？")) return;
    target.disabled = true;
    try {
      await fetch(`/api/draws/${drawId}/cancel`, { method: "POST" });
    } catch (err) {
      alert(`取消失败：${err.message}`);
      target.disabled = false;
    }
    return;
  }
});

// ---------- new-project form ----------
const newProjectForm = document.getElementById("new-project-form");
newProjectForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const fd = new FormData(newProjectForm);
  const payload = {
    name: fd.get("name"),
    description: fd.get("description") || null,
    base_prompt: fd.get("base_prompt"),
    format_spec: fd.get("format_spec") || null,
    working_dir: fd.get("working_dir") || ".",
    verbose_stream: fd.get("verbose_stream") === "on",
  };
  const r = await fetch("/api/projects", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) { alert(`保存失败：${await r.text()}`); return; }
  const saved = await r.json();
  window.location.href = `/projects/${saved.id}`;
});

// ---------- edit-project form ----------
const editProjectForm = document.getElementById("edit-project-form");
editProjectForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const dlg = document.getElementById("edit-project-dialog");
  const projectId = dlg.dataset.project;
  const fd = new FormData(editProjectForm);
  const payload = {
    name: fd.get("name"),
    description: fd.get("description") || null,
    base_prompt: fd.get("base_prompt"),
    format_spec: fd.get("format_spec") || null,
    working_dir: fd.get("working_dir") || ".",
    verbose_stream: fd.get("verbose_stream") === "on",
  };
  const r = await fetch(`/api/projects/${projectId}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) { alert(`保存失败：${await r.text()}`); return; }
  window.location.reload();
});

// ---------- new-test form ----------
const newTestForm = document.getElementById("new-test-form");
newTestForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const dlg = document.getElementById("new-test-dialog");
  const projectId = dlg.dataset.project;
  const fd = new FormData(newTestForm);
  const payload = {
    name: fd.get("name"),
    description: fd.get("description") || null,
    test_prompt: fd.get("test_prompt") || "",
    agent_count: parseInt(fd.get("agent_count")),
    model: fd.get("model"),
    tools: fd.get("tools"),
    cancel_policy: fd.get("cancel_policy"),
    cancel_delay_seconds: parseInt(fd.get("cancel_delay_seconds") || "0"),
    concurrency: parseInt(fd.get("concurrency") || "20"),
    retries: parseInt(fd.get("retries") || "1"),
    timeout_seconds: parseInt(fd.get("timeout_seconds") || "600"),
    manual_mode: fd.get("manual_mode") === "on",
  };
  const r = await fetch(`/api/projects/${projectId}/tests`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) { alert(`创建失败：${await r.text()}`); return; }
  const saved = await r.json();
  window.location.href = `/projects/${projectId}/tests/${saved.id}`;
});

// ---------- test page: synthesize / approve / start-draw ----------
const testPage = document.querySelector(".test-page");
if (testPage) initTestPage(testPage);

function initTestPage(page) {
  const projectId = page.dataset.projectId;
  const testId = page.dataset.testId;
  const agentCount = parseInt(page.dataset.agentCount);

  const grid = page.querySelector("#agent-edit-grid");
  const testPromptInput = document.getElementById("test-prompt-input");

  function readTestPrompt() {
    if (!testPromptInput) {
      console.error("test-prompt-input element not found!");
      return "";
    }
    return testPromptInput.value;
  }

  async function saveTestPrompt() {
    const val = readTestPrompt();
    const r = await fetch(`/api/projects/${projectId}/tests/${testId}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ test_prompt: val }),
    });
    if (!r.ok) throw new Error("保存 test_prompt 失败: " + await r.text());
    return val;
  }

  function _normalize(v) {
    // Defensive: textareas may contain literal "None" / "null" / "undefined"
    // if a previous Jinja render leaked Python's None. Treat those as empty.
    if (v === null || v === undefined) return null;
    const s = String(v).trim();
    if (!s) return null;
    if (["none", "null", "undefined"].includes(s.toLowerCase())) return null;
    return s;
  }

  function readOverrides() {
    const out = [];
    for (let i = 0; i < agentCount; i++) {
      const card = grid.querySelector(`.agent-edit-card[data-index="${i}"]`);
      out.push({
        hint: _normalize(card.querySelector(".hint-input").value),
        prompt_override: _normalize(card.querySelector(".override-input").value),
      });
    }
    return out;
  }

  function readPrompts() {
    const out = [];
    for (let i = 0; i < agentCount; i++) {
      const card = grid.querySelector(`.agent-edit-card[data-index="${i}"]`);
      out.push(card.querySelector(".prompt-input").value);
    }
    return out;
  }

  function fillPrompts(prompts) {
    for (let i = 0; i < prompts.length; i++) {
      const card = grid.querySelector(`.agent-edit-card[data-index="${i}"]`);
      if (card) card.querySelector(".prompt-input").value = prompts[i] || "";
    }
  }

  page.addEventListener("click", async (e) => {
    const target = e.target.closest("[data-action]");
    if (!target) return;
    const action = target.dataset.action;

    if (action === "save-test-prompt") {
      target.disabled = true;
      try {
        await saveTestPrompt();
        target.textContent = "已保存 ✓";
        setTimeout(() => { target.textContent = "保存题目"; target.disabled = false; }, 1500);
      } catch (err) {
        alert(`保存失败：${err.message}`);
        target.disabled = false;
      }
      return;
    }

    if (action === "save-overrides") {
      target.disabled = true;
      try {
        const overrides = readOverrides();
        const r = await fetch(`/api/projects/${projectId}/tests/${testId}`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ per_agent_overrides: overrides }),
        });
        if (!r.ok) throw new Error(await r.text());
        target.textContent = "已保存 ✓";
        setTimeout(() => { target.textContent = "保存 hint / override"; target.disabled = false; }, 1500);
      } catch (err) {
        alert(`保存失败：${err.message}`);
        target.disabled = false;
      }
      return;
    }

    if (action === "synthesize") {
      // Validate: test_prompt should not be empty (otherwise synthesis produces garbage)
      const tp = readTestPrompt().trim();
      if (!tp) {
        alert("请先填写「测试题目 / 任务描述」再生成 prompt。\n撰写 agent 需要知道具体题目才能生成有意义的 prompt。");
        if (testPromptInput) testPromptInput.focus();
        return;
      }
      // Save test_prompt + overrides first to ensure synthesis sees the latest.
      try {
        await saveTestPrompt();
        await fetch(`/api/projects/${projectId}/tests/${testId}`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ per_agent_overrides: readOverrides() }),
        });
      } catch (err) {
        alert(`保存配置失败：${err.message}`);
        return;
      }
      target.disabled = true;
      const orig = target.textContent;
      target.textContent = "撰写 agent 工作中… (≤180s)";
      try {
        const r = await fetch(`/api/projects/${projectId}/tests/${testId}/synthesize`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ timeout_seconds: 180 }),
        });
        if (!r.ok) {
          const errText = await r.text();
          throw new Error(errText);
        }
        const test = await r.json();
        if (!test.resolved_prompts || test.resolved_prompts.length === 0) {
          throw new Error("撰写 agent 返回了空结果");
        }
        fillPrompts(test.resolved_prompts);
        target.textContent = "生成完毕 ✓";
        setTimeout(() => window.location.reload(), 800);
      } catch (err) {
        alert(`生成失败：${err.message}`);
        target.textContent = orig;
        target.disabled = false;
      }
      return;
    }

    if (action === "save-prompts") {
      target.disabled = true;
      try {
        const r = await fetch(`/api/projects/${projectId}/tests/${testId}/manual-prompts`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ prompts: readPrompts() }),
        });
        if (!r.ok) throw new Error(await r.text());
        target.textContent = "已保存 ✓";
        setTimeout(() => window.location.reload(), 600);
      } catch (err) {
        alert(`保存失败：${err.message}`);
        target.disabled = false;
      }
      return;
    }

    if (action === "approve") {
      target.disabled = true;
      try {
        const r = await fetch(`/api/projects/${projectId}/tests/${testId}/approve`, {
          method: "POST",
        });
        if (!r.ok) throw new Error(await r.text());
        window.location.reload();
      } catch (err) {
        alert(`批准失败：${err.message}`);
        target.disabled = false;
      }
      return;
    }

    if (action === "revoke") {
      if (!confirm("撤销批准？之前生成的 prompts 仍保留，但需重新批准才能跑。")) return;
      const r = await fetch(`/api/projects/${projectId}/tests/${testId}/revoke`, { method: "POST" });
      if (r.ok) window.location.reload();
      return;
    }

    if (action === "start-draw") {
      target.disabled = true;
      try {
        const r = await fetch(`/api/projects/${projectId}/tests/${testId}/draws`, { method: "POST" });
        if (!r.ok) throw new Error(await r.text());
        const draw = await r.json();
        window.location.href = `/draws/${draw.draw_id}`;
      } catch (err) {
        alert(`启动失败：${err.message}`);
        target.disabled = false;
      }
      return;
    }
  });
}

// ---------- draw page: card grid + per-agent panel ----------
const drawPage = document.querySelector(".draw-page");
if (drawPage) initDrawStream(drawPage);

function initDrawStream(page) {
  const drawId = page.dataset.drawId;
  const grid = page.querySelector("#agent-grid");
  const cards = new Map();
  for (const card of grid.querySelectorAll(".agent-card")) {
    cards.set(card.dataset.node, card);
  }

  const drawStateChip = document.getElementById("draw-state");
  const winnerBanner = document.getElementById("winner-banner");
  const winnerId = document.getElementById("winner-id");
  const winnerPayload = document.getElementById("winner-payload");

  // Card click → open panel
  grid.addEventListener("click", (e) => {
    const btn = e.target.closest(".expand-btn");
    if (!btn) return;
    const card = btn.closest(".agent-card");
    if (card) openAgentPanel(drawId, card.dataset.node);
  });

  // Close button on panel
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-action='close-agent-panel']")) closeAgentPanel();
  });

  // Subscribe to draw-level SSE
  const source = new EventSource(`/api/draws/${drawId}/stream`);
  source.addEventListener("multidraw_draw", (ev) => {
    try {
      const draw = JSON.parse(ev.data);
      setDrawState(draw.state);
      if (draw.winner_node_id) showWinner(draw.winner_node_id, draw.winner_payload || "（无内容）");
    } catch {}
  });
  source.onmessage = (ev) => {
    let payload; try { payload = JSON.parse(ev.data); } catch { return; }
    handleEvent(payload);
  };
  source.onerror = () => drawStateChip?.classList.add("state-failed");

  function handleEvent(ev) {
    const type = ev.type;
    const nodeId = ev.node_id;
    if (!type) return;
    if (type === "node_started") updateCard(nodeId, "running");
    else if (type === "node_retrying") updateCard(nodeId, "retrying");
    else if (type === "node_completed") {
      const success = ev.success ?? ev.node?.success;
      if (success) {
        updateCard(nodeId, "won");
        showWinner(nodeId, (ev.output || "").slice(0, 4000));
      } else {
        updateCard(nodeId, ev.status || "completed");
      }
    } else if (type === "node_failed") updateCard(nodeId, "failed");
    else if (type === "node_cancelled") updateCard(nodeId, "cancelled");
    else if (type === "node_skipped") updateCard(nodeId, "skipped");
    else if (type === "run_cancelling") setDrawState("cancelling");
    else if (type === "run_completed") setDrawState("done");
  }

  function updateCard(nodeId, state) {
    const card = cards.get(nodeId);
    if (!card) return;
    card.classList.remove(...Array.from(card.classList).filter((c) => c.startsWith("state-")));
    card.classList.add(`state-${state}`);
    const chip = card.querySelector(".chip.state");
    if (chip) {
      chip.classList.remove(...Array.from(chip.classList).filter((c) => c.startsWith("state-")));
      chip.classList.add("state", `state-${state}`);
      chip.textContent = stateLabel(state);
    }
  }

  function setDrawState(state) {
    if (!drawStateChip) return;
    drawStateChip.classList.remove(...Array.from(drawStateChip.classList).filter((c) => c.startsWith("state-")));
    drawStateChip.classList.add("state", `state-${state}`);
    drawStateChip.textContent = stateLabel(state);
  }

  function showWinner(nodeId, payload) {
    if (!winnerBanner) return;
    winnerBanner.classList.remove("hidden");
    winnerBanner.classList.add("gold");
    if (winnerId) winnerId.textContent = nodeId;
    if (winnerPayload) winnerPayload.textContent = payload;
    page.classList.add("dim");
  }
}

// ---------- agent-side panel: per-agent SSE ----------
let agentPanelSource = null;

function openAgentPanel(drawId, agentId) {
  const panel = document.getElementById("agent-panel");
  if (!panel) return;
  closeAgentPanel(); // tear down previous

  panel.classList.remove("hidden");
  document.getElementById("agent-panel-title").textContent = agentId;
  document.getElementById("agent-panel-state").textContent = "连接中…";
  const events = document.getElementById("agent-panel-events");
  events.innerHTML = "";

  agentPanelSource = new EventSource(`/api/draws/${drawId}/agents/${agentId}/stream`);
  agentPanelSource.onmessage = (ev) => {
    let payload; try { payload = JSON.parse(ev.data); } catch { return; }
    appendAgentEvent(events, payload);
  };
  agentPanelSource.addEventListener("agent_stream_end", () => {
    document.getElementById("agent-panel-state").textContent = "已结束";
    agentPanelSource?.close();
    agentPanelSource = null;
  });
  agentPanelSource.onerror = () => {
    document.getElementById("agent-panel-state").textContent = "连接断开";
  };
  document.getElementById("agent-panel-state").textContent = "实时";
}

function closeAgentPanel() {
  if (agentPanelSource) {
    agentPanelSource.close();
    agentPanelSource = null;
  }
  document.getElementById("agent-panel")?.classList.add("hidden");
}

function appendAgentEvent(container, payload) {
  const type = payload.type || "?";
  const summary = payload.summary || "";
  const cls = classifyEvent(type);
  const div = document.createElement("div");
  div.className = `event ${cls}${payload.replay ? " replay" : ""}`;

  const head = document.createElement("div");
  head.innerHTML = `<span class="ev-type">${type}</span>`;
  div.appendChild(head);

  if (summary) {
    const pre = document.createElement("pre");
    pre.textContent = summary.length > 4000 ? summary.slice(0, 4000) + "…" : summary;
    div.appendChild(pre);
  } else if (type === "tool_execution_start") {
    const pre = document.createElement("pre");
    pre.textContent = `→ ${payload.raw?.toolName || ""}\n${JSON.stringify(payload.raw?.args || {}, null, 2).slice(0, 800)}`;
    div.appendChild(pre);
  } else if (type === "tool_execution_end") {
    const pre = document.createElement("pre");
    pre.textContent = `← ${payload.raw?.toolName || ""}${payload.raw?.isError ? " [error]" : ""}\n${JSON.stringify(payload.raw?.result || "", null, 2).slice(0, 800)}`;
    div.appendChild(pre);
  }

  container.appendChild(div);
  // auto-scroll only if user is at bottom
  if (container.scrollHeight - container.scrollTop - container.clientHeight < 80) {
    container.scrollTop = container.scrollHeight;
  }
}

function classifyEvent(type) {
  if (type.startsWith("message_")) return "message";
  if (type.startsWith("tool_execution_")) return "tool";
  if (type === "thinking") return "thinking";
  return "lifecycle";
}
