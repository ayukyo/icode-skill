(() => {
  "use strict";

  const token = document.querySelector('meta[name="icode-ui-token"]').content;
  const byId = (id) => document.getElementById(id);
  const STEP_LABELS = {
    init: "确认和整理需求",
    start: "开始完整开发流程",
    fast: "快速完成低风险修改",
    log: "分析问题日志",
    plan: "制定实施计划",
    review: "审查实施方案",
    merge: "合并意见并定稿",
    code: "编码实现",
    deepcheck: "深度复检",
    audit: "最终验收",
    readme: "生成交付说明",
    patch: "追加修改",
    verify: "执行验证",
    status: "检查工单状态",
    doc: "生成工程文档",
    docx: "生成 Word 文档",
    ppt: "生成演示文稿",
    limit: "维护项目约束",
    learn: "分析可复用经验",
    bak: "备份工单",
  };
  const STATUS_LABELS = {
    init_in_progress: "需求确认中",
    log_in_progress: "日志分析中",
    log_done: "日志分析完成",
    plan_done: "计划已完成",
    review_in_progress: "方案审查中",
    review_done: "方案审查完成",
    plan_finalized: "方案已定稿",
    code_in_progress: "编码中",
    code_done: "编码完成",
    deepcheck_in_progress: "深度复检中",
    deepcheck_done: "深度复检完成",
    completed: "已完成",
    debug_in_progress: "调试中",
    debug_done: "调试完成",
    disproved: "已证伪",
    superseded: "已替代",
    unknown: "状态未知",
  };
  const JOB_STATE_LABELS = {
    running: "运行中",
    finalizing: "写入回执中",
    succeeded: "执行成功",
    failed: "执行失败",
    cancelled: "已取消",
    outcome_unknown: "结果待核实",
  };
  const DELIVERY_LABELS = {
    verified: "验证完成",
    verification_pending: "等待验证",
    blocked: "验证受阻",
    not_applicable: "无需验证",
    unknown: "尚未判定",
  };
  const stepLabel = (step) => STEP_LABELS[step] || `ICODE：${step}`;
  const statusLabel = (status) => STATUS_LABELS[status] || status || "状态未知";
  const state = {
    projects: [],
    tickets: [],
    jobs: [],
    hosts: {},
    settings: null,
    selectedProjectId: null,
    selectedTicketId: null,
    ticket: null,
    refreshing: false,
    stale: true,
    search: "",
    statusFilter: "active",
    sort: "updated_desc",
    eventCursor: 0,
    timer: null,
    eventTimer: null,
    noticeAction: null,
  };

  const setText = (id, value) => {
    byId(id).textContent = value === null || value === undefined || value === "" ? "—" : String(value);
  };

  const makeRequestId = () => {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
      return `ui-${globalThis.crypto.randomUUID()}`;
    }
    return `ui-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  };

  const api = async (path, options = {}) => {
    const headers = new Headers(options.headers || {});
    headers.set("X-ICODE-UI-Token", token);
    const response = await fetch(path, { ...options, headers });
    let payload;
    try {
      payload = await response.json();
    } catch (_error) {
      throw new Error(`HTTP ${response.status}：服务没有返回 JSON`);
    }
    if (!response.ok || !payload.ok) {
      const error = new Error(payload.error || "请求失败");
      error.code = payload.error_code || payload.error_class || "UIError";
      error.recovery_action = payload.recovery_action || "inspect";
      throw error;
    }
    return payload;
  };

  const postJson = (path, payload, method = "POST") => api(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const setConnection = (kind, text) => {
    byId("connection-state").className = `connection ${kind}`;
    setText("connection-state", text);
  };

  const showNotice = (message, kind = "", recoveryAction = null) => {
    setText("notice-banner", message);
    byId("notice-banner").className = `banner ${kind}`.trim();
    state.noticeAction = recoveryAction;
    const button = byId("notice-action");
    const labels = { refresh: "立即刷新", settings: "打开设置", inspect: "查看工单", wait: "知道了" };
    button.hidden = !recoveryAction;
    if (recoveryAction) button.textContent = labels[recoveryAction] || "处理";
  };

  const setStale = (stale, message = "") => {
    state.stale = stale;
    byId("stale-banner").hidden = !stale || !state.selectedTicketId;
    const run = byId("run-step-button");
    run.disabled = stale || !state.ticket || !state.ticket.revision || !selectedStep();
    if (message) {
      showNotice(message, "danger", "refresh");
    }
  };

  const formatTime = (iso) => {
    if (!iso) return "尚未刷新";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "刚刚刷新";
    return `刷新于 ${date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
  };

  const selectedStep = () => byId("step-select").value || "";

  const ticketMatchesStatus = (ticket) => {
    const filter = state.statusFilter;
    if (filter === "all") return true;
    if (filter === "completed") return ["completed", "debug_done"].includes(ticket.status);
    if (filter === "verification_pending") return ticket.delivery_verdict === "verification_pending";
    if (filter === "closed") return ["disproved", "superseded"].includes(ticket.verdict) || ["disproved", "superseded"].includes(ticket.status);
    return !["completed", "debug_done", "disproved", "superseded"].includes(ticket.status);
  };

  const filteredTickets = () => {
    const tickets = state.tickets.filter((ticket) => {
      if (state.selectedProjectId && ticket.project_id !== state.selectedProjectId) return false;
      if (!ticketMatchesStatus(ticket)) return false;
      if (!state.search) return true;
      const haystack = `${ticket.ticket_id} ${ticket.project_name} ${ticket.summary}`.toLocaleLowerCase();
      return haystack.includes(state.search.toLocaleLowerCase());
    });
    return tickets.sort((left, right) => {
      if (state.sort === "updated_asc") return left.updated_at.localeCompare(right.updated_at);
      if (state.sort === "project") return left.project_name.localeCompare(right.project_name, "zh-CN") || right.updated_at.localeCompare(left.updated_at);
      return right.updated_at.localeCompare(left.updated_at);
    });
  };

  const makeEmpty = (text) => {
    const item = document.createElement("p");
    item.className = "empty-state";
    item.textContent = text;
    return item;
  };

  const renderProjects = () => {
    const container = byId("project-list");
    container.replaceChildren();
    setText("project-count", state.projects.length);

    const all = document.createElement("button");
    all.type = "button";
    all.className = `nav-item ${state.selectedProjectId ? "" : "active"}`;
    const allName = document.createElement("span");
    allName.textContent = "全部项目";
    const allCount = document.createElement("span");
    allCount.className = "count";
    allCount.textContent = String(state.tickets.length);
    all.append(allName, allCount);
    all.addEventListener("click", () => {
      state.selectedProjectId = null;
      renderProjects();
      renderTickets();
    });
    container.append(all);

    state.projects.forEach((project) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `nav-item ${state.selectedProjectId === project.project_id ? "active" : ""}`;
      const name = document.createElement("span");
      name.textContent = project.name;
      const count = document.createElement("span");
      count.className = "count";
      count.textContent = String(project.ticket_count);
      button.append(name, count);
      button.addEventListener("click", () => {
        state.selectedProjectId = project.project_id;
        setText("current-project", project.name);
        renderProjects();
        renderTickets();
      });
      container.append(button);
    });
    byId("new-ticket-button").disabled = state.projects.length === 0;
    byId("welcome-new-ticket").disabled = state.projects.length === 0;
  };

  const statusClass = (status) => {
    if (status === "completed" || status === "debug_done") return "done";
    if (status === "unknown") return "blocked";
    return "active";
  };

  const renderTickets = () => {
    const container = byId("ticket-list");
    const tickets = filteredTickets();
    container.replaceChildren();
    setText("ticket-count", tickets.length);
    if (!tickets.length) {
      container.append(makeEmpty("没有匹配的工单"));
      return;
    }
    tickets.forEach((ticket) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `ticket-item ${state.selectedTicketId === ticket.ticket_id ? "active" : ""}`;
      const row = document.createElement("span");
      row.className = "ticket-row";
      const id = document.createElement("strong");
      id.textContent = ticket.ticket_id;
      const dot = document.createElement("span");
      dot.className = `mini-status ${statusClass(ticket.status)}`;
      row.append(id, dot);
      const summary = document.createElement("span");
      summary.className = "ticket-item-summary";
      summary.textContent = ticket.summary || statusLabel(ticket.status);
      button.append(row, summary);
      button.addEventListener("click", () => selectTicket(ticket.ticket_id));
      container.append(button);
    });
  };

  const renderAllowedActions = (actions) => {
    const container = byId("allowed-actions");
    container.replaceChildren();
    if (!actions.length) {
      container.append(makeEmpty("当前没有可执行动作"));
      return;
    }
    actions.forEach((action) => {
      const chip = document.createElement("span");
      chip.className = "action-chip";
      chip.textContent = `${stepLabel(action)} · /icode ${action}`;
      container.append(chip);
    });
  };

  const renderStepSelect = (ticket) => {
    const select = byId("step-select");
    const prior = select.value;
    select.replaceChildren();
    const actions = ticket.allowed_actions || [];
    actions.forEach((action) => {
      const option = document.createElement("option");
      option.value = action;
      option.textContent = stepLabel(action);
      select.append(option);
    });
    if (ticket.next_step && actions.includes(ticket.next_step)) {
      select.value = ticket.next_step;
    } else if (actions.includes(prior)) {
      select.value = prior;
    }
    const action = selectedStep();
    byId("run-step-button").textContent = action ? `开始：${stepLabel(action)}` : "当前不可执行";
    byId("run-step-button").disabled = state.stale || !action || !ticket.revision;
  };

  const renderTicket = () => {
    const ticket = state.ticket;
    byId("welcome-view").hidden = Boolean(ticket);
    byId("ticket-view").hidden = !ticket;
    if (!ticket) return;
    setText("current-project", ticket.project_name);
    setText("current-ticket", ticket.ticket_id);
    setText("ticket-title", ticket.ticket_id);
    setText("ticket-summary", ticket.summary || "暂无需求摘要");
    setText("ticket-status", statusLabel(ticket.status));
    byId("ticket-status").className = `status-pill ${statusClass(ticket.status)}`;
    const revision = ticket.revision || {};
    setText("revision-badge", `事件 ${revision.event_count || 0}`);
    const showNext = !state.settings || state.settings.show_next_step;
    byId("next-card").hidden = !showNext;
    setText("next-step", ticket.next_step ? stepLabel(ticket.next_step) : "当前没有必做下一步");
    setText("next-reason", ticket.blocked_reason ? `控制面阻断：${ticket.blocked_reason}` : "推荐动作来自控制面实时策略。");
    setText("validation-state", ticket.validation && ticket.validation.ok ? "通过" : "只读 / 需处理");
    setText("open-steps", ticket.open_execution ? ticket.open_execution.steps : 0);
    setText("open-operations", ticket.open_execution ? ticket.open_execution.operations : 0);
    setText("open-agents", ticket.open_execution ? ticket.open_execution.agents : 0);
    setText("ticket-generation", ticket.generation);
    renderCockpit(ticket.cockpit);
    renderAllowedActions(ticket.allowed_actions || []);
    renderStepSelect(ticket);
  };

  const renderCockpit = (cockpit) => {
    const progress = byId("progress-steps");
    const artifacts = byId("artifact-list");
    progress.replaceChildren();
    artifacts.replaceChildren();
    if (!cockpit) {
      setText("delivery-verdict", "只读工单暂无驾驶舱数据");
      setText("verification-summary", "暂无数据");
      artifacts.append(makeEmpty("暂无可安全展示的产物"));
      return;
    }
    (cockpit.progress || []).forEach((step) => {
      const item = document.createElement("li");
      item.className = step.state;
      item.textContent = `${step.id} · ${step.label}`;
      progress.append(item);
    });
    setText("delivery-verdict", DELIVERY_LABELS[cockpit.delivery.verdict] || cockpit.delivery.verdict);
    const verification = cockpit.verification || {};
    setText(
      "verification-summary",
      `${verification.total || 0} 次记录 · ${verification.pass || 0} 通过 · ${verification.pending || 0} 待办`,
    );
    if (!(cockpit.artifacts || []).length) {
      artifacts.append(makeEmpty("尚未生成关键产物"));
      return;
    }
    cockpit.artifacts.forEach((artifact) => {
      const item = document.createElement("li");
      const name = document.createElement("strong");
      name.textContent = artifact.name;
      const meta = document.createElement("span");
      meta.textContent = `${artifact.role} · ${artifact.size} B`;
      item.append(name, meta);
      artifacts.append(item);
    });
  };

  const renderJobs = () => {
    const container = byId("job-list");
    const jobs = state.jobs.filter((job) => !state.selectedTicketId || job.ticket_id === state.selectedTicketId);
    container.replaceChildren();
    setText("job-count", jobs.length);
    if (!jobs.length) {
      container.append(makeEmpty("这个工单还没有从 UI 发起的任务"));
      return;
    }
    jobs.forEach((job) => {
      const card = document.createElement("article");
      card.className = "job-card";
      const heading = document.createElement("div");
      heading.className = "job-heading";
      const title = document.createElement("strong");
      title.textContent = stepLabel(job.step);
      const badge = document.createElement("span");
      badge.className = `job-state ${job.state}`;
      badge.textContent = JOB_STATE_LABELS[job.state] || job.state;
      heading.append(title, badge);
      const meta = document.createElement("p");
      meta.textContent = `${job.host}${job.model ? ` · ${job.model}` : ""}`;
      card.append(heading, meta);
      if (job.last_event_message) {
        const progress = document.createElement("p");
        progress.className = "job-progress";
        progress.textContent = job.last_event_message;
        card.append(progress);
      }
      if (job.output) {
        const output = document.createElement("pre");
        output.textContent = job.output;
        card.append(output);
      }
      if (job.cancel_requested && ["running", "finalizing"].includes(job.state)) {
        const cancelling = document.createElement("p");
        cancelling.textContent = "取消请求已发送，正在等待宿主结束…";
        card.append(cancelling);
      }
      if (["running", "finalizing"].includes(job.state) && !job.cancel_requested) {
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.className = "text-button danger-text";
        cancel.textContent = "取消任务";
        cancel.disabled = job.state === "finalizing";
        cancel.addEventListener("click", () => cancelJob(job.job_id));
        card.append(cancel);
      }
      container.append(card);
    });
  };

  const renderHostSummary = () => {
    const names = [];
    if (state.hosts.codex) names.push("Codex");
    if (state.hosts.claude) names.push("Claude");
    setText("host-summary", names.length ? `${names.join(" + ")} 可用` : "未检测到 Agent 主机");
  };

  const applySettingsToForm = () => {
    const settings = state.settings;
    if (!settings) return;
    byId("setting-host").value = settings.host;
    byId("setting-model").value = settings.model;
    byId("setting-mode").value = settings.mode;
    byId("setting-port").value = String(settings.preferred_port);
    byId("setting-refresh-interval").value = String(settings.refresh_interval_seconds);
    byId("setting-fallback").checked = settings.fallback;
    byId("setting-next-step").checked = settings.show_next_step;
    document.body.dataset.mode = settings.mode;
  };

  const selectTicket = async (ticketId) => {
    state.selectedTicketId = ticketId;
    state.ticket = null;
    setStale(true);
    renderTickets();
    renderTicket();
    await refresh(true);
  };

  const refresh = async (manual = false) => {
    if (state.refreshing) return;
    state.refreshing = true;
    const button = byId("refresh-button");
    button.disabled = true;
    const original = "刷新";
    button.lastElementChild.textContent = "刷新中…";
    if (manual) showNotice("正在重新读取索引、工单、门禁和任务状态…");
    try {
      const query = state.selectedTicketId ? `?ticket_id=${encodeURIComponent(state.selectedTicketId)}` : "";
      const payload = await api(`/api/v1/refresh${query}`);
      state.projects = payload.projects;
      state.tickets = payload.tickets;
      state.jobs = payload.jobs;
      state.hosts = payload.hosts;
      state.ticket = payload.ticket;
      if (state.selectedTicketId && !state.ticket) state.selectedTicketId = null;
      setText("last-refresh", formatTime(payload.observed_at));
      setConnection("ok", "本地已连接");
      showNotice(
        payload.ticket_error
          ? payload.ticket_error.message
          : "状态已刷新，可安全操作",
        payload.ticket_error ? "warning" : "",
        payload.ticket_error ? "refresh" : null,
      );
      setStale(false);
      renderProjects();
      renderTickets();
      renderTicket();
      renderJobs();
      renderHostSummary();
    } catch (error) {
      setConnection("error", "连接或状态异常");
      setStale(true, error.message);
    } finally {
      state.refreshing = false;
      button.disabled = false;
      button.lastElementChild.textContent = original;
      scheduleRefresh();
    }
  };

  const runStep = async () => {
    const ticket = state.ticket;
    const step = selectedStep();
    if (state.stale || !ticket || !ticket.revision || !step) return;
    byId("run-step-button").disabled = true;
    showNotice(`正在启动“${stepLabel(step)}”…`);
    try {
      await postJson("/api/v1/steps/run", {
        ticket_id: ticket.ticket_id,
        step,
        note: byId("step-note").value,
        request_id: makeRequestId(),
        expected_revision: ticket.revision.token,
      });
      byId("step-note").value = "";
      setStale(true);
      await refresh(true);
    } catch (error) {
      setStale(true);
      showNotice(error.message, "danger", error.recovery_action || "inspect");
    }
  };

  const cancelJob = async (jobId) => {
    if (!globalThis.confirm("确定取消这个任务吗？已产生的宿主修改不会自动回滚，终态仍会写入控制面。")) return;
    try {
      await postJson(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, {});
      await refresh(true);
    } catch (error) {
      setStale(true);
      showNotice(error.message, "danger", error.recovery_action || "refresh");
    }
  };

  const pollJobEvents = async () => {
    if (state.eventTimer) globalThis.clearTimeout(state.eventTimer);
    try {
      const ticket = state.selectedTicketId
        ? `&ticket_id=${encodeURIComponent(state.selectedTicketId)}`
        : "";
      const payload = await api(`/api/v1/job-events?after=${state.eventCursor}${ticket}`);
      state.eventCursor = payload.cursor;
      let terminalObserved = false;
      payload.events.forEach((event) => {
        const job = state.jobs.find((item) => item.job_id === event.job_id);
        if (!job) return;
        job.last_event_message = event.message;
        job.state = event.state;
        if (["succeeded", "failed", "cancelled", "outcome_unknown"].includes(event.state)) {
          terminalObserved = true;
        }
      });
      if (payload.events.length) renderJobs();
      if (terminalObserved && !state.refreshing) await refresh(false);
    } catch (_error) {
      // 事件通道失败不覆盖主刷新错误；30 秒一致性刷新仍是安全兜底。
    } finally {
      state.eventTimer = globalThis.setTimeout(pollJobEvents, 1200);
    }
  };

  const openNewTicket = () => {
    const select = byId("new-ticket-project");
    select.replaceChildren();
    state.projects.forEach((project) => {
      const option = document.createElement("option");
      option.value = project.project_id;
      option.textContent = project.name;
      select.append(option);
    });
    if (state.selectedProjectId && state.projects.some((item) => item.project_id === state.selectedProjectId)) {
      select.value = state.selectedProjectId;
    }
    byId("new-ticket-requirement").value = "";
    if (state.projects.length) byId("new-ticket-dialog").showModal();
  };

  const closeNewTicket = () => {
    if (byId("new-ticket-dialog").open) byId("new-ticket-dialog").close();
  };

  const createTicket = async (event) => {
    event.preventDefault();
    const submit = byId("new-ticket-submit");
    submit.disabled = true;
    submit.textContent = "创建中…";
    try {
      const response = await postJson("/api/v1/tickets/create", {
        project_id: byId("new-ticket-project").value,
        requirement: byId("new-ticket-requirement").value,
        request_id: makeRequestId(),
      });
      state.selectedProjectId = response.ticket.project_id;
      state.selectedTicketId = response.ticket.ticket_id;
      closeNewTicket();
      await refresh(true);
      showNotice("工单已创建。确认需求后，可运行推荐的“制定实施计划”。");
    } catch (error) {
      setStale(true);
      showNotice(error.message, "danger", error.recovery_action || "inspect");
    } finally {
      submit.disabled = false;
      submit.textContent = "创建工单";
    }
  };

  const openSettings = () => {
    applySettingsToForm();
    byId("settings-drawer").hidden = false;
    byId("drawer-backdrop").hidden = false;
    byId("settings-button").setAttribute("aria-expanded", "true");
    globalThis.requestAnimationFrame(() => document.body.classList.add("drawer-open"));
  };

  const closeSettings = () => {
    document.body.classList.remove("drawer-open");
    byId("settings-button").setAttribute("aria-expanded", "false");
    globalThis.setTimeout(() => {
      byId("settings-drawer").hidden = true;
      byId("drawer-backdrop").hidden = true;
    }, 180);
  };

  const saveSettings = async (event) => {
    event.preventDefault();
    const payload = {
      host: byId("setting-host").value,
      model: byId("setting-model").value,
      mode: byId("setting-mode").value,
      fallback: byId("setting-fallback").checked,
      show_next_step: byId("setting-next-step").checked,
      preferred_port: Number(byId("setting-port").value),
      refresh_interval_seconds: Number(byId("setting-refresh-interval").value),
    };
    try {
      const response = await postJson("/api/v1/settings", payload, "PUT");
      state.settings = response.settings;
      applySettingsToForm();
      renderTicket();
      scheduleRefresh();
      closeSettings();
    } catch (error) {
      setStale(true);
      showNotice(error.message, "danger", error.recovery_action || "settings");
    }
  };

  const scheduleRefresh = () => {
    if (state.timer) globalThis.clearTimeout(state.timer);
    const delayMs = (state.settings ? state.settings.refresh_interval_seconds : 30) * 1000;
    state.timer = globalThis.setTimeout(() => refresh(false), delayMs);
  };

  const bootstrap = async () => {
    try {
      const payload = await api("/api/v1/bootstrap");
      state.projects = payload.projects;
      state.tickets = payload.tickets;
      state.jobs = payload.jobs;
      state.hosts = payload.hosts;
      state.settings = payload.settings;
      state.selectedProjectId = payload.initial_project_id;
      const requestedTicket = payload.initial_ticket_id
        ? payload.tickets.find((ticket) => ticket.ticket_id === payload.initial_ticket_id)
        : null;
      if (requestedTicket && !ticketMatchesStatus(requestedTicket)) {
        state.statusFilter = "all";
        byId("ticket-status-filter").value = "all";
      }
      const preferredTicket = requestedTicket || payload.tickets.find(
        (ticket) => ticket.executable && ticketMatchesStatus(ticket));
      state.selectedTicketId = (preferredTicket && preferredTicket.ticket_id) || null;
      applySettingsToForm();
      renderProjects();
      renderTickets();
      renderJobs();
      renderHostSummary();
      if (state.selectedTicketId) {
        await refresh(false);
      } else {
        setText("last-refresh", formatTime(payload.refreshed_at));
        setConnection("ok", "本地已连接");
        setStale(false);
        scheduleRefresh();
      }
      pollJobEvents();
    } catch (error) {
      setConnection("error", "启动失败");
      setStale(true, error.message);
      scheduleRefresh();
      pollJobEvents();
    }
  };

  byId("ticket-search").addEventListener("input", (event) => {
    state.search = event.target.value.trim();
    renderTickets();
  });
  byId("ticket-status-filter").addEventListener("change", (event) => {
    state.statusFilter = event.target.value;
    renderTickets();
  });
  byId("ticket-sort").addEventListener("change", (event) => {
    state.sort = event.target.value;
    renderTickets();
  });
  byId("notice-action").addEventListener("click", () => {
    const action = state.noticeAction;
    if (action === "refresh") refresh(true);
    else if (action === "settings") openSettings();
    else if (action === "inspect") byId("ticket-view").scrollIntoView({ behavior: "smooth" });
    else showNotice("可等待当前任务完成，也可以在执行记录中取消它。");
  });
  byId("refresh-button").addEventListener("click", () => refresh(true));
  byId("welcome-refresh").addEventListener("click", () => refresh(true));
  byId("new-ticket-button").addEventListener("click", openNewTicket);
  byId("welcome-new-ticket").addEventListener("click", openNewTicket);
  byId("new-ticket-close").addEventListener("click", closeNewTicket);
  byId("new-ticket-cancel").addEventListener("click", closeNewTicket);
  byId("new-ticket-form").addEventListener("submit", createTicket);
  byId("run-step-button").addEventListener("click", runStep);
  byId("step-select").addEventListener("change", () => renderStepSelect(state.ticket));
  byId("settings-button").addEventListener("click", openSettings);
  byId("settings-close").addEventListener("click", closeSettings);
  byId("drawer-backdrop").addEventListener("click", closeSettings);
  byId("settings-form").addEventListener("submit", saveSettings);
  bootstrap();
})();
