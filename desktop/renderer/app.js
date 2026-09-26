"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const elements = {

    sessionList: byId("session-list"), title: byId("conversation-title"),
    welcome: byId("welcome"), messages: byId("messages"), scroll: byId("conversation-scroll"),
    input: byId("message-input"), send: byId("send-message"), cancel: byId("cancel-turn"),
    status: byId("turn-status"), notice: byId("notice"), noticeText: byId("notice-text"),
    noticeAction: byId("notice-action"), settings: byId("settings-panel"),
    settingsForm: byId("settings-form"), settingsMessage: byId("settings-message"),
    modelUrl: byId("model-base-url"), model: byId("model-name"), modelKey: byId("model-api-key"),
    searchUrl: byId("search-base-url"), searchKey: byId("search-api-key"),
  };
  const bridge = window.aiNeko;
  const LAST_SESSION = "ai-neko.desktop.last-session";
  const terminalStatuses = new Set(["completed", "complete", "done", "cancelled", "canceled", "failed", "error", "interrupted"]);
  const state = {
    connected: false, stopped: false, initializing: true, loadingSession: false, historyError: false, config: {}, sessions: [],
    sessionId: null, guide: true, generation: 0, active: null, submitting: false,
    pollController: null, noticeAction: null, pendingRequest: null,
  };

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function friendlyError(error) {
    if (error.publicMessage) return error.publicMessage;
    const messages = {
      unauthorized: "连接已失效，请从桌宠菜单退出后重新打开 ai-neko。",
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
      vision_revoked: "观察已关闭，本次问题没有发送，请重新提问。",
      invalid_image: "截图不受支持或已过期，请重新选择窗口后再试。",
      voice_not_configured: "请先在设置中填写语音服务地址和模型。",
      asr_not_configured: "请先配置语音识别服务。",
      tts_not_configured: "请先配置语音合成服务。",
      voice_key_missing: "请填写对应语音服务的 API Key。",
      voice_invalid_response: "语音服务返回了无法使用的结果，请检查模型和接口。",
      voice_timeout: "语音服务响应超时，请重试。",
      network: "暂时无法连接本地服务。请确认 ai-neko 仍在运行，再重试连接。",
    };
    if (/^(asr|tts)_/.test(error.code || "")) {
      const kind = error.code.startsWith("asr_") ? "语音识别" : "语音合成";
      const suffix = error.code.slice(4);
      const detail = { not_configured: "服务尚未配置，请填写地址、模型和音色。", key_missing: "缺少 API Key。", authentication_failed: "鉴权失败，请检查 API Key。", rate_limited: "请求受限，请稍后重试。", timeout: "响应超时，请重试。", invalid_response: "返回了不受支持的音频或文字格式，请检查接口。", network_error: "服务无法连接，请检查地址和网络。", output_limit: "返回内容超过限制。", empty_text: "没有识别到文字，请再说一次。", http_error: "服务返回错误，请检查配置。" }[suffix];
      if (detail) return kind + detail;
    }
    return messages[error.code] || "操作未完成，请稍后重试。如果问题持续，请重新启动 ai-neko。";
  }

  async function api(path, { method = "GET", body, signal } = {}) {
    if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
    if (!bridge) throw Object.assign(new Error("Desktop bridge unavailable"), { code: "network" });
    let timer;
    let abort;
    try {
      const response = await Promise.race([
        bridge.request({ method, path, ...(body === undefined ? {} : { body }) }),
        new Promise((_, reject) => {
          abort = () => reject(new DOMException("Cancelled", "AbortError"));
          signal?.addEventListener("abort", abort, { once: true });
          timer = setTimeout(() => reject(Object.assign(new Error("API timeout"), { code: "network" })), path.startsWith("/api/voice/") ? 125000 : 30000);
        }),
      ]);
      if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
      const data = response.body || {};
      if (response.status < 200 || response.status >= 300) {
        const detail = data.detail || data.error || data;
        throw Object.assign(new Error("API request failed"), {
          status: response.status,
          publicMessage: typeof detail === "object" && typeof detail.message === "string" ? detail.message.slice(0, 500) : undefined,
          code: response.status === 401 ? "unauthorized" : (typeof detail === "object" && detail.code) || data.code ||
            (response.status === 404 ? "not_found" : response.status === 409 ? "turn_active" : "request_failed"),
        });
      }
      return data;
    } catch (error) {
      if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
      if (!error.code) error.code = "network";
      throw error;
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
    }
  }

  function setPetState(value, caption) {
    window.aiNekoPet?.setState(value);
    document.body.dataset.petState = value;
    byId("pet-caption").textContent = caption || ({ idle: "在这里陪你", thinking: "正在想…", searching: "正在查资料…", responding: "正在回复…", failed: "我还在这里" }[value] || "在这里陪你");
  }

  function rememberSession(id) {
    try { if (id) localStorage.setItem(LAST_SESSION, id); else localStorage.removeItem(LAST_SESSION); } catch { /* Optional UI preference. */ }
  }

  function showPanel(name = "chat") {
    byId("chat-panel").hidden = name !== "chat";
    byId("history-panel").hidden = name !== "history";
    byId("settings-panel").hidden = name !== "settings";
    if (name !== "settings") { clearKeyInputs(); window.aiNekoCompanion?.clearKeys(); }
    window.aiNekoPet?.refreshInteractive();
  }

  function viewVisible() { return !byId("chat-panel").hidden && document.visibilityState !== "hidden"; }

  function paint() {
    return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
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
    // The user can open settings before the backend finishes starting.
    byId("save-settings").disabled = !connected;
    if (connected && elements.settingsMessage.textContent.includes("本地服务尚未连接")) elements.settingsMessage.textContent = "";
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

  function showConfigurationNotice() {
    byId("welcome-settings").hidden = modelReady();
    if (!modelReady()) {
      showNotice("先连接一个对话模型，就可以开始使用。", { label: "前往设置", action: openSettings });
    } else showNotice("");
  }

  function setGuide(guide) {
    state.guide = guide;
    byId("mode-guide").classList.toggle("is-active", guide);
    byId("mode-chat").classList.toggle("is-active", !guide);
    byId("mode-guide").setAttribute("aria-pressed", String(guide));
    byId("mode-chat").setAttribute("aria-pressed", String(!guide));
    byId("mode-hint").textContent = guide ? "需要时查询 · 附来源" : "不联网搜索";
    elements.input.placeholder = guide ? "聊聊天，或问问当前局面；需要资料时我会查询…" : "今天想聊些什么？";
    if (state.connected) showConfigurationNotice();
  }

  function chatReady() {
    return state.connected && !state.initializing && !state.loadingSession && !state.historyError && !state.stopped;
  }

  function updateComposer() {
    const ready = chatReady();
    document.body.dataset.chatReady = String(ready);
    document.body.dataset.sessionLoading = String(state.initializing || state.loadingSession);
    const busy = Boolean(state.active || state.submitting);
    elements.send.disabled = !ready || busy || !elements.input.value.trim();
    elements.send.hidden = Boolean(state.active);
    elements.cancel.hidden = !state.active;
    elements.cancel.disabled = !state.active || Boolean(state.active?.cancelling);
    elements.messages.setAttribute("aria-busy", String(busy || state.initializing || state.loadingSession));
    elements.input.disabled = !ready || state.submitting;
    byId("new-session").disabled = !state.connected || state.initializing || state.submitting || state.stopped;
    document.querySelectorAll(".retry-turn").forEach((button) => { button.disabled = busy || !ready; });
  }

  function resizeInput() {
    elements.input.style.height = "auto";
    elements.input.style.height = `${Math.min(elements.input.scrollHeight, 90)}px`;
    updateComposer();
  }

  function nearBottom() {
    return elements.scroll.scrollHeight - elements.scroll.scrollTop - elements.scroll.clientHeight < 150;
  }

  function scrollBottom(force = false) {
    if (force || nearBottom()) elements.scroll.scrollTop = elements.scroll.scrollHeight;
  }

  function closeSidebar() { showPanel("chat"); }

  function resetView() {
    window.aiNekoCompanion?.stopSpeech();
    window.aiNekoCompanion?.stopRecording(true);
    state.generation += 1;
    state.pollController?.abort();
    state.pollController = null;
    state.active = null;
    state.loadingSession = false;
    state.historyError = false;
    elements.status.textContent = "";
    setPetState("idle");
    elements.messages.replaceChildren();
    elements.messages.hidden = true;
    elements.welcome.hidden = false;
    updateComposer();
    closeSidebar();
    return state.generation;
  }

  function newConversation() {
    if (!state.connected || state.initializing || state.submitting || state.stopped) return;
    resetView();
    state.sessionId = null;
    rememberSession(null);
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
    heading.append(avatar, node("span", "", state.personaName || "小猫"));
    const output = node("div", "assistant-output", textFromTurn(turn));
    const result = node("p", "turn-result");
    const retry = node("button", "text-button retry-turn", "重试这条消息");
    retry.type = "button";
    retry.hidden = true;
    retry.addEventListener("click", () => {
      if (state.active || state.submitting || !state.connected) return;
      showPanel("chat");
      // Retry reuses the question, not its historical search permission.
      elements.input.value = input;
      resizeInput();
      byId("message-form").requestSubmit();
    });
    const sourceHeading = node("p", "sources-heading", "本次查阅的资料 · 展开可核对正文");
    sourceHeading.hidden = true;
    const sources = node("div", "source-list");
    sources.hidden = true;
    assistant.append(heading, output, result, retry, sourceHeading, sources);
    container.append(user, assistant);
    elements.messages.append(container);
    elements.welcome.hidden = true;
    elements.messages.hidden = false;
    const view = { container, output, result, retry, sourceHeading, sources, sourceMap: new Map(), errorMessage: turn.error ? friendlyError({ code: turn.error }) : "" };
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
      const link = node("button", "source-link", "打开原始来源 ↗");
      link.type = "button";
      link.addEventListener("click", () => bridge.openExternal(url.href).catch(() => showNotice("这个来源暂时无法打开。", { error: true })));
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
    view.retry.hidden = !["failed", "error", "interrupted"].includes(status);
    const messages = {
      completed: "", complete: "", done: "", cancelled: "已停止生成，已收到的内容保留在这里。",
      canceled: "已停止生成，已收到的内容保留在这里。", failed: "这次回答未能完成，已收到的内容已保留。",
      error: "这次回答未能完成，请检查模型或搜索设置后重试。",
      interrupted: "服务曾中断；已收到的内容保留在这里，可以重新提问。",
    };
    view.result.textContent = view.errorMessage || messages[status] || "";
    view.result.classList.toggle("is-error", Boolean(view.errorMessage) || ["failed", "error", "interrupted"].includes(status));
  }

  async function openSession(id) {
    if (state.submitting || state.stopped) return;
    const generation = resetView();
    state.loadingSession = true;
    updateComposer();
    elements.status.textContent = "正在恢复这段对话…";
    state.sessionId = id;
    rememberSession(id);
    elements.title.textContent = "正在载入对话…";
    elements.welcome.hidden = true;
    renderSessions();
    try {
      const response = await api(`/api/sessions/${encodeURIComponent(id)}`);
      if (generation !== state.generation) return;
      const session = response.session || response;
      elements.title.textContent = session.title || "对话";
      const turns = session.turns || response.turns || [];
      // Historical turns record their old permission; opening them must not change
      // the visibly selected search permission for the next question.
      if (!turns.length) elements.welcome.hidden = false;
      const pending = [];
      for (const turn of turns) {
        const view = createTurnView(turn);
        const sent = Number(turn.sent_seq ?? turn.last_sequence ?? turn.ack_seq ?? 0);
        const last = Number(turn.last_seq ?? sent);
        const turnId = turn.turn_id || turn.id;
        if (turnId && sent > Number(turn.ack_seq || 0) && viewVisible()) {
          // Loading history rendered the delivered prefix too, so it is now safe to confirm it.
          paint().then(() => {
            if (generation === state.generation && viewVisible()) return api(`/api/sessions/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}/ack`, { method: "POST", body: { sequence: sent } });
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
      state.historyError = true;
      elements.title.textContent = "对话载入失败";
      elements.status.textContent = "对话尚未恢复，请重试载入或新建对话；输入内容会保留。";
      showNotice(friendlyError(error), { error: true, label: "重试", action: () => openSession(id) });
    } finally {
      if (generation === state.generation) {
        state.loadingSession = false;
        if (elements.status.textContent === "正在恢复这段对话…") elements.status.textContent = "";
        updateComposer();
      }
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
      setPetState("responding");
      view.output.dataset.lastDeltaAt = String(performance.now());
      view.output.append(document.createTextNode(typeof data.text === "string" ? data.text : ""));
      if (view.liveTurnId) window.aiNekoCompanion?.text(view.liveTurnId, typeof data.text === "string" ? data.text : "");
    } else if (event.type === "source") {
      updateSource(view, data.source);
    } else if (event.type === "tool") {
      if (data.status === "running") {
        elements.status.textContent = data.name === "read_web_page" ? "正在读取网页…" : "正在查资料…";
        setPetState("searching");
      } else if (data.status === "error") {
        if (data.error === "search_key_missing") {
          showNotice(friendlyError({ code: data.error }), { error: true, label: "设置搜索", action: openSettings });
        } else showNotice("这次资料查询未完成，回答中的信息缺口需要继续核实。", { error: true });
      }
    } else if (event.type === "status") {
      elements.status.textContent = data.message || ({ accepted: "正在想…", researching: "正在查资料…", answering: "正在组织回答…" }[data.status] || "正在想…");
      setPetState(/查|搜|读|search|read|tool/i.test(data.message || data.status || "") ? "searching" : "thinking");
    } else if (event.type === "error") {
      setPetState("failed");
      view.errorMessage = data.message || friendlyError({ code: data.code });
      view.result.textContent = view.errorMessage;
      view.result.classList.add("is-error");
    } else if (event.type === "done") {
      finishView(view, data.status || "completed");
      if (view.liveTurnId) window.aiNekoCompanion?.done(view.liveTurnId, ["completed", "complete", "done"].includes(data.status || "completed"));
    }
  }

  async function startPolling(id, turnId, view, initialSequence, generation) {
    state.pollController?.abort();
    const controller = new AbortController();
    state.pollController = controller;
    const active = { sessionId: id, turnId, view, cancelling: false };
    state.active = active;
    view.output.classList.add("is-streaming");
    elements.status.textContent = "正在想…";
    setPetState("thinking");
    updateComposer();
    let sequence = Number.isFinite(initialSequence) ? initialSequence : 0;
    let errors = 0;
    let acknowledgedSequence = -1;
    let finalStatus = "";
    const path = `/api/sessions/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}`;
    try {
      while (!controller.signal.aborted && generation === state.generation) {
        try {
          // Do not claim unseen content was displayed while the companion panel is tucked away.
          if (!viewVisible() && !active.cancelling && !window.aiNekoCompanion?.speakingTurn(view.liveTurnId)) { await pause(150, controller.signal); continue; }
          const response = await api(`${path}/events?after=${sequence}`, { signal: controller.signal });
          if (generation !== state.generation || controller.signal.aborted) return;
          if (!viewVisible() && !active.cancelling && !window.aiNekoCompanion?.speakingTurn(view.liveTurnId)) { await pause(150, controller.signal); continue; }
          if (errors) elements.status.textContent = "连接已恢复，正在接收回答…";
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
          await paint();
          if (sequence > 0 && sequence > acknowledgedSequence && generation === state.generation && viewVisible() && !controller.signal.aborted) {
            await api(`${path}/ack`, { method: "POST", body: { sequence }, signal: controller.signal });
            acknowledgedSequence = sequence;
          }
          errors = 0;
          const last = Number(response.last_seq ?? response.last_sequence ?? sequence);
          if ((sawDone || terminalStatuses.has(response.status)) && sequence >= last && (viewVisible() || active.cancelling || window.aiNekoCompanion?.speakingTurn(view.liveTurnId))) {
            finalStatus = finalStatus || response.status;
            finishView(view, finalStatus);
            if (view.liveTurnId) window.aiNekoCompanion?.done(view.liveTurnId, ["completed", "complete", "done"].includes(finalStatus));
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
        if (finalStatus) {
          elements.status.textContent = finalStatus === "completed" ? "" : finalStatus === "cancelled" ? "已停止回复。" : "这次回复未完成。";
          setPetState(["failed", "error", "interrupted"].includes(finalStatus) ? "failed" : "idle");
        } else setPetState("failed");
        updateComposer();
        refreshSessions().catch(() => {});
      }
    }
  }

  async function sendMessage(event) {
    event.preventDefault();
    const text = elements.input.value.trim();
    if (!text) return;
    if (!chatReady()) {
      elements.status.textContent = "对话还在准备中，输入内容已保留；准备好后请再发送。";
      return;
    }
    if (state.active || state.submitting || state.stopped) return;
    if (!modelReady()) {
      openSettings();
      elements.settingsMessage.textContent = "请先填写模型 API 地址、模型名称和 API Key；本机模型可以不填 Key。";
      (!configValue("model_base_url") ? elements.modelUrl : !configValue("model") ? elements.model : elements.modelKey).focus();
      return;
    }
    const generation = state.generation;
    const guide = state.guide;
    window.aiNekoCompanion?.stopSpeech();
    window.aiNekoCompanion?.stopRecording(true);
    state.submitting = true;
    elements.status.textContent = "正在发送…";
    updateComposer();
    try {
      const capture = await window.aiNekoCompanion?.captureForTurn();
      if (generation !== state.generation) return;
      if (!state.sessionId) {
        const response = await api("/api/sessions", { method: "POST", body: {} });
        state.sessionId = sessionId(response.session || response);
        if (!state.sessionId) throw new Error("Missing session identifier");
        rememberSession(state.sessionId);
      }
      if (generation !== state.generation) return;
      const id = state.sessionId;
      const prior = state.pendingRequest;
      if (!prior || prior.sessionId !== id || prior.text !== text || prior.guide !== guide) {
        state.pendingRequest = { sessionId: id, text, guide, requestId: crypto.randomUUID().replaceAll("-", "") };
      }
      if (!window.aiNekoCompanion?.frameStillAllowed(capture)) throw Object.assign(new Error("Vision revoked"), { code: "vision_revoked" });
      const response = await api(`/api/sessions/${encodeURIComponent(id)}/turns`, { method: "POST", body: { text, guide, request_id: state.pendingRequest.requestId, ...(capture?.frame ? { image: capture.frame } : {}) } });
      const turn = response.turn || response;
      const turnId = turn.turn_id || turn.id;
      if (!turnId) throw new Error("Missing turn identifier");
      if (generation !== state.generation) return;
      state.pendingRequest = null;
      showNotice("");
      elements.input.value = "";
      resizeInput();
      const view = createTurnView({ ...turn, text, input: text });
      view.liveTurnId = String(turnId);
      window.aiNekoCompanion?.beginTurn(view.liveTurnId, id);
      scrollBottom(true);
      startPolling(id, String(turnId), view, Number(turn.sent_seq || 0), generation);
      refreshSessions().catch(() => {});
    } catch (error) {
      if (generation !== state.generation) return;
      elements.status.textContent = "未能确认发送结果，输入内容已保留；重试可恢复本次回答。";
      showNotice(friendlyError(error), { error: true, label: "检查设置", action: openSettings });
    } finally {
      if (generation === state.generation) state.submitting = false;
      updateComposer();
    }
  }

  async function cancelTurn() {
    window.aiNekoCompanion?.stopSpeech();
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
      "API Key 使用 Windows 凭据管理器保存，不写入界面存储。" :
      "此平台的 Key 仅保留在当前服务进程，重启后需重新填写；环境变量中的 Key 需自行移除。不写入界面存储。";
    clearKeyInputs();
    keyState("model", Boolean(configValue("model_key_set", "model_api_key_set")));
    keyState("search", Boolean(configValue("search_key_set", "search_api_key_set")));
    elements.settingsMessage.textContent = state.connected ? "" : "本地服务尚未连接，请等待服务准备完成。";
    byId("save-settings").disabled = !state.connected;
    showPanel("settings");
    window.dispatchEvent(new Event("ai-neko-settings"));
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
      showPanel("chat");
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

  let connecting = false;
  async function connect() {
    if (connecting || state.connected) return;
    connecting = true;
    state.initializing = true;
    connectionState(false, "正在连接");
    try {
      const response = await api("/api/config");
      state.config = response.config || response;
      await refreshSessions();
      connectionState(true, "正在恢复对话…");
      window.dispatchEvent(new Event("ai-neko-connected"));
      showConfigurationNotice();
      let previous = "";
      try { previous = localStorage.getItem(LAST_SESSION) || ""; } catch { /* Optional preference. */ }
      const latest = state.sessions.find((item) => sessionId(item) === previous) || state.sessions[0];
      if (latest && !state.sessionId) await openSession(sessionId(latest));
      if (state.connected) byId("connection-label").textContent = "在你身边 · 记录保存在本机";
    } catch (error) {
      connectionState(false, "连接暂时中断");
      showNotice(friendlyError(error), { error: true, label: "重新连接", action: connect });
    } finally {
      connecting = false;
      state.initializing = false;
      updateComposer();
    }
  }

  function hostStatus(status) {
    if (status.state === "ready") connect();
    else {
      connectionState(false, status.state === "starting" ? "正在准备见面" : "服务暂时不可用");
      if (status.state === "failed" || status.state === "stopped") {
        state.pollController?.abort();
        window.aiNekoCompanion?.stopSpeech();
        window.aiNekoCompanion?.stopRecording(true);
        state.active = null;
        setPetState("failed");
        showNotice(status.message || "服务已停止，请从桌宠菜单退出后重新打开。", { error: true });
      }
    }
  }

  async function initialize() {
    if (!bridge) { hostStatus({ state: "failed", message: "桌面连接未能加载，请重新启动 ai-neko。" }); return; }
    let statusVersion = 0;
    bridge.onStatus((value) => { statusVersion += 1; hostStatus(value); });
    bridge.onAction((action) => {
      if (action === "settings") openSettings();
      if (action === "show-chat") { showPanel("chat"); elements.input.focus(); }
    });
    try {
      const preferences = await bridge.getPreferences();
      byId("pet-scale").value = preferences.scale;
      byId("scale-value").value = `${Math.round(preferences.scale * 100)}%`;
      byId("always-on-top").checked = preferences.alwaysOnTop;
      window.aiNekoPet?.setScale(preferences.scale);
      const version = statusVersion;
      const snapshot = await bridge.status();
      // A ready event can arrive while this snapshot request is in flight.
      if (version === statusVersion) hostStatus(snapshot);
    } catch { hostStatus({ state: "failed", message: "桌面连接未能加载，请重新启动 ai-neko。" }); }
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
  elements.cancel.addEventListener("click", cancelTurn);
  elements.noticeAction.addEventListener("click", () => state.noticeAction?.());
  byId("open-settings").addEventListener("click", openSettings);
  byId("welcome-settings").addEventListener("click", openSettings);
  byId("close-settings").addEventListener("click", () => showPanel("chat"));
  elements.settingsForm.addEventListener("submit", saveSettings);
  byId("exit-app").addEventListener("click", () => bridge.quit());
  byId("open-history").addEventListener("click", () => { showPanel("history"); refreshSessions().catch(() => {}); });
  byId("close-history").addEventListener("click", () => showPanel("chat"));
  byId("close-chat").addEventListener("click", () => showPanel("none"));
  byId("show-chat").addEventListener("click", () => { showPanel("chat"); elements.input.focus(); });
  byId("pet-menu").addEventListener("click", () => bridge.showMenu());
  window.addEventListener("pet-click", () => { showPanel("chat"); elements.input.focus(); });
  byId("pet-scale").addEventListener("input", (event) => {
    const scale = Number(event.target.value);
    byId("scale-value").value = `${Math.round(scale * 100)}%`;
    window.aiNekoPet?.setScale(scale);
  });
  byId("pet-scale").addEventListener("change", (event) => {
    bridge.setPreferences({ scale: Number(event.target.value) }).catch(() => { elements.settingsMessage.textContent = "角色大小未能保存，请重试。"; });
  });
  byId("always-on-top").addEventListener("change", (event) => {
    bridge.setPreferences({ alwaysOnTop: event.target.checked }).catch(() => { elements.settingsMessage.textContent = "置顶设置未能保存，请重试。"; });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !event.isComposing) { showPanel("none"); window.aiNekoPet?.endDrag(); }
  });
  document.querySelectorAll(".starter").forEach((button) => {
    button.addEventListener("click", () => {
      showPanel("chat");
      setGuide(button.dataset.guide === "true");
      elements.input.value = button.dataset.prompt || "";
      resizeInput();
      elements.input.focus();
    });
  });
  window.addEventListener("pagehide", () => { clearKeyInputs(); state.pollController?.abort(); });
  window.addEventListener("ai-neko-persona", (event) => { state.personaName = event.detail.name; });
  window.aiNekoChat = Object.freeze({ api, friendlyError, cancelTurn,
    invalidatePending: () => {
      state.generation += 1; state.pendingRequest = null; state.submitting = false;
      state.pollController?.abort();
    },
    refreshAfterForget: async () => {
      if (state.sessionId) await openSession(state.sessionId);
      await refreshSessions(); showPanel("settings");
    },
    canRecord: () => chatReady() && modelReady() && !state.submitting,
    submitText: async (text) => {
      elements.input.value = text; resizeInput();
      const deadline = Date.now() + 15000;
      while (state.active && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 100));
      if (state.active) throw Object.assign(new Error("Previous turn active"), { code: "turn_active" });
      await sendMessage({ preventDefault() {} });
    },
  });
  setGuide(state.guide);
  initialize();
})();
