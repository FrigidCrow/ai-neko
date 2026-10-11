'use strict';
(() => {
  const el = (id) => document.getElementById(id);
  const bridge = window.aiNeko;
  const chat = window.aiNekoChat;
  const state = { vision: false, visionEpoch: 0, recording: null, recordingEpoch: 0, deviceRefresh: null, audioContext: null, voiceConfig: {}, voiceConfigEpoch: 0, localVoice: {}, qwenVoice: {}, qwenAction: null, qwenOperation: null, qwenRequestId: null, qwenRetry: null, qwenEpoch: 0, qwenReleased: false, qwenReleaseNeeded: false, voicePoll: null, voiceUIEpoch: 0, voiceBusy: false, pending: new Set(), speechTurn: null, speechContext: null };
  const status = (message) => { el('media-status').textContent = message; };
  const deviceStatus = (message) => { el('audio-device-status').textContent = message; status(message); };
  const abortError = () => new DOMException('Cancelled', 'AbortError');
  function qwenErrorText(error) {
    // Render only known error codes and bounded numeric requirements. Provider
    // detail/publicMessage/exception text may contain paths or other private data.
    const preflight = state.qwenVoice.preflight || state.voiceConfig.qwen?.preflight || {};
    const minimum = (name, fallback) => Number.isSafeInteger(preflight[name]) && preflight[name] > 0 && preflight[name] <= 1024 * 1024 ? preflight[name] : fallback;
    const ram = minimum('available_ram_mib', 1536), commit = minimum('available_commit_mib', 8192), vram = minimum('available_vram_mib', 3072);
    const mib = (value) => value + ' MiB' + (value % 1024 === 0 ? '（' + value / 1024 + ' GiB）' : '');
    const hostRequirement = '当前预热保护线：可用 RAM ' + mib(ram) + '、可用系统提交空间（commit）' + mib(commit) + '。';
    const gpuRequirement = '当前预热保护线：可用显存 ' + mib(vram) + '。';
    const messages = {
      qwen_memory_pressure: '可用内存或系统提交空间不足，Qwen 未继续加载。' + hostRequirement + '请释放资源后重试。',
      qwen_gpu_memory: '可用显存不足，Qwen 未继续加载。' + gpuRequirement + '请释放显存后重试。',
      qwen_resource_exhausted: '内存、系统提交空间或显存不足，Qwen 已停止本次请求。' + hostRequirement + gpuRequirement + '请释放资源后重试。',
      qwen_gpu_unavailable: 'NVIDIA GPU 或 CUDA 当前不可用，请检查本机 Qwen 运行环境。',
      qwen_binding_invalid: 'Qwen 本机资源无效，请检查已登记的环境、模型和声音。',
      qwen_not_ready: 'Qwen 本机资源尚未准备好，请检查已登记的环境、模型和声音。',
      qwen_unsupported: 'Qwen 本地语音需要 Windows x64 与 NVIDIA CUDA 环境。',
      qwen_closed: 'Qwen 语音服务已关闭或正在释放资源，请等待清理完成后再试。',
      qwen_timeout: 'Qwen 处理超时，本次请求已停止，请稍后重试。',
      qwen_output_limit: 'Qwen 输出超过限制，请缩短文本后重试。',
      qwen_inference_failed: 'Qwen 本地语音运行失败，请检查本机资源后重试。',
      qwen_response: 'Qwen 未返回有效的语音结果，请重试。',
      network: '无法连接本地语音服务，请确认 ai-neko 仍在运行后重试。',
      unauthorized: '本地连接已失效，请重新连接 ai-neko。',
    };
    return typeof messages[error?.code] === 'string' ? messages[error.code] : 'Qwen 请求未完成，请稍后重试。';
  }
  const errorText = (error) => typeof error.code === 'string' && error.code.startsWith('qwen_') ? qwenErrorText(error) : error.code ? `${chat.friendlyError(error)}${error.status ? `（${error.status}）` : ''}` : (error.message || '操作失败，请重试。');
  const microphoneError = (error) => error.name === 'NotAllowedError' ? '本次麦克风访问未获许可，请重试。' : errorText(error);
  let playbackReceipts = Promise.resolve();
  const voiceTimings = [];
  const mediaNow = () => globalThis.performance?.now() ?? Date.now();
  const recordVoiceTiming = (value) => {
    voiceTimings.push(Object.freeze({ ...value }));
    if (voiceTimings.length > 256) voiceTimings.splice(0, voiceTimings.length - 256);
  };
  async function flushPlayback() {
    let timer;
    try { await Promise.race([playbackReceipts, new Promise((resolve) => { timer = setTimeout(resolve, 5000); })]); }
    finally { clearTimeout(timer); }
  }

  async function voiceRequest(path, body, signal) {
    const requestId = crypto.randomUUID().replaceAll('-', '');
    state.pending.add(requestId);
    const cancel = () => { void chat.api('/api/voice/cancel', { method: 'POST', body: { request_id: requestId } }).catch(() => {}); };
    if (signal?.aborted) { state.pending.delete(requestId); throw abortError(); }
    signal?.addEventListener('abort', cancel, { once: true });
    try { return await chat.api(path, { method: 'POST', body: { ...body, request_id: requestId }, signal }); }
    finally { state.pending.delete(requestId); signal?.removeEventListener('abort', cancel); }
  }
  async function context({ playback = false } = {}) {
    if (!state.audioContext) state.audioContext = new AudioContext();
    const ctx = state.audioContext;
    // Decoding microphone input does not need a running output device. A stale
    // speaker choice must not cancel recording or prevent local recognition.
    if (playback) {
      if (typeof ctx.setSinkId === 'function') await ctx.setSinkId(el('speaker-device').value || '');
      await ctx.resume();
    }
    return ctx;
  }
  async function playAudio(data, signal, segment, onStarted, onReady) {
    if (signal.aborted) throw abortError();
    const ctx = await context({ playback: true });
    const raw = atob(data.audio_base64);
    const bytes = Uint8Array.from(raw, (char) => char.charCodeAt(0));
    const decodeStarted = mediaNow();
    const buffer = await ctx.decodeAudioData(bytes.buffer);
    if (signal.aborted) throw abortError();
    onReady?.({ audio_duration_ms: buffer.duration * 1000, decode_ms: mediaNow() - decodeStarted });
    return new Promise((resolve, reject) => {
      const source = ctx.createBufferSource();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      source.buffer = buffer; source.connect(analyser); analyser.connect(ctx.destination);
      const levels = new Uint8Array(analyser.fftSize);
      let frame;
      let started = false;
      const record = (playbackState) => {
        if (!segment?.context) return;
        const { sessionId, turnId } = segment.context;
        playbackReceipts = playbackReceipts.catch(() => {}).then(() => chat.api(`/api/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(turnId)}/audio`, {
          method: 'POST', body: { segment_id: segment.segment_id, state: playbackState, text_start: segment.text_start, text_end: segment.text_end },
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
      try {
        source.start(); started = true; record('started');
        // This is a source scheduling event after context.resume(), not an
        // acoustic or sound-card measurement.
        onStarted?.({ context_running: ctx.state === 'running' ? 1 : 0, audio_context_time_ms: ctx.currentTime * 1000 }); meter();
      }
      catch (error) { source.onended = null; cleanup(); reject(error); }
    });
  }
  const speech = new window.aiNekoMedia.SpeechQueue({
    synthesize: (text, signal, segment) => voiceRequest('/api/voice/synthesize', { text,
      ...(segment?.context?.match ? { session_id: segment.context.sessionId, turn_id: segment.context.turnId, match: segment.context.match } : {}) }, signal),
    play: playAudio,
    onTiming: recordVoiceTiming,
    onActivity: (active) => { document.body.dataset.speaking = String(active); },
    onError: (error) => status(`声音未能播放：${errorText(error)} 文字回答仍保留。`),
  });
  function stopSpeech() {
    const prior = state.speechContext;
    state.speechTurn = null; state.speechContext = null;
    speech.stop(); window.aiNekoPet?.setSpeechLevel(0);
    if (prior?.control) void chat.api(`/api/sessions/${encodeURIComponent(prior.sessionId)}/turns/${encodeURIComponent(prior.turnId)}/cancel`, { method: 'POST', body: {} }).catch(() => {});
  }
  function clearKeys() { for (const kind of ['asr', 'tts']) el(`${kind}-api-key`).value = ''; }
  function currentCapture(capture) {
    return state.recording === capture && capture.epoch === state.recordingEpoch && !capture.discard && !capture.controller.signal.aborted && chat.bindingCurrent(capture.binding);
  }
  function stopCapture(capture, discard = false, { quiet = false } = {}) {
    if (!capture) return;
    const owned = state.recording === capture;
    if (discard) {
      capture.discard = true; capture.controller.abort(); capture.chunks = [];
      if (owned) { state.recordingEpoch += 1; state.recording = null; }
    }
    clearTimeout(capture.timer);
    if (capture.recorder?.state === 'recording') { capture.inputEndedAt = mediaNow(); capture.recorder.stop(); }
    capture.stream?.getTracks().forEach((track) => track.stop());
    if (owned) {
      void bridge.microphone(false).catch(() => {});
      el('record-voice').textContent = '● 说话'; el('record-voice').setAttribute('aria-pressed', 'false');
      document.body.dataset.recording = 'false';
      if (discard && !quiet) status('已停止录音。');
    }
  }
  function stopDeviceRefresh() {
    const refresh = state.deviceRefresh;
    if (!refresh) return;
    state.deviceRefresh = null;
    el('refresh-audio-devices').disabled = false;
    el('refresh-audio-devices').textContent = '允许麦克风并刷新设备';
    if (!refresh.complete) deviceStatus('已取消读取声音设备。');
    refresh.stream?.getTracks().forEach((track) => track.stop());
    void bridge.microphone(false).catch(() => {});
  }
  function stopRecording(discard = false, options) {
    if (discard) stopDeviceRefresh();
    stopCapture(state.recording, discard, options);
  }
  // N.E.K.O app-audio-capture.js uses attempt-local streams and a start generation.
  // Register ownership before cancellation or permission waits. Late callbacks
  // may release their own stream but must never stop a newer recording.
  async function toggleRecording() {
    stopDeviceRefresh();
    if (state.recording?.recorder?.state === 'recording') { stopRecording(); return; }
    if (state.recording) stopRecording(true);
    if (!chat.canRecord()) { status('对话尚未准备好，请先连接模型。'); return; }
    const capture = { epoch: ++state.recordingEpoch, controller: new AbortController(), discard: false, stream: null, recorder: null, chunks: [], timer: null };
    state.recording = capture;
    stopSpeech();
    status('正在准备录音…');
    try {
      await chat.cancelTurn();
      if (!currentCapture(capture)) return;
      capture.voiceConfig = await chat.api('/api/voice/config');
      if (!currentCapture(capture)) return;
      if (capture.voiceConfig.voice_provider === 'local' && capture.voiceConfig.local?.state !== 'ready') {
        throw new Error('请先在语音设置中下载并启用免费语音。');
      }
      capture.binding = await chat.captureBinding();
      if (!currentCapture(capture)) return;
      status('正在打开麦克风…');
      await bridge.microphone(true);
      if (!currentCapture(capture)) return;
      const device = el('microphone-device').value;
      capture.stream = await navigator.mediaDevices.getUserMedia({ video: false, audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1, ...(device ? { deviceId: { exact: device } } : {}) } });
      if (!currentCapture(capture)) { stopCapture(capture, true); return; }
      const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'].find((item) => MediaRecorder.isTypeSupported(item));
      capture.recorder = new MediaRecorder(capture.stream, mime ? { mimeType: mime } : undefined);
      let size = 0;
      capture.recorder.ondataavailable = (event) => {
        if (!currentCapture(capture)) return;
        size += event.data.size;
        if (size > 8 * 1024 * 1024) { stopCapture(capture, true); status('录音超过8MB，已丢弃，请缩短问题。'); }
        else if (event.data.size) capture.chunks.push(event.data);
      };
      capture.recorder.onstop = async () => {
        capture.inputEndedAt ??= mediaNow();
        capture.stream.getTracks().forEach((track) => track.stop());
        if (!currentCapture(capture)) return;
        try {
          status('正在识别你的话…');
          const blob = new Blob(capture.chunks, { type: capture.recorder.mimeType });
          const compressed = await blob.arrayBuffer();
          if (!currentCapture(capture)) return;
          const ctx = await context();
          if (!currentCapture(capture)) return;
          const decoded = await ctx.decodeAudioData(compressed);
          if (!currentCapture(capture)) return;
          if (capture.autoStopped && decoded.length / decoded.sampleRate > 60) status('录音达到时限，正在识别前60秒…');
          const wav = new Blob([window.aiNekoMedia.encodeVoiceWav(decoded, { truncate: capture.autoStopped === true })], { type: 'audio/wav' });
          if (!currentCapture(capture)) return;
          const dataURL = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onerror = reject; reader.onload = () => resolve(reader.result); reader.readAsDataURL(wav); });
          if (!currentCapture(capture)) return;
          const result = await voiceRequest('/api/voice/transcribe', { audio_base64: String(dataURL).split(',')[1], mime_type: 'audio/wav', session_id: capture.binding.sessionId, match: capture.binding.match }, capture.controller.signal);
          if (!currentCapture(capture)) return;
          const text = result.text?.trim();
          if (!text) { status('没有识别到文字，请再说一次。'); return; }
          status(`听到：${text}`);
          el('speak-replies').checked = true;
          await chat.submitText(text, { signal: capture.controller.signal, isCurrent: () => currentCapture(capture), binding: capture.binding, inputOrigin: "voice", inputEndedAt: capture.inputEndedAt });
        } catch (error) { if (currentCapture(capture)) status(`语音识别未完成：${errorText(error)}`); }
        finally { capture.chunks = []; if (state.recording === capture) state.recording = null; }
      };
      capture.recorder.onerror = () => {
        const owned = currentCapture(capture); stopCapture(capture, true);
        if (owned) status('录音设备发生错误，请重新选择麦克风。');
      };
      for (const track of capture.stream.getAudioTracks()) track.addEventListener('ended', () => {
        if (currentCapture(capture) && capture.recorder.state === 'recording') { stopCapture(capture, true); status('麦克风已断开，请重新选择设备。'); }
      }, { once: true });
      capture.recorder.start(250);
      capture.timer = setTimeout(() => { if (currentCapture(capture)) { capture.autoStopped = true; stopCapture(capture); } }, 60000);
      el('record-voice').textContent = '■ 结束并发送'; el('record-voice').setAttribute('aria-pressed', 'true');
      document.body.dataset.recording = 'true';
      el('pet-caption').textContent = '正在听你说话';
      status('正在听你说话，再点一次结束并发送。');
    } catch (error) {
      const owned = currentCapture(capture); stopCapture(capture, true);
      if (owned) status(`无法开始录音：${microphoneError(error)}`);
    }
  }
  async function disableVision({ notify = true } = {}) {
    state.vision = false; state.visionEpoch += 1;
    el('vision-enabled').checked = false;
    el('vision-preview').removeAttribute('src'); el('vision-preview').hidden = true;
    el('vision-status').textContent = '观察已关闭'; el('vision-badge').textContent = '观察已关闭';
    const results = await Promise.allSettled([chat.revokeVision(), bridge.stopVision()]);
    const failed = results.find((item) => item.status === 'rejected');
    if (failed) throw failed.reason;
    if (notify) await chat.closeObservation();
  }
  async function captureForTurn() {
    if (!state.vision) return null;
    const epoch = state.visionEpoch;
    let frame;
    try { frame = await bridge.captureVision(); }
    catch (error) {
      if (epoch === state.visionEpoch) {
        const stopped = disableVision(); const disabledEpoch = state.visionEpoch;
        await stopped;
        if (disabledEpoch === state.visionEpoch) status(`无法取得画面：${errorText(error)}`);
      }
      throw error;
    }
    if (!state.vision || epoch !== state.visionEpoch) throw Object.assign(new Error('观察已关闭，本次问题没有发送；请重新提问。'), { code: 'vision_revoked' });
    el('vision-preview').src = frame.data_url; el('vision-preview').hidden = false;
    el('vision-status').textContent = `${frame.source_name} · ${new Date(frame.captured_at).toLocaleTimeString()} · 本轮新截图`;
    return { frame, epoch };
  }
  function frameStillAllowed(capture) { return !capture || (state.vision && capture.epoch === state.visionEpoch); }
  const settingsSections = Object.fromEntries([
    ['persona', 'persona-status'], ['memories', 'memory-status'],
    ['memoryConfig', 'memory-config-status'], ['backups', 'memory-backup-status'],
  ].map(([name, id]) => [name, { id, generation: 0, actionGeneration: 0, pendingAction: false, pendingActions: new Set(),
    actionOwners: new Map(), actionNotices: new Map(), mutationTail: Promise.resolve(), readError: '', actionNotice: '', summary: '' }]));
  function renderSection(name) {
    const section = settingsSections[name];
    el(section.id).textContent = [...new Set([section.summary, section.actionNotice, ...section.actionNotices.values(), section.readError].filter(Boolean))].join(' ');
  }
  function sectionNotice(name, message) {
    settingsSections[name].actionNotice = message; renderSection(name);
  }
  function invalidateSection(name) { settingsSections[name].generation += 1; }
  function sectionAction(name, key = name) {
    const section = settingsSections[name], generation = ++section.actionGeneration;
    section.actionOwners.set(key, generation); section.pendingActions.add(generation);
    invalidateSection(name); section.pendingAction = true;
    const current = () => generation === section.actionOwners.get(key);
    return { current,
      settled() {
        if (section.pendingActions.delete(generation)) {
          section.pendingAction = section.pendingActions.size > 0; invalidateSection(name);
        }
      },
      notice(message) { if (current()) { section.actionNotices.set(key, message); renderSection(name); } },
    };
  }
  function mutateSection(name, action, mutation) {
    const section = settingsSections[name];
    // UI generations cannot order server writes. Keep writes in their dispatch
    // order, and discard a superseded intent only before it reaches the server.
    const operation = section.mutationTail.then(() => action.current() ? mutation() : undefined);
    section.mutationTail = operation.catch(() => {});
    return operation;
  }
  async function readSection(name, request, apply) {
    const section = settingsSections[name], generation = ++section.generation;
    try {
      const value = await request();
      if (generation !== section.generation) return false;
      apply(value);
      // A recovered GET clears its own error, never a failed save's notice.
      section.readError = ''; renderSection(name); return true;
    } catch (error) {
      if (generation === section.generation) { section.readError = errorText(error); renderSection(name); }
      return false;
    }
  }
  const personaKeys = ['name', 'user_name', 'traits', 'speaking_style', 'catchphrase', 'game_style'];
  const personaBaseline = Object.fromEntries(personaKeys.map((key) => [key, '']));
  const personaValues = () => Object.fromEntries(personaKeys.map((key) => [key, el(`persona-${key.replaceAll('_', '-')}`).value]));
  async function loadPersona({ submitted = null } = {}) {
    return readSection('persona', () => chat.api('/api/persona'), (value) => {
      for (const key of personaKeys) {
        if (settingsSections.persona.pendingAction) continue;
        const input = el(`persona-${key.replaceAll('_', '-')}`);
        const saved = Array.isArray(value[key]) ? value[key].join('、') : value[key] || '';
        if (input.value === (submitted ? submitted[key] : personaBaseline[key])) input.value = saved;
        personaBaseline[key] = saved;
      }
      settingsSections.persona.summary = `当前人格版本 ${value.version}`;
      const identity = document.querySelector('.identity strong'); if (identity) identity.textContent = value.name;
      const dockName = document.querySelector('.pet-name span:nth-child(2)'); if (dockName) dockName.textContent = value.name;
      window.dispatchEvent(new CustomEvent('ai-neko-persona', { detail: { name: value.name } }));
    });
  }
  function stopVoicePolling() {
    clearTimeout(state.voicePoll); state.voicePoll = null; state.voiceUIEpoch += 1;
  }
  const qwenRetryCodes = new Set(['qwen_memory_pressure', 'qwen_gpu_memory', 'qwen_resource_exhausted', 'qwen_timeout', 'qwen_inference_failed', 'qwen_response']);
  function canRetryQwen() {
    return state.qwenRetry?.requestId === state.qwenRequestId && state.qwenRetry !== null &&
      state.qwenVoice.state === 'error' && state.qwenVoice.supported === true && state.qwenVoice.loaded === false;
  }
  function showVoiceProvider() {
    const local = el('voice-provider').value === 'local';
    const qwen = el('local-tts-engine').value === 'qwen';
    el('local-voice-settings').hidden = !local; el('cloud-voice-settings').hidden = local;
    el('kokoro-voice-settings').hidden = qwen; el('qwen-voice-settings').hidden = !qwen;
    const loading = state.qwenAction === 'load';
    el('voice-provider').disabled = state.voiceBusy && !loading;
    el('local-tts-engine').disabled = !local || (state.voiceBusy && !loading);
    el('local-tts-speaker-id').disabled = !local || qwen || state.voiceBusy;
    for (const id of ['asr-base-url', 'asr-model', 'asr-api-key', 'clear-asr-key', 'tts-base-url', 'tts-model', 'tts-voice', 'tts-api-key', 'clear-tts-key']) el(id).disabled = local;
    const selectedSaved = el('voice-provider').value === (state.voiceConfig.voice_provider || 'openai') &&
      (!local || el('local-tts-engine').value === (state.voiceConfig.local_tts_engine || 'kokoro'));
    el('test-voice').disabled = state.voiceBusy || !selectedSaved || (local &&
      (qwen ? state.qwenVoice.state !== 'ready' || state.qwenVoice.supported === false : state.localVoice.state !== 'ready'));
    el('load-qwen-voice').disabled = state.voiceBusy || !selectedSaved || !local || !qwen || (state.qwenVoice.state !== 'ready' && !canRetryQwen()) || state.qwenVoice.supported === false || state.qwenVoice.loaded === true;
    el('unload-qwen-voice').disabled = (state.voiceBusy && !loading) || !local || !qwen || (!loading && !state.qwenReleaseNeeded && state.qwenVoice.loaded !== true);
    el('refresh-qwen-voice').disabled = state.voiceBusy;
    el('load-qwen-voice').textContent = state.qwenAction === 'load' ? '正在预热 Qwen…' : canRetryQwen() ? '重试预热 Qwen' : '预热 Qwen';
    el('unload-qwen-voice').textContent = state.qwenAction === 'unload' ? '正在释放…' : loading ? '取消预热并释放' : '释放 Qwen 显存';
  }
  function showQwenVoice(value, { fromOperation = false } = {}) {
    state.qwenVoice = value || {};
    // Only an owned load failure grants retry. Public status has no error code:
    // retain that grant only while the backend still reports a valid binding
    // (idle_unload_seconds exists only after load_binding succeeds).
    const bound = Number.isSafeInteger(state.qwenVoice.idle_unload_seconds) && state.qwenVoice.idle_unload_seconds > 0;
    if (state.qwenVoice.state !== 'error' || state.qwenVoice.supported !== true || state.qwenVoice.loaded !== false ||
        (!fromOperation && !bound)) state.qwenRetry = null;
    const labels = { missing: 'Qwen 资源缺失：请先配置本机实验环境、模型和声音。这里不会自动下载。',
      error: 'Qwen 暂不可用，请检查本机资源后刷新状态。', ready: state.qwenVoice.loaded ? 'Qwen 已预热，可以试听。' : 'Qwen 资源已就绪；首次朗读需加载，也可先点击预热。' };
    const message = state.qwenAction === 'load' ? '正在加载 Qwen，请稍候；首次加载可能需要数十秒。' :
      state.qwenAction === 'unload' ? '正在停止 Qwen 并释放显存…' : state.qwenVoice.message || labels[state.qwenVoice.state] || '正在读取 Qwen 资源状态…';
    el('qwen-voice-status').textContent = message;
    showVoiceProvider();
  }
  async function refreshQwenVoice() {
    if (state.voiceBusy) return;
    const epoch = ++state.qwenEpoch, uiEpoch = state.voiceUIEpoch;
    el('qwen-voice-status').textContent = '正在读取 Qwen 资源状态…';
    try {
      const value = await chat.api('/api/voice/qwen/status');
      if (epoch === state.qwenEpoch && uiEpoch === state.voiceUIEpoch) showQwenVoice(value);
    } catch (error) {
      if (epoch === state.qwenEpoch && uiEpoch === state.voiceUIEpoch) showQwenVoice({ state: 'error', message: `读取 Qwen 状态失败：${qwenErrorText(error)}` });
    }
  }
  function qwenAction(action, automatic = false) {
    const previous = state.qwenOperation;
    // Release may supersede a cold load; other writes await backend cleanup.
    if (state.voiceBusy && !(action === 'unload' && previous?.action === 'load')) {
      return previous?.action === action ? previous.promise : undefined;
    }
    if (action === 'unload') state.qwenReleased = true;
    else if (!automatic) state.qwenReleased = false;
    const requestId = action === 'load' ? crypto.randomUUID().replaceAll('-', '') : previous?.requestId || state.qwenRequestId;
    const operation = { action, requestId, controller: new AbortController(), promise: null };
    if (action === 'load') state.qwenRequestId = requestId;
    state.qwenOperation = operation; state.qwenRetry = null;
    state.voiceBusy = true; state.qwenAction = action; state.qwenEpoch += 1; state.qwenReleaseNeeded = true;
    // Abort only detaches the renderer waiter. Matching unload must also run:
    // its request tombstone prevents a delayed load from reaching the worker.
    previous?.controller.abort();
    if (!automatic) { stopSpeech(); if (action === 'load') stopRecording(true); }
    showQwenVoice(state.qwenVoice, { fromOperation: true }); showLocalVoice(state.localVoice);
    const current = () => state.qwenOperation === operation;
    operation.promise = (async () => {
      try {
        const value = await chat.api('/api/voice/qwen/' + action, { method: 'POST', body: requestId ? { request_id: requestId } : {},
          ...(action === 'load' ? { signal: operation.controller.signal } : {}) });
        if (!current()) return;
        state.qwenVoice = value;
        if (action === 'unload' && value.loaded === false) state.qwenReleaseNeeded = false;
      } catch (error) {
        if (!current()) return;
        if (action === 'load' && qwenRetryCodes.has(error.code)) state.qwenRetry = { code: error.code, requestId };
        state.qwenVoice = { ...state.qwenVoice, state: 'error', message: 'Qwen ' + (action === 'load' ? '预热' : '释放') + '失败：' + qwenErrorText(error) };
        if (automatic || action === 'unload') status(state.qwenVoice.message);
      } finally {
        if (current()) {
          // Reads begun during this operation must not outlive its final state.
          state.qwenEpoch += 1;
          state.voiceBusy = false; state.qwenAction = null; state.qwenOperation = null;
          showQwenVoice(state.qwenVoice, { fromOperation: true }); showLocalVoice(state.localVoice);
        }
      }
    })();
    return operation.promise;
  }
  function cancelQwenPreload() {
    if (state.qwenOperation?.action === 'load') return qwenAction('unload');
    if (state.qwenOperation?.action === 'unload') return state.qwenOperation.promise;
  }
  function prepareSpeech({ newTurn = false } = {}) {
    if (!el('speak-replies').checked || state.voiceBusy || state.voiceConfig.voice_provider !== 'local' ||
        state.voiceConfig.local_tts_engine !== 'qwen' || state.qwenVoice.state !== 'ready' ||
        state.qwenVoice.supported === false) return;
    if (!newTurn && (state.qwenReleased || state.qwenVoice.loaded === true)) return;
    // Each accepted submission revalidates residency through idempotent load:
    // the backend can unload on idle without updating this renderer's cache.
    // Opening settings cannot undo explicit release. No timers or retry loop.
    if (newTurn) state.qwenReleased = false;
    void qwenAction('load', true);
  }
  function showLocalVoice(value) {
    state.localVoice = value || {};
    const installing = state.localVoice.state === 'installing';
    const labels = { missing: '尚未下载，点击下方按钮准备免费语音。', installing: '正在准备免费语音…', ready: '免费语音已准备好，可以试听中文女声。', error: '准备失败，可以重试。' };
    const downloaded = Number(state.localVoice.downloaded_bytes), total = Number(state.localVoice.total_bytes);
    const progress = installing && Number.isFinite(downloaded) && downloaded >= 0 && Number.isFinite(total) && total > 0
      ? ` ${Math.min(100, Math.floor(downloaded / total * 100))}%（${(downloaded / 1048576).toFixed(1)} / ${(total / 1048576).toFixed(1)} MB）` : '';
    el('local-voice-status').textContent = `${state.localVoice.message || labels[state.localVoice.state] || '正在读取资源状态…'}${progress}`;
    el('install-local-voice').hidden = state.localVoice.state === 'ready';
    el('install-local-voice').disabled = installing || state.voiceBusy;
    el('install-local-voice').textContent = state.localVoice.state === 'error' ? '重试下载并启用免费语音' : '下载并启用免费语音';
    el('cancel-local-voice').hidden = !installing; el('cancel-local-voice').disabled = state.voiceBusy;
    showVoiceProvider();
  }
  function pollLocalVoice(epoch = state.voiceUIEpoch) {
    clearTimeout(state.voicePoll); state.voicePoll = null;
    if (epoch !== state.voiceUIEpoch || el('settings-panel').hidden || state.localVoice.state !== 'installing') return;
    state.voicePoll = setTimeout(async () => {
      state.voicePoll = null;
      if (epoch !== state.voiceUIEpoch || el('settings-panel').hidden) return;
      try {
        const value = await chat.api('/api/voice/local');
        if (epoch !== state.voiceUIEpoch || el('settings-panel').hidden) return;
        showLocalVoice(value); pollLocalVoice(epoch);
      } catch (error) {
        if (epoch === state.voiceUIEpoch && !el('settings-panel').hidden) {
          el('local-voice-status').textContent = `读取下载状态失败：${errorText(error)}。重新打开设置可刷新。`;
          el('cancel-local-voice').disabled = false;
        }
      }
    }, 1000);
  }
  const voiceFieldKeys = ['voice_provider', 'local_tts_engine', 'local_tts_speaker_id', 'asr_base_url', 'asr_model', 'tts_base_url', 'tts_model', 'tts_voice'];
  const voiceValues = () => Object.fromEntries(voiceFieldKeys.map((key) => [key, el(key.replaceAll('_', '-')).value]));
  const voiceBaseline = voiceValues();
  function acceptVoiceConfig(value, qwenEpoch = state.qwenEpoch) {
    state.voiceConfig = value;
    if (qwenEpoch === state.qwenEpoch) showQwenVoice(value.qwen);
    showLocalVoice(value.local);
  }
  async function loadVoiceConfig({ submitted = null, clearSubmitted = null } = {}) {
    stopVoicePolling(); const epoch = state.voiceUIEpoch, qwenEpoch = state.qwenEpoch, configEpoch = ++state.voiceConfigEpoch;
    const value = await chat.api('/api/voice/config');
    if (configEpoch !== state.voiceConfigEpoch) return;
    // Closing the panel invalidates its fields and polling, not the persisted
    // provider used by speech. A successful save also updates this cache directly.
    acceptVoiceConfig(value, qwenEpoch);
    if (epoch !== state.voiceUIEpoch) return;
    const defaults = { voice_provider: 'openai', local_tts_engine: 'kokoro', local_tts_speaker_id: 46 };
    for (const key of voiceFieldKeys) {
      const input = el(key.replaceAll('_', '-')), saved = String(value[key] ?? defaults[key] ?? '');
      if (input.value === (submitted ? submitted[key] : voiceBaseline[key])) input.value = saved;
      voiceBaseline[key] = saved;
    }
    for (const kind of ['asr', 'tts']) {
      el(`${kind}-key-state`).textContent = value[`${kind}_key_set`] ? '已设置，留空保留' : '尚未设置';
      if (clearSubmitted && el(`clear-${kind}-key`).checked === clearSubmitted[kind]) el(`clear-${kind}-key`).checked = false;
    }
    showVoiceProvider(); pollLocalVoice(epoch); prepareSpeech();
  }
  let memoryEditors = new Map();
  let memoryConfigBaseline = false;
  function showExtractionOverview(overview) {
    const target = el('memory-extraction-status');
    const counts = ['pending', 'running', 'failed', 'cancelled'];
    const states = {
      pending: '尚未完成，尚未确认是否保存。',
      running: '尚未完成，尚未确认是否保存。',
      failed: '抽取失败。',
      cancelled: '已取消，不会自动重试。',
      completed: '抽取完成；是否保存内容请以下方记忆列表为准。',
    };
    // A missing/older response must not make an unknown background state look
    // successful. Render metadata only, separately from manual action notices.
    if (!overview || !counts.every((key) => Number.isSafeInteger(overview[key]) && overview[key] >= 0) ||
        typeof overview.context_limit_exceeded !== 'boolean' ||
        (overview.latest !== null && (!overview.latest || !Object.hasOwn(states, overview.latest.status) ||
          !Number.isSafeInteger(overview.latest.attempts) || overview.latest.attempts < 0))) {
      target.textContent = '后台抽取状态暂不可用；请刷新记忆查看。'; return;
    }
    const lines = [
      `后台抽取（最近一次刷新）：未完成任务记录中，待处理 ${overview.pending}，运行状态 ${overview.running}；未完成状态不保证会继续处理。`,
      overview.latest ? `最近提交的任务：${states[overview.latest.status]}` : '还没有自动抽取任务记录。',
      `历史任务累计：失败 ${overview.failed}，取消 ${overview.cancelled}；这些计数不代表本次手动保存的结果。`,
    ];
    if (overview.context_limit_exceeded) lines.push('当前记忆上下文超出自动整理容量，自动抽取受限；明确保存仍可使用。');
    if (overview.failed || overview.cancelled || overview.context_limit_exceeded) {
      lines.push('未保存的内容需要在新消息中明确重述，或用上方表单明确保存。');
    }
    target.textContent = lines.join(' ');
  }
  async function loadMemoryConfig() {
    return readSection('memoryConfig', () => chat.api('/api/memory/config'), (config) => {
      if (settingsSections.memoryConfig.pendingAction) return;
      const input = el('memory-auto-extract'), saved = config.auto_extract === true;
      if (input.checked === memoryConfigBaseline) input.checked = saved;
      memoryConfigBaseline = saved;
    });
  }
  async function loadMemories({ accepted = null } = {}) {
    // Invalidate the old overview immediately, including reopen and reconnect.
    // The existing section generation protects both it and the fact list.
    const generation = settingsSections.memories.generation + 1;
    showExtractionOverview(null);
    const loaded = await readSection('memories', () => chat.api('/api/memories'), (result) => {
      const next = new Map(), cards = [];
      for (const item of result.memories || []) {
        const existing = memoryEditors.get(item.id);
        if (existing) {
          if (!settingsSections.memories.pendingAction) {
            if (existing.text.value === (accepted?.id === item.id ? accepted.content : existing.baseline)) existing.text.value = item.content;
            existing.baseline = item.content;
          }
          cards.push(existing.card); next.set(item.id, existing); continue;
        }
        const card = document.createElement('div'); card.className = 'memory-card';
        const text = document.createElement('textarea'); text.value = item.content; text.maxLength = 1000; text.setAttribute('aria-label', '记忆内容');
        const meta = document.createElement('small');
        meta.textContent = (({ preference: '偏好', fact: '事实', event: '事件' })[item.kind] || item.kind) + ' · ' +
          (item.source_ids?.some((id) => !id.startsWith('manual:')) ? '来自对话' : '明确保存');
        const save = document.createElement('button'); save.type = 'button'; save.className = 'soft-button'; save.textContent = '保存修改';
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'text-button'; remove.textContent = '遗忘';
        const route = '/api/memories/' + encodeURIComponent(item.id);
        save.onclick = async () => {
          const submitted = text.value, action = sectionAction('memories', item.id); stopSpeech();
          try {
            await mutateSection('memories', action, async () => {
              await chat.cancelTurn();
              if (action.current()) await chat.api(route, { method: 'PUT', body: { content: submitted } });
            });
            if (!action.current()) return;
            action.settled();
            await loadMemories({ accepted: { id: item.id, content: submitted } });
            action.notice('记忆已经更新。');
          } catch (error) { action.notice(errorText(error)); }
          finally { action.settled(); }
        };
        remove.onclick = async () => {
          const action = sectionAction('memories', item.id); stopSpeech(); stopRecording(true);
          try {
            await mutateSection('memories', action, async () => {
              await chat.invalidatePending(); await chat.cancelTurn();
              if (!action.current()) return;
              await chat.api(route, { method: 'DELETE' });
              // Erased content must leave the renderer even if another action
              // owns the latest notice or the subsequent history refresh fails.
              invalidateSection('memories'); memoryEditors.delete(item.id); card.hidden = true;
              await chat.refreshAfterForget();
            });
            if (!action.current()) return;
            action.settled(); await loadMemories();
            action.notice('已遗忘这条记忆，并刷新相关历史。');
          } catch (error) { await chat.refreshAfterForget().catch(() => {}); action.notice(errorText(error)); }
          finally { action.settled(); }
        };
        const evidence = document.createElement('button'); evidence.type = 'button'; evidence.className = 'text-button'; evidence.textContent = '查看来源';
        const evidenceBox = document.createElement('div'); evidenceBox.className = 'memory-evidence'; evidenceBox.hidden = true;
        evidence.onclick = async () => {
          if (!evidenceBox.hidden) { evidenceBox.hidden = true; evidence.textContent = '查看来源'; return; }
          evidence.disabled = true;
          try {
            const value = await chat.api(route + '/sources'); evidenceBox.replaceChildren();
            for (const origin of value.sources || []) {
              const label = document.createElement('small');
              label.textContent = origin.source_id?.startsWith('manual:') ? '明确保存的原文' : '对话来源 · ' + (origin.turn_id || origin.source_id);
              const quote = document.createElement('p'); quote.textContent = origin.source_text || '没有可显示的来源原文。';
              evidenceBox.append(label, quote);
            }
            if (!value.sources?.length) evidenceBox.textContent = '没有可显示的来源原文。';
            evidenceBox.hidden = false; evidence.textContent = '收起来源';
          } catch (error) { sectionNotice('memories', errorText(error)); }
          finally { evidence.disabled = false; }
        };
        card.append(text, meta, save, remove, evidence, evidenceBox);
        cards.push(card); next.set(item.id, { card, text, meta, baseline: item.content });
      }
      for (const [id, editor] of memoryEditors) {
        if (!next.has(id) && editor.text.value !== editor.baseline) {
          editor.meta.textContent = '该条目已不在最新列表中，未保存的编辑仍保留在这里。';
          cards.push(editor.card); next.set(id, editor);
        }
      }
      const list = el('memory-list'), focused = document.activeElement;
      const selection = focused?.tagName === 'TEXTAREA' && list.contains(focused)
        ? [focused.selectionStart, focused.selectionEnd, focused.selectionDirection] : null;
      memoryEditors = next; list.replaceChildren(...cards);
      if (selection && list.contains(focused)) { focused.focus({ preventScroll: true }); focused.setSelectionRange(...selection); }
      if (!cards.length) el('memory-list').textContent = '还没有长期记忆。';
      showExtractionOverview(result.extraction_overview);
    });
    if (!loaded && generation === settingsSections.memories.generation) showExtractionOverview(null);
    return loaded;
  }
  let backupRevision = null;
  let backupConfirmation = null;
  let backupBusy = false;
  function closeBackupConfirmation() { backupConfirmation = null; el('memory-backup-confirmation').hidden = true; }
  function backupControls(busy) {
    backupBusy = busy;
    for (const button of document.querySelectorAll('[aria-label="记忆快照"] button')) button.disabled = busy || button.dataset.unrestorable === 'true';
  }
  function confirmBackupAction(action, item) {
    if (backupBusy) return;
    invalidateSection('backups');
    backupConfirmation = { action, id: item.id, revision: backupRevision };
    const restore = action === 'restore';
    el('memory-backup-confirm-title').textContent = restore ? '确认恢复这个快照？' : '确认删除这个快照？';
    el('memory-backup-confirm-detail').textContent = `${item.id}\n${restore ? '恢复其中的人格和事实，保留之后的纠正和遗忘。会停止当前任务，并按删除策略刷新当前历史。' : '仅删除选中的本地快照，无法撤销；当前人格和记忆保持不变。此操作会停止当前任务。'}`;
    el('confirm-memory-backup').textContent = restore ? '确认恢复' : '确认删除';
    el('memory-backup-confirmation').hidden = false;
    el('cancel-memory-backup').focus();
  }
  async function loadBackups() {
    return readSection('backups', () => chat.api('/api/memory/backups'), (result) => {
    backupRevision = result.revision;
    el('memory-backup-list').dataset.revision = String(result.revision);
    if (!backupConfirmation || backupConfirmation.revision !== result.revision || !(result.backups || []).some((item) => item.id === backupConfirmation.id)) closeBackupConfirmation();
    el('memory-backup-list').replaceChildren();
    for (const item of result.backups || []) {
      if (!/^memory-[a-f0-9]{32}\.sqlite$/.test(item.id)) continue;
      const card = document.createElement('div'); card.className = 'backup-card'; card.dataset.backupId = item.id;
      const title = document.createElement('strong');
      const timestamp = typeof item.created_at === 'number' ? (item.created_at < 1e12 ? item.created_at * 1000 : item.created_at) : item.created_at;
      const date = new Date(timestamp);
      title.textContent = Number.isNaN(date.getTime()) ? '本地记忆快照' : date.toLocaleString();
      const detail = document.createElement('small'); detail.textContent = `${item.id} · ${Math.ceil((item.bytes || 0) / 1024)} KB${Number.isInteger(item.facts_count) ? ` · ${item.facts_count} 条记忆` : ''}`;
      const restore = document.createElement('button'); restore.type = 'button'; restore.className = 'soft-button'; restore.textContent = '恢复';
      restore.dataset.unrestorable = String(item.restorable === false); restore.disabled = backupBusy || item.restorable === false;
      restore.onclick = () => confirmBackupAction('restore', item);
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'text-button'; remove.textContent = '删除快照'; remove.disabled = backupBusy;
      remove.onclick = () => confirmBackupAction('delete', item);
      card.append(title, detail, restore, remove);
      if (item.restorable === false) { const error = document.createElement('p'); error.className = 'settings-message'; error.textContent = '此快照文件损坏或格式不兼容，无法恢复。可以删除。'; card.append(error); }
      el('memory-backup-list').append(card);
    }
    if (!el('memory-backup-list').children.length) el('memory-backup-list').textContent = '还没有记忆快照。';
    });
  }
  async function prepareMemoryAction() {
    stopSpeech(); stopRecording(true); await chat.invalidatePending();
    await chat.cancelTurn(); await flushPlayback();
  }
  el('create-memory-backup').onclick = async () => {
    if (backupBusy) return;
    invalidateSection('backups'); backupControls(true); closeBackupConfirmation();
    try { await prepareMemoryAction(); await chat.api('/api/memory/backups', { method: 'POST', body: {} }); await chat.refreshAfterForget(); await loadBackups(); sectionNotice('backups', '已创建本地记忆快照。'); }
    catch (error) { await chat.refreshAfterForget().catch(() => {}); sectionNotice('backups', errorText(error)); }
    finally { backupControls(false); }
  };
  el('refresh-memory-backups').onclick = () => loadBackups();
  el('cancel-memory-backup').onclick = closeBackupConfirmation;
  el('confirm-memory-backup').onclick = async () => {
    if (!backupConfirmation || backupBusy) return;
    const selected = backupConfirmation; invalidateSection('backups'); backupControls(true); closeBackupConfirmation();
    try {
      await prepareMemoryAction();
      const path = `/api/memory/backups/${encodeURIComponent(selected.id)}`;
      if (selected.action === 'restore') {
        await chat.api(`${path}/restore`, { method: 'POST', body: { confirm: true, expected_revision: selected.revision } });
        await chat.refreshAfterForget(); await Promise.all([loadPersona(), loadMemories()]);
      } else { await chat.api(path, { method: 'DELETE' }); await chat.refreshAfterForget(); }
      await loadBackups();
      sectionNotice('backups', selected.action === 'restore' ? '已恢复人格和记忆，保留后续纠正与遗忘，并刷新历史。' : '已删除选中的快照。');
    } catch (error) {
      await loadBackups().catch(() => {});
      await chat.refreshAfterForget().catch(() => {});
      sectionNotice('backups', error.status === 409 ? '记忆已经变化，请重新选择快照并确认恢复。' : errorText(error));
    } finally { backupControls(false); }
  };
  el('persona-form').onsubmit = async (event) => {
    event.preventDefault(); const action = sectionAction('persona');
    const submitted = personaValues(), payload = Object.fromEntries(personaKeys.map((key) => [key, submitted[key].trim()]));
    payload.traits = payload.traits.split(/[、,，\n]/).map((item) => item.trim()).filter(Boolean);
    try {
      await mutateSection('persona', action, () => chat.api('/api/persona', { method: 'PUT', body: payload }));
      if (!action.current()) return;
      action.settled(); await loadPersona({ submitted }); action.notice('已保存，下一轮生效。');
    } catch (error) { action.notice(errorText(error)); }
    finally { action.settled(); }
  };
  el('voice-form').onsubmit = async (event) => {
    event.preventDefault(); if (state.voiceBusy) return;
    const submitted = voiceValues(), clearSubmitted = Object.fromEntries(['asr', 'tts'].map((kind) => [kind, el(`clear-${kind}-key`).checked]));
    const payload = { voice_provider: el('voice-provider').value };
    if (payload.voice_provider === 'local') {
      payload.local_tts_engine = el('local-tts-engine').value || 'kokoro';
    }
    if (payload.voice_provider === 'local' && payload.local_tts_engine === 'kokoro') {
      const value = el('local-tts-speaker-id').value.trim();
      if (!/^\d{1,3}$/.test(value) || Number(value) > 102) {
        el('voice-config-status').textContent = '本地声线编号应为 0 到 102 的整数。'; return;
      }
      payload.local_tts_speaker_id = Number(value);
    }
    if (payload.voice_provider !== 'local') {
      for (const key of ['asr_base_url', 'asr_model', 'tts_base_url', 'tts_model', 'tts_voice', 'asr_api_key', 'tts_api_key']) payload[key] = el(key.replaceAll('_', '-')).value.trim();
      for (const kind of ['asr', 'tts']) payload[`clear_${kind}_api_key`] = el(`clear-${kind}-key`).checked;
    }
    state.voiceBusy = true; state.voiceConfigEpoch += 1; stopRecording(true); stopSpeech(); showVoiceProvider();
    try {
      const saved = await chat.api('/api/voice/config', { method: 'PUT', body: payload });
      state.voiceConfigEpoch += 1;
      acceptVoiceConfig({ ...state.voiceConfig, ...Object.fromEntries(voiceFieldKeys.filter((key) => key in payload).map((key) => [key, payload[key]])), ...saved });
      await loadVoiceConfig({ submitted, clearSubmitted });
      el('voice-config-status').textContent = payload.voice_provider === 'local' && payload.local_tts_engine === 'qwen'
        ? state.qwenVoice.state === 'ready' ? 'Qwen 设置已保存，可以预热或试听；语音识别仍使用 SenseVoice。' : 'Qwen 设置已保存，但资源尚未就绪，请查看 Qwen 状态。'
        : payload.voice_provider === 'local' && state.localVoice.state !== 'ready' ? '免费语音已保存，请下载资源后试听。' : '语音配置已保存，可以试听。';
    }
    catch (error) { el('voice-config-status').textContent = errorText(error); }
    finally { state.voiceBusy = false; for (const key of ['asr_api_key', 'tts_api_key']) if (key in payload) payload[key] = ''; showLocalVoice(state.localVoice); prepareSpeech(); }
  };
  el('voice-provider').onchange = () => {
    stopSpeech(); stopRecording(true); void cancelQwenPreload();
    showVoiceProvider(); el('voice-config-status').textContent = '选择后请保存；免费语音也可直接点击下载并启用。';
  };
  el('local-tts-engine').onchange = () => {
    stopSpeech(); stopRecording(true); void cancelQwenPreload(); showVoiceProvider();
    el('voice-config-status').textContent = '朗读引擎已选择，请保存后试听。语音识别仍使用 SenseVoice。';
    if (el('local-tts-engine').value === 'qwen') void refreshQwenVoice();
  };
  el('load-qwen-voice').onclick = () => { if (!el('load-qwen-voice').disabled) return qwenAction('load'); };
  el('unload-qwen-voice').onclick = () => { if (!el('unload-qwen-voice').disabled) return qwenAction('unload'); };
  el('refresh-qwen-voice').onclick = refreshQwenVoice;
  el('local-tts-speaker-id').onchange = () => {
    stopSpeech();
    el('voice-config-status').textContent = '声线已选择，请保存后试听。';
  };
  el('install-local-voice').onclick = async () => {
    if (state.voiceBusy || state.localVoice.state === 'installing') return;
    state.voiceBusy = true; state.voiceConfigEpoch += 1; stopRecording(true); stopSpeech(); showLocalVoice(state.localVoice);
    try {
      const engine = el('local-tts-engine').value || 'kokoro';
      const saved = await chat.api('/api/voice/config', { method: 'PUT', body: { voice_provider: 'local', local_tts_engine: engine } });
      state.voiceConfigEpoch += 1;
      acceptVoiceConfig({ ...state.voiceConfig, voice_provider: 'local', local_tts_engine: engine, ...saved });
      el('voice-provider').value = 'local';
      showLocalVoice(await chat.api('/api/voice/local/install', { method: 'POST', body: { confirm: true } }));
      el('voice-config-status').textContent = '免费语音已启用，资源准备好后可试听。'; pollLocalVoice();
    } catch (error) { el('local-voice-status').textContent = errorText(error); }
    finally { state.voiceBusy = false; el('install-local-voice').disabled = state.localVoice.state === 'installing'; el('cancel-local-voice').disabled = false; showVoiceProvider(); }
  };
  el('cancel-local-voice').onclick = async () => {
    if (state.voiceBusy) return;
    state.voiceBusy = true; stopVoicePolling(); showLocalVoice(state.localVoice);
    try { showLocalVoice(await chat.api('/api/voice/local/cancel', { method: 'POST', body: {} })); pollLocalVoice(); }
    catch (error) { el('local-voice-status').textContent = errorText(error); }
    finally { state.voiceBusy = false; el('install-local-voice').disabled = state.localVoice.state === 'installing'; el('cancel-local-voice').disabled = false; showVoiceProvider(); }
  };
  el('memory-form').onsubmit = async (event) => {
    // Adding separate drafts is additive; only replacements of the same stored
    // row may supersede a queued write.
    event.preventDefault(); const action = sectionAction('memories', Symbol('new-memory'));
    const submitted = el('memory-content').value, kind = el('memory-kind').value;
    try {
      await mutateSection('memories', action, () => chat.api('/api/memories', { method: 'POST', body: { content: submitted.trim(), kind } }));
      if (!action.current()) return;
      action.settled();
      if (el('memory-content').value === submitted && el('memory-kind').value === kind) el('memory-content').value = '';
      await loadMemories(); action.notice('已保存，之后对话可以回想。');
    } catch (error) { action.notice(errorText(error)); }
    finally { action.settled(); }
  };
  el('memory-auto-extract').onchange = async (event) => {
    const action = sectionAction('memoryConfig'), submitted = event.target.checked;
    try {
      await mutateSection('memoryConfig', action, async () => {
        await chat.api('/api/memory/config', { method: 'PUT', body: { auto_extract: submitted } });
        // Even an older successful write establishes the rollback value if the
        // newest queued intent fails. The queue still sends that newest intent.
        memoryConfigBaseline = submitted;
      });
      if (!action.current()) return;
      action.notice(submitted ? '之后的对话会自动整理记忆。' : '已关闭自动整理。');
    } catch (error) {
      if (!action.current()) return;
      if (event.target.checked === submitted) event.target.checked = memoryConfigBaseline;
      action.notice(errorText(error));
    } finally { action.settled(); }
  };
  el('refresh-memories').onclick = () => Promise.all([loadMemories(), loadMemoryConfig()]);
  el('refresh-vision').onclick = async () => {
    try {
      await disableVision();
      el('vision-source').replaceChildren(new Option('选择一个窗口', ''));
      const sources = await bridge.visionSources();
      for (const source of sources) el('vision-source').add(new Option(`窗口 · ${source.name}`, source.id));
      el('vision-status').textContent = sources.length ? '选择窗口后开启观察。' : '没有可用窗口，请恢复目标窗口后刷新。';
    } catch (error) { el('vision-status').textContent = errorText(error); }
  };
  el('vision-source').onchange = () => disableVision().catch(() => {});
  el('vision-enabled').onchange = async (event) => {
    if (!event.target.checked) { await disableVision(); return; }
    const source = el('vision-source').value;
    const stopped = disableVision();
    const epoch = state.visionEpoch;
    // Preserve the checked user intent while cancellation and selection wait.
    // Actual capture stays disabled until this generation receives its preview.
    el('vision-enabled').checked = true;
    el('vision-status').textContent = '正在选择画面，请稍候…';
    el('vision-badge').textContent = '观察准备中';
    try {
      await stopped;
      if (epoch !== state.visionEpoch) return;
      const frame = await bridge.selectVision(source);
      if (epoch !== state.visionEpoch) return;
      state.vision = true; el('vision-enabled').checked = true; el('vision-preview').src = frame.data_url; el('vision-preview').hidden = false;
      el('vision-status').textContent = `已选：${frame.source_name}。每次提问取新图。`;
      el('vision-badge').textContent = `观察：${frame.source_name}`;
    } catch (error) {
      if (epoch !== state.visionEpoch) return;
      const stopped = disableVision(); const disabledEpoch = state.visionEpoch;
      await stopped.catch(() => {});
      if (disabledEpoch === state.visionEpoch) el('vision-status').textContent = errorText(error);
    }
  };
  el('microphone-device').onchange = () => {
    if (state.recording) { stopRecording(true); status('已切换麦克风并取消本轮，请重新说话。'); }
  };
  el('refresh-audio-devices').onclick = async () => {
    stopRecording(true);
    const refresh = { stream: null };
    state.deviceRefresh = refresh;
    const current = () => state.deviceRefresh === refresh;
    el('refresh-audio-devices').disabled = true;
    el('refresh-audio-devices').textContent = '正在读取声音设备…';
    deviceStatus('正在读取声音设备，系统可能会请求麦克风权限…');
    try {
      await bridge.microphone(true);
      if (!current()) return;
      refresh.stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      try {
        if (!current()) return;
        const devices = await navigator.mediaDevices.enumerateDevices();
        if (!current()) return;
        for (const [id, kind, label] of [['microphone-device', 'audioinput', '系统默认麦克风'], ['speaker-device', 'audiooutput', '系统默认扬声器']]) {
          const selected = el(id).value;
          el(id).replaceChildren(new Option(label, ''));
          for (const device of devices.filter((item) => item.kind === kind)) el(id).add(new Option(device.label || '未命名设备', device.deviceId));
          if (devices.some((device) => device.kind === kind && device.deviceId === selected)) el(id).value = selected;
        }
        refresh.complete = true;
        const inputs = devices.filter((device) => device.kind === 'audioinput').length;
        const outputs = devices.filter((device) => device.kind === 'audiooutput').length;
        deviceStatus(`设备列表已更新：${inputs} 个麦克风选项、${outputs} 个播放设备选项。请在下方选择；回到聊天后点“说话”开始录音。`);
      } finally { refresh.stream.getTracks().forEach((track) => track.stop()); }
    } catch (error) { if (current()) { refresh.complete = true; deviceStatus(`无法读取声音设备：${microphoneError(error)}`); } }
    finally { if (current()) stopDeviceRefresh(); }
  };
  el('voice-shortcut').onchange = async (event) => { try { const enabled = await bridge.voiceShortcut(event.target.checked); event.target.checked = enabled; status(enabled ? '语音快捷键已开启。' : '快捷键未启用；如果被占用，可以使用说话按钮。'); } catch (error) { event.target.checked = false; status(errorText(error)); } };
  const speechOptions = () => ({ engine: state.voiceConfig.voice_provider === 'local' ? state.voiceConfig.local_tts_engine : 'openai' });
  el('test-voice').onclick = () => { if (el('test-voice').disabled) return; stopSpeech(); speech.begin(null, speechOptions()); speech.append('我在这里陪你。准备好了，我们一起看看下一步怎么做。', true); };
  el('record-voice').onclick = () => void toggleRecording();
  el('stop-audio').onclick = () => {
    stopRecording(true); stopSpeech();
    const release = cancelQwenPreload();
    void chat.cancelControlConfirmation().catch((error) => status(errorText(error)));
    status(release ? '已停止声音，正在取消 Qwen 预热并释放资源。' : '已停止声音。');
    return release;
  };
  el('speak-replies').onchange = () => {
    if (!el('speak-replies').checked) { stopSpeech(); return cancelQwenPreload(); }
    state.qwenReleased = false; prepareSpeech();
  };
  const loadSettings = () => {
    void loadPersona();
    loadVoiceConfig().catch((error) => { el('voice-config-status').textContent = errorText(error); });
    void loadMemories(); void loadMemoryConfig();
    void loadBackups();
  };
  window.addEventListener('ai-neko-settings', loadSettings);
  window.addEventListener('ai-neko-connected', loadSettings);
  const settingsObserver = new MutationObserver(() => { if (el('settings-panel').hidden) stopVoicePolling(); });
  settingsObserver.observe(el('settings-panel'), { attributes: true, attributeFilter: ['hidden'] });
  window.addEventListener('pagehide', () => { stopVoicePolling(); settingsObserver.disconnect(); stopRecording(true); stopSpeech(); void cancelQwenPreload(); void disableVision({ notify: false }).catch(() => {}); clearKeys(); });
  bridge?.onAction((action) => { if (action === 'voice-toggle') void toggleRecording(); });
  window.aiNekoCompanion = Object.freeze({
    // Bounded in-memory timings; no text/audio/credentials or acoustic claims.
    voiceTimings: () => voiceTimings.map((item) => ({ ...item })),
    captureForTurn, frameStillAllowed, stopSpeech, stopRecording, clearKeys, flushPlayback, disableVision, prepareSpeech,
    beginTurn: (id, sessionId, binding, control = false, timing = {}) => {
      stopSpeech();
      if (el('speak-replies').checked) {
        state.speechTurn = id;
        state.speechContext = { sessionId, turnId: id, control,
          match: binding ? { match_id: binding.match_id, expected_revision: binding.expected_revision } : null };
        speech.begin(state.speechContext, { ...speechOptions(), submittedAt: timing.submittedAt, inputEndedAt: timing.inputEndedAt });
      }
    },
    updateTurnBinding: (id, binding) => {
      if (state.speechTurn === id && state.speechContext) state.speechContext.match = { match_id: binding.match_id, expected_revision: binding.expected_revision };
    },
    text: (id, value) => { if (state.speechTurn === id && el('speak-replies').checked) speech.append(value); },
    done: (id, completed) => { if (state.speechTurn === id) { if (completed) speech.append('', true); else stopSpeech(); } },
    speakingTurn: (id) => state.speechTurn === id,
  });
})();
