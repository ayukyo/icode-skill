(() => {
  "use strict";

  const token = document.querySelector('meta[name="icode-ui-token"]').content;
  const byId = (id) => document.getElementById(id);
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
    timer: null,
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
      throw new Error(`${payload.error_class || "UIError"}：${payload.error || "请求失败"}`);
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

  const setStale = (stale, message = "") => {
    state.stale = stale;
    byId("stale-banner").hidden = !stale || !state.selectedTicketId;
    const run = byId("run-step-button");
    run.disabled = stale || !state.ticket || !state.ticket.revision || !selectedStep();
    if (message) {
      setText("notice-banner", message);
      byId("notice-banner").className = "banner danger";
    }
  };

  const formatTime = (iso) => {
    if (!iso) return "尚未刷新";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "刚刚刷新";
    return `刷新于 ${date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
  };

  const selectedStep = () => byId("step-select").value || "";

  const filteredTickets = () => state.tickets.filter((ticket) => {
    if (state.selectedProjectId && ticket.project_id !== state.selectedProjectId) return false;
    if (!state.search) return true;
    const haystack = `${ticket.ticket_id} ${ticket.project_name} ${ticket.summary}`.toLocaleLowerCase();
    return haystack.includes(state.search.toLocaleLowerCase());
  });

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
      summary.textContent = ticket.summary || ticket.status;
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
      chip.textContent = `/icode ${action}`;
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
      option.textContent = `/icode ${action}`;
      select.append(option);
    });
    if (ticket.next_step && actions.includes(ticket.next_step)) {
      select.value = ticket.next_step;
    } else if (actions.includes(prior)) {
      select.value = prior;
    }
    const action = selectedStep();
    byId("run-step-button").textContent = action ? `运行 /icode ${action}` : "当前不可执行";
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
    setText("ticket-status", ticket.status);
    byId("ticket-status").className = `status-pill ${statusClass(ticket.status)}`;
    const revision = ticket.revision || {};
    setText("revision-badge", `事件 ${revision.event_count || 0}`);
    const showNext = !state.settings || state.settings.show_next_step;
    byId("next-card").hidden = !showNext;
    setText("next-step", ticket.next_step ? `/icode ${ticket.next_step}` : "当前没有必做下一步");
    setText("next-reason", ticket.blocked_reason ? `控制面阻断：${ticket.blocked_reason}` : "推荐动作来自控制面实时策略。");
    setText("validation-state", ticket.validation && ticket.validation.ok ? "通过" : "只读 / 需处理");
    setText("open-steps", ticket.open_execution ? ticket.open_execution.steps : 0);
    setText("open-operations", ticket.open_execution ? ticket.open_execution.operations : 0);
    setText("open-agents", ticket.open_execution ? ticket.open_execution.agents : 0);
    setText("ticket-generation", ticket.generation);
    renderAllowedActions(ticket.allowed_actions || []);
    renderStepSelect(ticket);
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
      title.textContent = `/icode ${job.step}`;
      const badge = document.createElement("span");
      badge.className = `job-state ${job.state}`;
      badge.textContent = job.state === "finalizing" ? "写入回执中" : job.state;
      heading.append(title, badge);
      const meta = document.createElement("p");
      meta.textContent = `${job.host}${job.model ? ` · ${job.model}` : ""}`;
      card.append(heading, meta);
      if (job.output) {
        const output = document.createElement("pre");
        output.textContent = job.output;
        card.append(output);
      }
      if (["running", "finalizing"].includes(job.state)) {
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
    if (manual) setText("notice-banner", "正在重新读取索引、工单、门禁和任务状态…");
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
      byId("notice-banner").className = payload.ticket_error ? "banner warning" : "banner";
      setText(
        "notice-banner",
        payload.ticket_error
          ? payload.ticket_error.message
          : "状态已刷新，可安全操作",
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
    setText("notice-banner", `正在启动 /icode ${step}…`);
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
      setStale(true, error.message);
    }
  };

  const cancelJob = async (jobId) => {
    try {
      await postJson(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, {});
      await refresh(true);
    } catch (error) {
      setStale(true, error.message);
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
      setText("notice-banner", "工单已创建。确认需求后，可运行推荐的 plan 步骤。");
    } catch (error) {
      setStale(true, error.message);
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
      setStale(true, error.message);
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
      const preferredTicket = payload.tickets.find((ticket) => ticket.executable);
      state.selectedTicketId = payload.initial_ticket_id || (preferredTicket && preferredTicket.ticket_id) || null;
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
    } catch (error) {
      setConnection("error", "启动失败");
      setStale(true, error.message);
      scheduleRefresh();
    }
  };

  byId("ticket-search").addEventListener("input", (event) => {
    state.search = event.target.value.trim();
    renderTickets();
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
