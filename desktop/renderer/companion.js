'use strict';
(() => {
  const el = (id) => document.getElementById(id);
  const bridge = window.aiNeko;
  const chat = window.aiNekoChat;
  const state = { vision: false, visionEpoch: 0, recording: null, recordingEpoch: 0, audioContext: null, voiceConfig: {}, pending: new Set(), speechTurn: null };
  const status = (message) => { el('media-status').textContent = message; };
  const abortError = () => new DOMException('Cancelled', 'AbortError');
  const errorText = (error) => error.code ? `${chat.friendlyError(error)}${error.status ? `（${error.status}）` : ''}` : (error.message || '操作失败，请重试。');

  async function voiceRequest(path, body, signal) {
    const requestId = crypto.randomUUID().replaceAll('-', '');
    state.pending.add(requestId);
    const cancel = () => { void chat.api('/api/voice/cancel', { method: 'POST', body: { request_id: requestId } }).catch(() => {}); };
    if (signal?.aborted) { state.pending.delete(requestId); throw abortError(); }
    signal?.addEventListener('abort', cancel, { once: true });
    try { return await chat.api(path, { method: 'POST', body: { ...body, request_id: requestId }, signal }); }
    finally { state.pending.delete(requestId); signal?.removeEventListener('abort', cancel); }
  }
  async function context() {
    if (!state.audioContext) state.audioContext = new AudioContext();
    const ctx = state.audioContext;
    if (typeof ctx.setSinkId === 'function') await ctx.setSinkId(el('speaker-device').value || '');
    await ctx.resume();
    return ctx;
  }
  async function playAudio(data, signal, segment, onStarted) {
    if (signal.aborted) throw abortError();
    const ctx = await context();
    const raw = atob(data.audio_base64);
    const bytes = Uint8Array.from(raw, (char) => char.charCodeAt(0));
    const buffer = await ctx.decodeAudioData(bytes.buffer);
    if (signal.aborted) throw abortError();
    return new Promise((resolve, reject) => {
      const source = ctx.createBufferSource();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      source.buffer = buffer; source.connect(analyser); analyser.connect(ctx.destination);
      const levels = new Uint8Array(analyser.fftSize);
      let frame;
      let started = false;
      let acknowledgement = Promise.resolve();
      const record = (playbackState) => {
        if (!segment?.context) return;
        const { sessionId, turnId } = segment.context;
        acknowledgement = acknowledgement.catch(() => {}).then(() => chat.api(`/api/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(turnId)}/audio`, {
          method: 'POST', body: { segment_id: segment.segment_id, state: playbackState },
        })).catch(() => {});
      };
      const meter = () => {
        analyser.getByteTimeDomainData(levels);
        const rms = Math.sqrt(levels.reduce((sum, value) => sum + ((value - 128) / 128) ** 2, 0) / levels.length);
        window.aiNekoPet?.setSpeechLevel(Math.min(1, rms * 7));
        frame = requestAnimationFrame(meter);
      };
      const cleanup = () => { cancelAnimationFrame(frame); window.aiNekoPet?.setSpeechLevel(0); source.disconnect(); analyser.disconnect(); signal.removeEventListener('abort', stop); };
      const stop = () => { source.onended = null; try { source.stop(); } catch {} if (started) record('stopped'); cleanup(); reject(abortError()); };
      source.onended = () => { record('completed'); cleanup(); resolve(); };
      signal.addEventListener('abort', stop, { once: true });
      try { source.start(); started = true; record('started'); onStarted?.(); meter(); }
      catch (error) { source.onended = null; cleanup(); reject(error); }
    });
  }
  const speech = new window.aiNekoMedia.SpeechQueue({
    synthesize: (text, signal) => voiceRequest('/api/voice/synthesize', { text }, signal),
    play: playAudio,
    onActivity: (active) => { document.body.dataset.speaking = String(active); },
    onError: (error) => status(`声音未能播放：${errorText(error)} 文字回答仍保留。`),
  });
  function stopSpeech() { state.speechTurn = null; speech.stop(); window.aiNekoPet?.setSpeechLevel(0); }
  function clearKeys() { for (const kind of ['asr', 'tts']) el(`${kind}-api-key`).value = ''; }
  function stopRecording(discard = false) {
    const capture = state.recording;
    if (!capture) return;
    if (discard) { capture.discard = true; capture.controller.abort(); state.recordingEpoch += 1; }
    clearTimeout(capture.timer);
    if (capture.recorder?.state === 'recording') capture.recorder.stop();
    capture.stream?.getTracks().forEach((track) => track.stop());
    void bridge.microphone(false);
    el('record-voice').textContent = '● 说话'; el('record-voice').setAttribute('aria-pressed', 'false');
    document.body.dataset.recording = 'false';
    if (discard) { state.recording = null; status('已停止录音。'); }
  }
  // N.E.K.O app-audio-capture.js uses attempt-local streams and a start generation.
  // Keep that cancellation boundary without importing its global capture runtime.
  async function toggleRecording() {
    if (state.recording?.recorder?.state === 'recording') { stopRecording(); return; }
    if (state.recording) stopRecording(true);
    if (!chat.canRecord()) { status('对话尚未准备好，请先连接模型。'); return; }
    stopSpeech();
    await chat.cancelTurn();
    const epoch = ++state.recordingEpoch;
    const capture = { controller: new AbortController(), discard: false, stream: null, recorder: null, chunks: [], timer: null };
    state.recording = capture;
    status('正在打开麦克风…');
    try {
      await bridge.microphone(true);
      const device = el('microphone-device').value;
      capture.stream = await navigator.mediaDevices.getUserMedia({ video: false, audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1, ...(device ? { deviceId: { exact: device } } : {}) } });
      if (capture.discard || epoch !== state.recordingEpoch) { capture.stream.getTracks().forEach((track) => track.stop()); return; }
      const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'].find((item) => MediaRecorder.isTypeSupported(item));
      capture.recorder = new MediaRecorder(capture.stream, mime ? { mimeType: mime } : undefined);
      let size = 0;
      capture.recorder.ondataavailable = (event) => {
        size += event.data.size;
        if (size > 8 * 1024 * 1024) { stopRecording(true); status('录音超过8MB，已丢弃，请缩短问题。'); }
        else if (event.data.size) capture.chunks.push(event.data);
      };
      capture.recorder.onstop = async () => {
        capture.stream.getTracks().forEach((track) => track.stop());
        if (capture.discard || epoch !== state.recordingEpoch) return;
        try {
          status('正在识别你的话…');
          const blob = new Blob(capture.chunks, { type: capture.recorder.mimeType });
          const dataURL = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onerror = reject; reader.onload = () => resolve(reader.result); reader.readAsDataURL(blob); });
          if (capture.discard || epoch !== state.recordingEpoch) return;
          const result = await voiceRequest('/api/voice/transcribe', { audio_base64: String(dataURL).split(',')[1], mime_type: blob.type }, capture.controller.signal);
          if (capture.discard || epoch !== state.recordingEpoch) return;
          const text = result.text?.trim();
          if (!text) { status('没有识别到文字，请再说一次。'); return; }
          if (state.recording === capture) state.recording = null;
          status(`听到：${text}`);
          el('speak-replies').checked = true;
          await chat.submitText(text);
        } catch (error) { if (!capture.controller.signal.aborted) status(`语音识别未完成：${errorText(error)}`); }
        finally { capture.chunks = []; if (state.recording === capture) state.recording = null; }
      };
      capture.recorder.onerror = () => { stopRecording(true); status('录音设备发生错误，请重新选择麦克风。'); };
      for (const track of capture.stream.getAudioTracks()) track.addEventListener('ended', () => {
        if (state.recording === capture && capture.recorder.state === 'recording') { stopRecording(true); status('麦克风已断开，请重新选择设备。'); }
      }, { once: true });
      capture.recorder.start(250);
      capture.timer = setTimeout(() => stopRecording(), 60000);
      el('record-voice').textContent = '■ 结束并发送'; el('record-voice').setAttribute('aria-pressed', 'true');
      document.body.dataset.recording = 'true';
      el('pet-caption').textContent = '正在听你说话';
      status('正在听你说话，再点一次结束并发送。');
      await context();
    } catch (error) {
      stopRecording(true);
      status(`无法开始录音：${error.name === 'NotAllowedError' ? '请在系统隐私设置中允许麦克风。' : errorText(error)}`);
    }
  }
  async function disableVision() {
    state.vision = false; state.visionEpoch += 1;
    el('vision-enabled').checked = false;
    el('vision-preview').removeAttribute('src'); el('vision-preview').hidden = true;
    el('vision-status').textContent = '观察已关闭'; el('vision-badge').textContent = '观察已关闭';
    await bridge.stopVision();
  }
  async function captureForTurn() {
    if (!state.vision) return null;
    const epoch = state.visionEpoch;
    let frame;
    try { frame = await bridge.captureVision(); }
    catch (error) { await disableVision(); status(`无法取得画面：${errorText(error)}`); throw error; }
    if (!state.vision || epoch !== state.visionEpoch) throw Object.assign(new Error('观察已关闭，本次问题没有发送；请重新提问。'), { code: 'vision_revoked' });
    el('vision-preview').src = frame.data_url; el('vision-preview').hidden = false;
    el('vision-status').textContent = `${frame.source_name} · ${new Date(frame.captured_at).toLocaleTimeString()} · 本轮新截图`;
    return { frame, epoch };
  }
  function frameStillAllowed(capture) { return !capture || (state.vision && capture.epoch === state.visionEpoch); }
  async function loadPersona() {
    const value = await chat.api('/api/persona');
    for (const key of ['name', 'user_name', 'traits', 'speaking_style', 'catchphrase', 'game_style']) el(`persona-${key.replaceAll('_', '-')}`).value = Array.isArray(value[key]) ? value[key].join('、') : value[key] || '';
    el('persona-status').textContent = `当前人格版本 ${value.version}`;
    const identity = document.querySelector('.identity strong'); if (identity) identity.textContent = value.name;
    const dockName = document.querySelector('.pet-name span:nth-child(2)'); if (dockName) dockName.textContent = value.name;
    window.dispatchEvent(new CustomEvent('ai-neko-persona', { detail: { name: value.name } }));
  }
  async function loadVoiceConfig() {
    state.voiceConfig = await chat.api('/api/voice/config');
    for (const key of ['asr_base_url', 'asr_model', 'tts_base_url', 'tts_model', 'tts_voice']) el(key.replaceAll('_', '-')).value = state.voiceConfig[key] || '';
    for (const kind of ['asr', 'tts']) { el(`${kind}-key-state`).textContent = state.voiceConfig[`${kind}_key_set`] ? '已设置，留空保留' : '尚未设置'; el(`clear-${kind}-key`).checked = false; }
    clearKeys();
  }
  async function loadMemories() {
    const result = await chat.api('/api/memories');
    el('memory-list').replaceChildren();
    for (const item of result.memories || []) {
      const card = document.createElement('div'); card.className = 'memory-card';
      const text = document.createElement('textarea'); text.value = item.content; text.maxLength = 1000; text.setAttribute('aria-label', '记忆内容');
      const meta = document.createElement('small'); meta.textContent = `${({ preference: '偏好', fact: '事实', event: '事件' })[item.kind] || item.kind} · ${item.source_ids?.some((id) => !id.startsWith('manual:')) ? '来自对话' : '明确保存'}`;
      const save = document.createElement('button'); save.type = 'button'; save.className = 'soft-button'; save.textContent = '保存修改';
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'text-button'; remove.textContent = '遗忘';
      save.onclick = async () => { stopSpeech(); await chat.cancelTurn(); try { await chat.api(`/api/memories/${encodeURIComponent(item.id)}`, { method: 'PUT', body: { content: text.value } }); await loadMemories(); el('memory-status').textContent = '记忆已纠正。'; } catch (error) { el('memory-status').textContent = errorText(error); } };
      remove.onclick = async () => { stopSpeech(); stopRecording(true); chat.invalidatePending(); await chat.cancelTurn(); try { await chat.api(`/api/memories/${encodeURIComponent(item.id)}`, { method: 'DELETE' }); await chat.refreshAfterForget(); await loadMemories(); el('memory-status').textContent = '已遗忘这条记忆，并刷新相关历史。'; } catch (error) { await chat.refreshAfterForget().catch(() => {}); el('memory-status').textContent = errorText(error); } };
      const evidence = document.createElement('button'); evidence.type = 'button'; evidence.className = 'text-button'; evidence.textContent = '查看依据';
      const evidenceBox = document.createElement('div'); evidenceBox.className = 'memory-evidence'; evidenceBox.hidden = true;
      evidence.onclick = async () => {
        if (!evidenceBox.hidden) { evidenceBox.hidden = true; evidence.textContent = '查看依据'; return; }
        evidence.disabled = true;
        try {
          const result = await chat.api(`/api/memories/${encodeURIComponent(item.id)}/sources`);
          evidenceBox.replaceChildren();
          for (const source of result.sources || []) {
            const label = document.createElement('small'); label.textContent = source.source_id?.startsWith('manual:') ? '明确保存的原文' : `对话依据 · ${source.turn_id || source.source_id}`;
            const quote = document.createElement('p'); quote.textContent = source.source_text || '没有可显示的来源原文。';
            evidenceBox.append(label, quote);
          }
          if (!result.sources?.length) evidenceBox.textContent = '没有可显示的来源原文。';
          evidenceBox.hidden = false; evidence.textContent = '收起依据';
        } catch (error) { el('memory-status').textContent = errorText(error); }
        finally { evidence.disabled = false; }
      };
      card.append(text, meta, save, remove, evidence, evidenceBox); el('memory-list').append(card);
    }
    if (!result.memories?.length) el('memory-list').textContent = '还没有长期记忆。';
    const config = await chat.api('/api/memory/config'); el('memory-auto-extract').checked = config.auto_extract;
  }
  el('persona-form').onsubmit = async (event) => {
    event.preventDefault();
    const payload = {};
    for (const key of ['name', 'user_name', 'traits', 'speaking_style', 'catchphrase', 'game_style']) payload[key] = el(`persona-${key.replaceAll('_', '-')}`).value.trim();
    payload.traits = payload.traits.split(/[、,，\n]/).map((item) => item.trim()).filter(Boolean);
    try { await chat.api('/api/persona', { method: 'PUT', body: payload }); await loadPersona(); el('persona-status').textContent += ' · 下一轮生效'; }
    catch (error) { el('persona-status').textContent = errorText(error); }
  };
  el('voice-form').onsubmit = async (event) => {
    event.preventDefault(); const payload = {};
    for (const key of ['asr_base_url', 'asr_model', 'tts_base_url', 'tts_model', 'tts_voice', 'asr_api_key', 'tts_api_key']) payload[key] = el(key.replaceAll('_', '-')).value.trim();
    for (const kind of ['asr', 'tts']) payload[`clear_${kind}_api_key`] = el(`clear-${kind}-key`).checked;
    try { await chat.api('/api/voice/config', { method: 'PUT', body: payload }); await loadVoiceConfig(); el('voice-config-status').textContent = '语音配置已保存，可以试听。'; }
    catch (error) { el('voice-config-status').textContent = errorText(error); }
    finally { clearKeys(); payload.asr_api_key = ''; payload.tts_api_key = ''; }
  };
  el('memory-form').onsubmit = async (event) => {
    event.preventDefault();
    try { await chat.api('/api/memories', { method: 'POST', body: { content: el('memory-content').value.trim(), kind: el('memory-kind').value } }); el('memory-content').value = ''; await loadMemories(); el('memory-status').textContent = '已保存，后续对话可以召回。'; }
    catch (error) { el('memory-status').textContent = errorText(error); }
  };
  el('memory-auto-extract').onchange = async (event) => { try { await chat.api('/api/memory/config', { method: 'PUT', body: { auto_extract: event.target.checked } }); el('memory-status').textContent = event.target.checked ? '之后的对话会自动整理记忆。' : '已关闭自动整理。'; } catch (error) { event.target.checked = !event.target.checked; el('memory-status').textContent = errorText(error); } };
  el('refresh-memories').onclick = () => loadMemories().catch((error) => { el('memory-status').textContent = errorText(error); });
  el('refresh-vision').onclick = async () => {
    try {
      await disableVision();
      const sources = await bridge.visionSources(); el('vision-source').replaceChildren(new Option('选择一个窗口或屏幕', ''));
      for (const source of sources) el('vision-source').add(new Option(`${source.kind === 'screen' ? '整个屏幕' : '窗口'} · ${source.name}`, source.id));
      el('vision-status').textContent = sources.length ? '选择后开启观察；不会自动选择整个屏幕。' : '没有可用来源，请检查系统屏幕录制权限。';
    } catch (error) { el('vision-status').textContent = errorText(error); }
  };
  el('vision-source').onchange = () => disableVision().catch(() => {});
  el('vision-enabled').onchange = async (event) => {
    if (!event.target.checked) { await disableVision(); return; }
    const epoch = ++state.visionEpoch;
    try {
      const frame = await bridge.selectVision(el('vision-source').value);
      if (epoch !== state.visionEpoch) return;
      state.vision = true; el('vision-preview').src = frame.data_url; el('vision-preview').hidden = false;
      el('vision-status').textContent = `已选：${frame.source_name}。每次提问取新图。`;
      el('vision-badge').textContent = `观察：${frame.source_name}`;
    } catch (error) { await disableVision(); el('vision-status').textContent = errorText(error); }
  };
  el('refresh-audio-devices').onclick = async () => {
    try {
      await bridge.microphone(true);
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        for (const [id, kind, label] of [['microphone-device', 'audioinput', '系统默认麦克风'], ['speaker-device', 'audiooutput', '系统默认扬声器']]) {
          el(id).replaceChildren(new Option(label, ''));
          for (const device of devices.filter((item) => item.kind === kind)) el(id).add(new Option(device.label || '未命名设备', device.deviceId));
        }
      } finally { stream.getTracks().forEach((track) => track.stop()); }
      status('声音设备列表已更新。');
    } catch (error) { status(`无法读取声音设备：${errorText(error)}`); }
    finally { void bridge.microphone(false); }
  };
  el('voice-shortcut').onchange = async (event) => { try { const enabled = await bridge.voiceShortcut(event.target.checked); event.target.checked = enabled; status(enabled ? '语音快捷键已开启。' : '快捷键未启用；如果被占用，可以使用说话按钮。'); } catch (error) { event.target.checked = false; status(errorText(error)); } };
  el('test-voice').onclick = () => { stopSpeech(); speech.append('我在这里陪你。准备好了，我们一起看看下一步怎么做。', true); };
  el('record-voice').onclick = () => void toggleRecording();
  el('stop-audio').onclick = () => { stopRecording(true); stopSpeech(); status('已停止声音。'); };
  el('speak-replies').onchange = () => { if (!el('speak-replies').checked) stopSpeech(); };
  const loadSettings = () => {
    loadPersona().catch((error) => { el('persona-status').textContent = errorText(error); });
    loadVoiceConfig().catch((error) => { el('voice-config-status').textContent = errorText(error); });
    loadMemories().catch((error) => { el('memory-status').textContent = errorText(error); });
  };
  window.addEventListener('ai-neko-settings', loadSettings);
  window.addEventListener('ai-neko-connected', loadSettings);
  window.addEventListener('pagehide', () => { stopRecording(true); stopSpeech(); void disableVision(); clearKeys(); });
  bridge?.onAction((action) => { if (action === 'voice-toggle') void toggleRecording(); });
  window.aiNekoCompanion = Object.freeze({
    captureForTurn, frameStillAllowed, stopSpeech, stopRecording, clearKeys,
    beginTurn: (id, sessionId) => { stopSpeech(); if (el('speak-replies').checked) { state.speechTurn = id; speech.begin({ sessionId, turnId: id }); } },
    text: (id, value) => { if (state.speechTurn === id && el('speak-replies').checked) speech.append(value); },
    done: (id, completed) => { if (state.speechTurn === id) { if (completed) speech.append('', true); else stopSpeech(); } },
    speakingTurn: (id) => state.speechTurn === id,
  });
})();
