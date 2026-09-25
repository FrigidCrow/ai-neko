"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const elements = {
    sidebar: byId("sidebar"), scrim: byId("sidebar-scrim"), menu: byId("toggle-sidebar"),
    sessionList: byId("session-list"), title: byId("conversation-title"),
    welcome: byId("welcome"), messages: byId("messages"), scroll: byId("conversation-scroll"),
    input: byId("message-input"), send: byId("send-message"), cancel: byId("cancel-turn"),
    status: byId("turn-status"), notice: byId("notice"), noticeText: byId("notice-text"),
    noticeAction: byId("notice-action"), settings: byId("settings-dialog"),
    settingsForm: byId("settings-form"), settingsMessage: byId("settings-message"),
    modelUrl: byId("model-base-url"), model: byId("model-name"), modelKey: byId("model-api-key"),
    searchUrl: byId("search-base-url"), searchKey: byId("search-api-key"),
  };
  const TOKEN_STORAGE = "ai-neko.local-token";
  const terminalStatuses = new Set(["completed", "complete", "done", "cancelled", "canceled", "failed", "error", "interrupted"]);
  const state = {
    token: "", connected: false, stopped: false, config: {}, sessions: [],
    sessionId: null, guide: true, generation: 0, active: null, submitting: false,
    pollController: null, noticeAction: null, pendingRequest: null,
  };

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function storedToken() {
    try { return sessionStorage.getItem(TOKEN_STORAGE) || ""; } catch { return ""; }
  }

  function saveToken(token) {
    state.token = token;
    try {
      if (token) sessionStorage.setItem(TOKEN_STORAGE, token);
      else sessionStorage.removeItem(TOKEN_STORAGE);
    } catch { /* Storage may be unavailable; this tab can still use its in-memory token. */ }
  }

  function friendlyError(error) {
    const messages = {
      unauthorized: "连接凭据已失效，请重新通过 ai-neko 启动入口打开网页。",
      config_required: "请先在设置中填写模型地址、模型名称和 API Key。",
      model_not_configured: "请先在设置中填写模型地址、模型名称和 API Key。",
      model_key_missing: "请先配置模型 API Key。本机运行的模型可以不填 Key。",
      search_key_missing: "请先在设置中填写 Tavily API Key。",
      search_not_configured: "查攻略需要搜索服务，请先在设置中填写 Tavily API Key。",
      authentication_failed: "服务鉴权失败，请检查对应供应商的 API Key。",
      rate_limited: "服务请求受限，请稍后重试。",
      provider_http_error: "供应商返回错误，请检查配置或稍后重试。",
      model_timeout: "模型响应超时，请稍后重试或换用其他模型。",
      timeout: "服务响应超时，请稍后重试。",
      network_error: "无法连接供应商，请检查 API 地址和网络连接。",
      generation_failed: "这次回答未能完成，请检查模型设置后重试。",
      process_interrupted: "上次程序异常退出，本轮已停止；已经收到的内容保留在这里。",
      blocked_endpoint: "服务地址不可访问，请检查供应商地址或本机模型地址。",
      output_limit: "模型输出达到长度上限，请缩小问题范围后重试。",
      stream_interrupted: "模型连接中断，请重试。已收到的内容保留在这里。",
      invalid_response: "模型返回格式不受支持，请检查 OpenAI 兼容接口设置。",
      turn_active: "这个对话仍在生成回答，请等待完成或先停止生成。",
      busy: "这个对话仍在生成回答，请等待完成或先停止生成。",
      not_found: "没有找到这条对话，请尝试新建对话。",
      invalid_config: "设置格式有误，请检查 API 地址和模型名称。",
      network: "暂时无法连接本地服务。请确认 ai-neko 仍在运行，再重试连接。",
    };
    return messages[error.code] || "操作未完成，请稍后重试。如果问题持续，请重新启动 ai-neko。";
  }

  async function api(path, { method = "GET", body, signal, anonymous = false } = {}) {
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (signal?.aborted) controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const headers = { Accept: "application/json" };
      if (!anonymous) headers.Authorization = `Bearer ${state.token}`;
      if (body !== undefined) headers["Content-Type"] = "application/json";
      const response = await fetch(path, {
        method, headers, body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal, cache: "no-store", credentials: "same-origin", redirect: "error",
      });
      const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = data.detail || data.error || data;
        const error = new Error("API request failed");
        error.status = response.status;
        error.code = response.status === 401 ? "unauthorized" :
          (typeof detail === "object" && detail.code) || data.code ||
          (response.status === 404 ? "not_found" : response.status === 409 ? "turn_active" : "request_failed");
        throw error;
      }
      return data;
    } catch (error) {
      if (signal?.aborted) throw new DOMException("Navigation cancelled", "AbortError");
      if (!error.code || error.name === "AbortError" || error instanceof TypeError) error.code = "network";
      throw error;
    } finally {
      clearTimeout(timeout);
      signal?.removeEventListener("abort", abort);
    }
  }

  function showNotice(message, { error = false, label = "", action = null } = {}) {
    elements.noticeText.textContent = message;
    elements.notice.hidden = !message;
    elements.notice.classList.toggle("is-error", error);
    elements.noticeAction.hidden = !label || !action;
    elements.noticeAction.textContent = label;
    state.noticeAction = action;
  }

  function connectionState(connected, label) {
    state.connected = connected;
    byId("connection-label").textContent = label;
    byId("connection-dot").classList.toggle("is-connected", connected);
    byId("connection-dot").classList.toggle("is-error", !connected);
    updateComposer();
  }

  function configValue(...keys) {
    for (const key of keys) if (state.config[key] !== undefined) return state.config[key];
    return undefined;
  }

  function modelReady() {
    const modelURL = safeWebURL(configValue("model_base_url"));
    const localModel = modelURL && (["localhost", "[::1]"].includes(modelURL.hostname) || /^127(?:\.\d{1,3}){3}$/.test(modelURL.hostname));
    return Boolean(configValue("model_base_url") && configValue("model") &&
      (localModel || configValue("model_key_set", "model_api_key_set", "model_configured")));
  }

  function searchReady() {
    return Boolean(configValue("search_base_url") &&
      configValue("search_key_set", "search_api_key_set", "search_configured"));
  }

  function showConfigurationNotice() {
    byId("welcome-settings").hidden = modelReady() && searchReady();
    if (!modelReady()) {
      showNotice("先连接一个对话模型，就可以开始使用。", { label: "前往设置", action: openSettings });
    } else if (state.guide && !searchReady()) {
      showNotice("查攻略还需要 Tavily 搜索；也可以切换到日常聊天。", { label: "设置搜索", action: openSettings });
    } else showNotice("");
  }

  function setGuide(guide) {
    state.guide = guide;
    byId("mode-guide").classList.toggle("is-active", guide);
    byId("mode-chat").classList.toggle("is-active", !guide);
    byId("mode-guide").setAttribute("aria-pressed", String(guide));
    byId("mode-chat").setAttribute("aria-pressed", String(!guide));
    byId("mode-hint").textContent = guide ? "联网搜索 · 附来源" : "轻松聊聊 · 不联网搜索";
    elements.input.placeholder = guide ? "告诉我游戏、平台和版本，或直接说说你遇到的问题…" : "今天想聊些什么？";
    if (state.connected) showConfigurationNotice();
  }

  function updateComposer() {
    const busy = Boolean(state.active || state.submitting);
    elements.send.disabled = !state.connected || busy || !elements.input.value.trim();
    elements.send.hidden = Boolean(state.active);
    elements.cancel.hidden = !state.active;
    elements.cancel.disabled = !state.active || Boolean(state.active?.cancelling);
    elements.messages.setAttribute("aria-busy", String(busy));
    elements.input.disabled = state.stopped || state.submitting;
    byId("new-session").disabled = state.submitting || state.stopped;
  }

  function resizeInput() {
    elements.input.style.height = "auto";
    elements.input.style.height = `${Math.min(elements.input.scrollHeight, 190)}px`;
    updateComposer();
  }

  function nearBottom() {
    return elements.scroll.scrollHeight - elements.scroll.scrollTop - elements.scroll.clientHeight < 150;
  }

  function scrollBottom(force = false) {
    if (force || nearBottom()) elements.scroll.scrollTop = elements.scroll.scrollHeight;
  }

  function closeSidebar() {
    elements.sidebar.classList.remove("is-open");
    elements.scrim.hidden = true;
    elements.menu.setAttribute("aria-expanded", "false");
  }

  function resetView() {
    state.generation += 1;
    state.pollController?.abort();
    state.pollController = null;
    state.active = null;
    elements.status.textContent = "";
    elements.messages.replaceChildren();
    elements.messages.hidden = true;
    elements.welcome.hidden = false;
    updateComposer();
    closeSidebar();
    return state.generation;
  }

  function newConversation() {
    if (state.submitting || state.stopped) return;
    resetView();
    state.sessionId = null;
    elements.title.textContent = "新对话";
    renderSessions();
    if (state.connected) showConfigurationNotice();
    elements.input.focus();
  }

  function dateLabel(value) {
    if (!value) return "";
    const date = new Date(typeof value === "number" && value < 1e12 ? value * 1000 : value);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
  }

  function sessionId(session) { return String(session.id || session.session_id || ""); }

  function renderSessions() {
    elements.sessionList.replaceChildren();
    if (!state.sessions.length) {
      elements.sessionList.append(node("p", "session-empty", "从一个问题开始吧"));
      return;
    }
    for (const session of state.sessions) {
      const id = sessionId(session);
      if (!id) continue;
      const button = node("button", "session-item");
      button.type = "button";
      button.classList.toggle("is-current", id === state.sessionId);
      if (id === state.sessionId) button.setAttribute("aria-current", "page");
      const icon = node("span", "session-icon", "◌");
      icon.setAttribute("aria-hidden", "true");
      const text = node("span", "session-text");
      text.append(node("span", "session-name", session.title || "新对话"));
      const date = dateLabel(session.updated_at || session.created_at);
      if (date) text.append(node("span", "session-time", date));
      button.append(icon, text);
      button.addEventListener("click", () => openSession(id));
      elements.sessionList.append(button);
    }
  }

  async function refreshSessions() {
    const response = await api("/api/sessions");
    state.sessions = Array.isArray(response) ? response : response.sessions || [];
    renderSessions();
    const current = state.sessions.find((session) => sessionId(session) === state.sessionId);
    if (current) elements.title.textContent = current.title || "新对话";
  }

  function textFromTurn(turn) {
    for (const key of ["delivered_text", "output", "assistant_text", "response"]) {
      if (typeof turn[key] === "string") return turn[key];
    }
    return "";
  }

  function createTurnView(turn) {
    const container = node("article", "turn");
    const input = typeof turn.input === "string" ? turn.input : turn.text || turn.input?.text || "";
    const user = node("div", "user-message", input);
    user.setAttribute("aria-label", "你的消息");
    const assistant = node("div", "assistant-message");
    const heading = node("div", "assistant-heading");
    const avatar = node("span", "assistant-avatar", "✳");
    avatar.setAttribute("aria-hidden", "true");
    heading.append(avatar, node("span", "", "ai-neko"));
    const output = node("div", "assistant-output", textFromTurn(turn));
    const result = node("p", "turn-result");
    const sourceHeading = node("p", "sources-heading", "本次查阅的资料 · 展开可核对正文");
    sourceHeading.hidden = true;
    const sources = node("div", "source-list");
    sources.hidden = true;
    assistant.append(heading, output, result, sourceHeading, sources);
    container.append(user, assistant);
    elements.messages.append(container);
    elements.welcome.hidden = true;
    elements.messages.hidden = false;
    const view = { container, output, result, sourceHeading, sources, sourceMap: new Map(), errorMessage: turn.error ? friendlyError({ code: turn.error }) : "" };
    for (const source of turn.sources || []) updateSource(view, source);
    if (terminalStatuses.has(turn.status)) finishView(view, turn.status);
    return view;
  }

  function safeWebURL(value) {
    if (typeof value !== "string") return null;
    try {
      const url = new URL(value);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return null;
      return url;
    } catch { return null; }
  }

  function sourceStatus(source) {
    const status = String(source.status || "");
    if (["read", "ok", "success", "fetched", "read_ok", "available"].includes(status)) return "已读取正文";
    if (["blocked", "denied", "unsafe", "unavailable", "unreadable", "failed", "error", "read_failed"].includes(status)) return "正文未能读取";
    if (["snippet", "search_only", "snippet_only", "searched", "found"].includes(status)) return "仅有搜索摘要";
    if (["reading", "pending", "fetching"].includes(status)) return "正在读取正文";
    return source.text || source.content ? "已读取正文" : "仅有搜索摘要";
  }

  function updateSource(view, source) {
    if (!source || typeof source !== "object") return;
    const id = String(source.id || source.url || view.sourceMap.size + 1);
    const previous = view.sourceMap.get(id);
    const details = node("details", "source-card");
    details.open = previous?.open || false;
    const url = safeWebURL(source.url);
    const summary = node("summary", "source-summary");
    const titleRow = node("span", "source-title-row");
    titleRow.append(node("span", "source-number", /^S?\d+$/.test(id) ? id : String(view.sourceMap.size + 1)));
    titleRow.append(node("span", "source-title", source.title || url?.hostname || "来源页面"));
    const chevron = node("span", "source-chevron", "›");
    chevron.setAttribute("aria-hidden", "true");
    titleRow.append(chevron);
    const status = sourceStatus(source);
    summary.append(titleRow, node("span", "source-meta", [url?.hostname || "链接不可用", status].join(" · ")));
    const body = node("div", "source-details");
    body.append(node("p", "source-status", status));
    const readText = source.text || source.content || "";
    const snippet = source.snippet || "";
    if (readText) body.append(node("p", "source-excerpt", readText));
    else if (snippet) body.append(node("p", "source-excerpt", `搜索摘要：${snippet}`));
    else body.append(node("p", "", "此来源暂时没有可显示的正文或摘要。"));
    if (source.error) body.append(node("p", "", "该网页正文读取受限或失败；不能仅凭搜索摘要确认完整内容。"));
    const sourceDate = (value) => {
      if (!value) return "";
      if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) return value.replaceAll("-", "/");
      const parsed = new Date(value);
      return Number.isNaN(parsed.getTime()) ? "" : parsed.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
    };
    const contentDate = sourceDate(source.content_date || source.published_at);
    const retrieved = sourceDate(source.retrieved_at);
    body.append(node("p", "source-date", `内容日期：${contentDate || "未知"}${retrieved ? ` · 检索于 ${retrieved}` : ""}`));
    if (url) {
      const link = node("a", "source-link", "打开原始来源 ↗");
      link.href = url.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.referrerPolicy = "no-referrer";
      body.append(link);
    }
    details.append(summary, body);
    if (previous) previous.replaceWith(details);
    else view.sources.append(details);
    view.sourceMap.set(id, details);
    view.sources.hidden = false;
    view.sourceHeading.hidden = false;
  }

  function finishView(view, status) {
    view.output.classList.remove("is-streaming");
    const messages = {
      completed: "", complete: "", done: "", cancelled: "已停止生成，已收到的内容保留在这里。",
      canceled: "已停止生成，已收到的内容保留在这里。", failed: "这次回答未能完成，已收到的内容已保留。",
      error: "这次回答未能完成，请检查模型或搜索设置后重试。",
      interrupted: "服务曾中断；已收到的内容保留在这里，可以重新提问。",
    };
    view.result.textContent = ["failed", "error", "interrupted"].includes(status) && view.errorMessage ? view.errorMessage : messages[status] || "";
    view.result.classList.toggle("is-error", ["failed", "error", "interrupted"].includes(status));
  }

  async function openSession(id) {
    if (state.submitting || state.stopped) return;
    const generation = resetView();
    state.sessionId = id;
    elements.title.textContent = "正在载入对话…";
    elements.welcome.hidden = true;
    renderSessions();
    try {
      const response = await api(`/api/sessions/${encodeURIComponent(id)}`);
      if (generation !== state.generation) return;
      const session = response.session || response;
      elements.title.textContent = session.title || "对话";
      const turns = session.turns || response.turns || [];
      if (turns.length && typeof turns[turns.length - 1].guide === "boolean") setGuide(turns[turns.length - 1].guide);
      if (!turns.length) elements.welcome.hidden = false;
      const pending = [];
      for (const turn of turns) {
        const view = createTurnView(turn);
        const sent = Number(turn.sent_seq ?? turn.last_sequence ?? turn.ack_seq ?? 0);
        const last = Number(turn.last_seq ?? sent);
        const turnId = turn.turn_id || turn.id;
        if (turnId && sent > Number(turn.ack_seq || 0)) {
          // Loading history rendered the delivered prefix too, so it is now safe to confirm it.
          api(`/api/sessions/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}/ack`, {
            method: "POST", body: { sequence: sent },
          }).catch(() => {});
        }
        if (!terminalStatuses.has(turn.status) || last > sent) {
          if (turnId) pending.push({ turnId: String(turnId), view, sent });
        }
      }
      scrollBottom(true);
      updateComposer();
      // Completed turns may still have unseen events. Drain every turn in order, not just the active one.
      for (const turn of pending) {
        if (generation !== state.generation) return;
        await startPolling(id, turn.turnId, turn.view, turn.sent, generation);
      }
    } catch (error) {
      if (generation !== state.generation) return;
      elements.title.textContent = "对话载入失败";
      showNotice(friendlyError(error), { error: true, label: "重试", action: () => openSession(id) });
    }
  }

  function pause(milliseconds, signal) {
    return new Promise((resolve, reject) => {
      const abort = () => { clearTimeout(timer); reject(new DOMException("Cancelled", "AbortError")); };
      const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, milliseconds);
      if (signal.aborted) abort();
      else signal.addEventListener("abort", abort, { once: true });
    });
  }

  function eventSequence(event) { return Number(event.seq ?? event.sequence ?? 0); }

  function renderEvent(view, event) {
    const data = event.data && typeof event.data === "object" ? { ...event.data, ...event } : event;
    if (event.type === "text") {
      view.output.append(document.createTextNode(typeof data.text === "string" ? data.text : ""));
    } else if (event.type === "source") {
      updateSource(view, data.source);
    } else if (event.type === "status") {
      elements.status.textContent = data.message || "正在处理…";
    } else if (event.type === "error") {
      view.errorMessage = data.message || friendlyError({ code: data.code });
      view.result.textContent = view.errorMessage;
      view.result.classList.add("is-error");
    } else if (event.type === "done") {
      finishView(view, data.status || "completed");
    }
  }

  async function startPolling(id, turnId, view, initialSequence, generation) {
    state.pollController?.abort();
    const controller = new AbortController();
    state.pollController = controller;
    const active = { sessionId: id, turnId, view, cancelling: false };
    state.active = active;
    view.output.classList.add("is-streaming");
    elements.status.textContent = "正在准备回答…";
    updateComposer();
    let sequence = Number.isFinite(initialSequence) ? initialSequence : 0;
    let errors = 0;
    let finalStatus = "";
    const path = `/api/sessions/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}`;
    try {
      while (!controller.signal.aborted && generation === state.generation) {
        try {
          const response = await api(`${path}/events?after=${sequence}`, { signal: controller.signal });
          if (generation !== state.generation || controller.signal.aborted) return;
          if (errors) elements.status.textContent = "连接已恢复，正在接收回答…";
          errors = 0;
          const follow = nearBottom();
          const events = Array.isArray(response.events) ? response.events : [];
          let sawDone = false;
          for (const event of events) {
            const next = eventSequence(event);
            if (!Number.isFinite(next) || next <= sequence) continue;
            renderEvent(view, event);
            sequence = next;
            if (event.type === "done") { sawDone = true; finalStatus = event.status || event.data?.status || response.status; }
          }
          if (follow) scrollBottom(true);
          // Acknowledge only data rendered into this still-active view.
          if (events.length && generation === state.generation) {
            await api(`${path}/ack`, { method: "POST", body: { sequence }, signal: controller.signal });
          }
          const last = Number(response.last_seq ?? response.last_sequence ?? sequence);
          if ((sawDone || terminalStatuses.has(response.status)) && sequence >= last) {
            finalStatus = finalStatus || response.status;
            finishView(view, finalStatus);
            break;
          }
          await pause(150, controller.signal);
        } catch (error) {
          if (controller.signal.aborted || generation !== state.generation) return;
          if (error.code === "unauthorized" || ++errors >= 5) throw error;
          elements.status.textContent = "连接暂时中断，正在重新连接…";
          await pause(Math.min(500 * errors, 2500), controller.signal);
        }
      }
    } catch (error) {
      if (controller.signal.aborted || generation !== state.generation) return;
      view.output.classList.remove("is-streaming");
      elements.status.textContent = "接收中断；重新打开此对话可继续获取已保存的回答。";
      showNotice(friendlyError(error), { error: true, label: "重新连接", action: () => openSession(id) });
    } finally {
      if (state.active === active && generation === state.generation) {
        state.active = null;
        state.pollController = null;
        if (finalStatus) elements.status.textContent = finalStatus === "completed" ? "" : "本次生成已结束。";
        updateComposer();
        refreshSessions().catch(() => {});
      }
    }
  }

  async function sendMessage(event) {
    event.preventDefault();
    const text = elements.input.value.trim();
    if (!text || !state.connected || state.active || state.submitting || state.stopped) return;
    if (!modelReady() || (state.guide && !searchReady())) { openSettings(); return; }
    const generation = state.generation;
    const guide = state.guide;
    state.submitting = true;
    elements.status.textContent = "正在发送…";
    updateComposer();
    try {
      if (!state.sessionId) {
        const response = await api("/api/sessions", { method: "POST", body: {} });
        state.sessionId = sessionId(response.session || response);
        if (!state.sessionId) throw new Error("Missing session identifier");
      }
      const id = state.sessionId;
      const prior = state.pendingRequest;
      if (!prior || prior.sessionId !== id || prior.text !== text || prior.guide !== guide) {
        state.pendingRequest = { sessionId: id, text, guide, requestId: crypto.randomUUID().replaceAll("-", "") };
      }
      const response = await api(`/api/sessions/${encodeURIComponent(id)}/turns`, { method: "POST", body: { text, guide, request_id: state.pendingRequest.requestId } });
      const turn = response.turn || response;
      const turnId = turn.turn_id || turn.id;
      if (!turnId) throw new Error("Missing turn identifier");
      if (generation !== state.generation) return;
      state.pendingRequest = null;
      elements.input.value = "";
      resizeInput();
      const view = createTurnView({ ...turn, text, input: text });
      scrollBottom(true);
      startPolling(id, String(turnId), view, Number(turn.sent_seq || 0), generation);
      refreshSessions().catch(() => {});
    } catch (error) {
      if (generation !== state.generation) return;
      elements.status.textContent = "未能确认发送结果，输入内容已保留；重试可恢复本次回答。";
      showNotice(friendlyError(error), { error: true, label: "检查设置", action: openSettings });
    } finally {
      state.submitting = false;
      updateComposer();
    }
  }

  async function cancelTurn() {
    const active = state.active;
    if (!active || active.cancelling) return;
    active.cancelling = true;
    updateComposer();
    elements.status.textContent = "正在停止生成…";
    try {
      await api(`/api/sessions/${encodeURIComponent(active.sessionId)}/turns/${encodeURIComponent(active.turnId)}/cancel`, { method: "POST", body: {} });
      // Keep polling until the server confirms cancellation and delivers its last events.
    } catch (error) {
      if (state.active !== active) return;
      active.cancelling = false;
      updateComposer();
      showNotice(friendlyError(error), { error: true });
    }
  }

  function keyState(kind, configured) {
    byId(`${kind}-key-state`).textContent = configured ? "已设置 · 留空则保留" : "尚未设置";
    byId(`${kind}-key-state`).classList.toggle("is-set", configured);
    byId(`clear-${kind}-label`).hidden = !configured;
    byId(`clear-${kind}-key`).checked = false;
  }

  function clearKeyInputs() {
    elements.modelKey.value = "";
    elements.searchKey.value = "";
  }

  function openSettings() {
    closeSidebar();
    elements.modelUrl.value = configValue("model_base_url") || "";
    elements.model.value = configValue("model") || "";
    elements.searchUrl.value = configValue("search_base_url") || "https://api.tavily.com";
    byId("credential-note").textContent = configValue("credential_storage") === "windows_credential_manager" ?
      "API Key 使用 Windows 凭据管理器保存，不写入浏览器存储。" :
      "此平台的 Key 仅保留在当前服务进程，重启后需重新填写；环境变量中的 Key 需自行移除。不写入浏览器存储。";
    clearKeyInputs();
    keyState("model", Boolean(configValue("model_key_set", "model_api_key_set")));
    keyState("search", Boolean(configValue("search_key_set", "search_api_key_set")));
    elements.settingsMessage.textContent = state.connected ? "" : "本地服务尚未连接，请先从 ai-neko 启动入口打开此网页。";
    byId("save-settings").disabled = !state.connected;
    if (!elements.settings.open) elements.settings.showModal();
  }

  function validEndpoint(value) {
    const url = safeWebURL(value);
    return Boolean(url && !url.search && !url.hash);
  }

  async function saveSettings(event) {
    event.preventDefault();
    if (!state.connected) return;
    const modelUrl = elements.modelUrl.value.trim();
    const searchUrl = elements.searchUrl.value.trim();
    if (!validEndpoint(modelUrl) || (searchUrl && !validEndpoint(searchUrl))) {
      elements.settingsMessage.textContent = "请填写完整的 http(s) API 基础地址，不包含账号、密码、查询参数或片段。";
      return;
    }
    const payload = {
      model_base_url: modelUrl, model: elements.model.value.trim(), model_api_key: elements.modelKey.value.trim(),
      search_base_url: searchUrl, search_api_key: elements.searchKey.value.trim(),
      clear_model_api_key: byId("clear-model-key").checked,
      clear_search_api_key: byId("clear-search-key").checked,
    };
    byId("save-settings").disabled = true;
    elements.settingsMessage.textContent = "正在保存…";
    try {
      const response = await api("/api/config", { method: "PUT", body: payload });
      state.config = response.config || response;
      // The public response must never return provider credentials. Do not copy it to browser storage.
      clearKeyInputs();
      elements.settings.close();
      showConfigurationNotice();
      elements.status.textContent = "设置已保存。可以开始提问了。";
      elements.input.focus();
    } catch (error) {
      elements.settingsMessage.textContent = error.status === 422 || error.status === 400 ?
        "设置未保存。请检查地址、模型名称和 Key；新 Key 与清除选项不能同时填写。" : friendlyError(error);
    } finally {
      // Credential strings exist only for the duration of this request and the user's input.
      payload.model_api_key = "";
      payload.search_api_key = "";
      byId("save-settings").disabled = !state.connected;
    }
  }

  async function exitApp() {
    byId("confirm-exit").disabled = true;
    byId("exit-message").textContent = "正在停止本地服务…";
    try {
      await api("/shutdown", { method: "POST", body: {} });
      state.stopped = true;
      state.pollController?.abort();
      state.active = null;
      saveToken("");
      byId("exit-dialog").close();
      connectionState(false, "本地服务已退出");
      elements.status.textContent = "";
      showNotice("ai-neko 已退出，可以关闭此网页。再次使用时请重新打开应用。" );
      byId("exit-app").disabled = true;
    } catch (error) {
      byId("exit-message").textContent = friendlyError(error);
      byId("confirm-exit").disabled = false;
    }
  }

  async function connect() {
    connectionState(false, "正在连接本地服务");
    try {
      if (!state.token) throw Object.assign(new Error("Missing session token"), { code: "unauthorized" });
      const response = await api("/api/config");
      state.config = response.config || response;
      await refreshSessions();
      connectionState(true, "已连接本地服务");
      showConfigurationNotice();
    } catch (error) {
      connectionState(false, "本地服务未连接");
      if (error.code === "unauthorized") saveToken("");
      showNotice(friendlyError(error), { error: true, label: state.token ? "重试连接" : "", action: state.token ? connect : null });
    }
  }

  async function initialize() {
    const fragment = new URLSearchParams(location.hash.slice(1));
    const bootstrap = fragment.get("bootstrap");
    const directToken = fragment.get("token");
    // Remove one-time bootstrap codes before the first asynchronous request or navigation.
    if (location.hash) history.replaceState(null, "", location.pathname + location.search);
    saveToken(directToken || storedToken());
    if (bootstrap) {
      try {
        const response = await api("/api/bootstrap", { method: "POST", body: { code: bootstrap }, anonymous: true });
        if (typeof response.token !== "string" || !response.token) throw new Error("Invalid bootstrap");
        saveToken(response.token);
      } catch {
        saveToken("");
        connectionState(false, "启动连接已失效");
        showNotice("这个启动链接已使用或失效，请重新通过 ai-neko 启动入口打开网页。", { error: true });
        return;
      }
    }
    await connect();
  }

  byId("message-form").addEventListener("submit", sendMessage);
  elements.input.addEventListener("input", resizeInput);
  elements.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      if (!elements.send.disabled) byId("message-form").requestSubmit();
    }
  });
  byId("mode-guide").addEventListener("click", () => setGuide(true));
  byId("mode-chat").addEventListener("click", () => setGuide(false));
  byId("new-session").addEventListener("click", newConversation);
  byId("brand-home").addEventListener("click", (event) => { event.preventDefault(); newConversation(); });
  elements.cancel.addEventListener("click", cancelTurn);
  elements.noticeAction.addEventListener("click", () => state.noticeAction?.());
  byId("open-settings").addEventListener("click", openSettings);
  byId("welcome-settings").addEventListener("click", openSettings);
  byId("close-settings").addEventListener("click", () => elements.settings.close());
  elements.settings.addEventListener("close", clearKeyInputs);
  elements.settingsForm.addEventListener("submit", saveSettings);
  byId("exit-app").addEventListener("click", () => byId("exit-dialog").showModal());
  byId("confirm-exit").addEventListener("click", exitApp);
  elements.menu.addEventListener("click", () => {
    const open = !elements.sidebar.classList.contains("is-open");
    elements.sidebar.classList.toggle("is-open", open);
    elements.scrim.hidden = !open;
    elements.menu.setAttribute("aria-expanded", String(open));
    if (open) byId("new-session").focus();
  });
  elements.scrim.addEventListener("click", closeSidebar);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && elements.sidebar.classList.contains("is-open")) {
      closeSidebar(); elements.menu.focus();
    }
  });
  document.querySelectorAll(".starter").forEach((button) => {
    button.addEventListener("click", () => {
      setGuide(button.dataset.guide === "true");
      elements.input.value = button.dataset.prompt || "";
      resizeInput();
      elements.input.focus();
    });
  });
  window.addEventListener("pagehide", () => { clearKeyInputs(); state.pollController?.abort(); });
  initialize();
})();
