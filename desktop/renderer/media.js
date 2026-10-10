'use strict';
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.aiNekoMedia = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  // Browser-decoded audio stays in memory. A real PCM header and mono samples
  // are produced for both providers so a concurrent provider switch is safe.
  function encodeVoiceWav(audio, { maxSeconds = 60, maxBytes = 8 * 1024 * 1024, truncate = false } = {}) {
    const rate = audio.sampleRate, inputCount = audio.length, channels = audio.numberOfChannels;
    if (!Number.isFinite(rate) || rate < 8000 || rate > 192000 || !Number.isSafeInteger(inputCount) || inputCount <= 0 ||
        !Number.isInteger(channels) || channels < 1 || channels > 32 || (!truncate && inputCount / rate > maxSeconds)) {
      throw new Error('录音为空、格式不受支持或超过60秒，请重新录制。');
    }
    // Only the automatic time-limit stop opts in. MediaRecorder's final chunk
    // can run past the timer; retain the first bounded minute instead of losing it.
    const count = truncate ? Math.min(inputCount, Math.floor(rate * maxSeconds)) : inputCount;
    const targetRate = 16000, frames = Math.max(1, Math.floor(count * targetRate / rate));
    if (44 + frames * 2 > maxBytes) throw new Error('录音超过8MB，请缩短问题。');
    const input = Array.from({ length: channels }, (_, channel) => audio.getChannelData(channel));
    if (input.some((samples) => samples.length !== inputCount)) throw new Error('录音数据不完整，请重新录制。');
    const output = new ArrayBuffer(44 + frames * 2), view = new DataView(output);
    const label = (offset, value) => { for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i)); };
    label(0, 'RIFF'); view.setUint32(4, output.byteLength - 8, true); label(8, 'WAVE');
    label(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, targetRate, true); view.setUint32(28, targetRate * 2, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true); label(36, 'data'); view.setUint32(40, frames * 2, true);
    const mono = (index) => {
      let sample = 0;
      for (const data of input) sample += Number.isFinite(data[index]) ? data[index] : 0;
      return sample / channels;
    };
    const ratio = rate / targetRate;
    for (let i = 0; i < frames; i++) {
      let sample;
      if (ratio >= 1) {
        // Integrate each target sample's source interval, avoiding decimation
        // that simply drops two out of three samples at 48 kHz.
        const start = i * ratio, end = Math.min(count, (i + 1) * ratio);
        let sum = 0;
        for (let j = Math.floor(start); j < Math.ceil(end); j++) sum += mono(j) * (Math.min(end, j + 1) - Math.max(start, j));
        sample = sum / (end - start);
      } else {
        const position = i * ratio, left = Math.floor(position), fraction = position - left;
        sample = mono(left) * (1 - fraction) + mono(Math.min(count - 1, left + 1)) * fraction;
      }
      sample = Math.max(-1, Math.min(1, sample));
      view.setInt16(44 + i * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
    }
    return output;
  }
  // Inspired by N.E.K.O SentenceBuffer (_infra.py:309) and clearAudioQueue
  // (app-audio-playback.js:1382), independently implemented for this runtime.
  // Playback belongs to one generation; no history restoration feeds this queue.
  function speechBoundary(buffer, { qwen, first, final }) {
    const searchable = buffer.replace(/https?:\/\/[^\s)]+/g, (url) => ' '.repeat(url.length));
    const points = Array.from(buffer), cap = 240;
    const hard = /[。！？；….!?;\n]/u, soft = /[，,：:]/u;
    for (let index = 0; index < searchable.length; index++) {
      const char = searchable[index], next = searchable[index + 1] || '', previous = searchable[index - 1] || '';
      if (!hard.test(char) && !(qwen && soft.test(char))) continue;
      if (char === '.') {
        // Preserve decimals, versions, initials and common abbreviations, also
        // when a streaming chunk ends at an ambiguous decimal point.
        if (/[0-9]/.test(previous) && (/[0-9]/.test(next) || (!next && !final))) continue;
        if (/[A-Za-z]/.test(previous) && /[A-Za-z]/.test(next)) continue;
        const prefix = searchable.slice(0, index + 1);
        if (/(?:\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc)|\b[A-Za-z](?:\.[A-Za-z])*)\.$/i.test(prefix)) continue;
      }
      if (soft.test(char) && /[0-9]/.test(previous) && (/[0-9]/.test(next) || (!next && !final))) continue;
      const count = Array.from(buffer.slice(0, index + 1)).length;
      if (!qwen || count >= (first ? 12 : soft.test(char) ? 48 : 32)) return index + 1;
    }
    if (points.length >= cap) {
      let count = points.slice(0, cap).join('').length;
      // Prefer a nearby word boundary for long English text.
      if (qwen && /[A-Za-z0-9]/.test(buffer[count - 1]) && /[A-Za-z0-9]/.test(buffer[count] || '')) {
        const space = buffer.lastIndexOf(' ', count);
        if (space > count / 2) count = space + 1;
      }
      return count;
    }
    return final ? buffer.length : 0;
  }
  class SpeechQueue {
    constructor({ synthesize, play, onError = () => {}, onActivity = () => {}, onTiming = () => {} }) {
      Object.assign(this, { synthesize, play, onError, onActivity, onTiming });
      this.generation = 0; this.controller = new AbortController();
      this.buffer = ''; this.queue = []; this.running = false; this.failed = false; this.context = null;
      this.cursor = 0; this.pendingStart = 0; this.segmentIndex = 0; this.firstText = false; this.run = null; this.engine = 'kokoro'; this.lastPlaybackEnd = null;
    }
    now() { return globalThis.performance?.now() ?? Date.now(); }
    timing(stage, segment, started, metrics = {}) {
      const at = this.now();
      // Numerical lifecycle evidence only; source start/end is not proof of audibility.
      const record = { stage, generation: this.generation, segment_index: segment?.index ?? 0, at_ms: at };
      if (started !== undefined) record.duration_ms = Math.max(0, at - started);
      for (const key of ['at_ms', 'audio_duration_ms', 'synthesis_ms', 'decode_ms', 'gap_ms', 'rtf', 'context_running', 'audio_context_time_ms']) {
        if (Number.isFinite(metrics[key]) && metrics[key] >= 0) record[key] = metrics[key];
      }
      try { this.onTiming(record); } catch { /* Observation must not alter playback. */ }
    }
    stop() {
      if (this.run?.playing) this.timing('playback_stopped', this.run.playing, this.run.playStarted);
      this.timing('generation_stopped');
      this.generation += 1;
      this.controller.abort(); this.controller = new AbortController();
      if (this.run) this.run.next = null;
      this.run = null;
      this.buffer = ''; this.queue = []; this.running = false; this.failed = false; this.context = null;
      this.cursor = 0; this.pendingStart = 0; this.segmentIndex = 0; this.firstText = false;
      this.lastPlaybackEnd = null;
      this.onActivity(false);
    }
    begin(context, { engine = 'kokoro', submittedAt, inputEndedAt } = {}) {
      this.stop(); this.context = context; this.engine = engine;
      if (Number.isFinite(inputEndedAt)) this.timing('input_ended', null, undefined, { at_ms: inputEndedAt });
      if (Number.isFinite(submittedAt)) this.timing('turn_submitted', null, undefined, { at_ms: submittedAt });
    }
    append(text, final = false) {
      if (this.failed) return;
      if (text && !this.firstText) { this.firstText = true; this.timing('first_text'); }
      this.buffer += text;
      while (this.buffer) {
        let count = speechBoundary(this.buffer, { qwen: this.engine === 'qwen', first: this.segmentIndex === 0, final });
        // A stream chunk may end between an emoji's UTF-16 halves.
        if (!final && count && /[\uD800-\uDBFF]/.test(this.buffer[count - 1])) count--;
        if (!count) break;
        const raw = this.buffer.slice(0, count);
        const sentence = raw.replace(/https?:\/\/\S+/g, '').replace(/\[S\d+\]/g, '').replace(/[*#`]/g, '').trim();
        this.buffer = this.buffer.slice(count);
        this.cursor += Array.from(raw).length;
        // Only silent formatting may precede this segment. Its raw range remains
        // contiguous while the server applies the same pronunciation filtering.
        if (/[\p{L}\p{N}]/u.test(sentence)) {
          const segment = { text: sentence, text_start: this.pendingStart, text_end: this.cursor,
            context: this.context, index: ++this.segmentIndex, segment_id: globalThis.crypto.randomUUID().replaceAll('-', '') };
          this.queue.push(segment); this.timing('segment_queued', segment);
          this.pendingStart = this.cursor;
        }
      }
      if (this.run) this.prefetch(this.run);
      void this.drain();
    }
    current(run) { return this.run === run && run.generation === this.generation && !run.signal.aborted; }
    fail(run, error) {
      if (!this.current(run)) return;
      if (run.playing) this.timing('playback_stopped', run.playing, run.playStarted);
      this.timing('generation_failed');
      this.queue = []; this.buffer = ''; this.failed = true; run.next = null;
      // A failed next segment also cancels the current source. Late promises are
      // handled, and cannot refill the queue or reach a later generation.
      this.controller.abort(); this.onActivity(false); this.onError(error);
    }
    prepare(run, segment) {
      const result = (async () => {
        if (!this.current(run)) return {};
        const started = this.now(); this.timing('synthesis_started', segment);
        try {
          const audio = await this.synthesize(segment.text, run.signal, segment);
          if (!this.current(run)) return {};
          this.timing('synthesis_completed', segment, started);
          return { audio, synthesis_ms: Math.max(0, this.now() - started) };
        } catch (error) {
          if (this.current(run)) { this.timing('synthesis_failed', segment, started); this.fail(run, error); }
          return { error };
        }
      })();
      return { segment, result };
    }
    prefetch(run) {
      // One playing segment plus at most one prepared/in-flight successor. Never
      // consume further text until that successor becomes the playing segment.
      if (this.current(run) && run.playing && !run.next && this.queue.length) {
        run.next = this.prepare(run, this.queue.shift());
      }
    }
    async drain() {
      if (this.running || this.failed || !this.queue.length) return;
      this.running = true;
      const run = { generation: this.generation, signal: this.controller.signal, playing: null, next: null };
      this.run = run;
      try {
        let prepared = this.prepare(run, this.queue.shift());
        while (prepared && this.current(run)) {
          const { segment } = prepared;
          const result = await prepared.result;
          if (!this.current(run)) return;
          if (result.error) throw result.error;
          await this.play(result.audio, run.signal, segment, (metrics = {}) => {
            if (!this.current(run) || run.playing) return;
            run.playing = segment; run.playStarted = this.now();
            this.timing('playback_started', segment, undefined, { context_running: metrics.context_running, audio_context_time_ms: metrics.audio_context_time_ms,
              gap_ms: this.lastPlaybackEnd === null ? undefined : run.playStarted - this.lastPlaybackEnd });
            this.onActivity(true); this.prefetch(run);
          }, (metrics = {}) => {
            if (!this.current(run)) return;
            const duration = metrics.audio_duration_ms;
            this.timing('pcm_ready', segment, undefined, { audio_duration_ms: duration, decode_ms: metrics.decode_ms,
              synthesis_ms: result.synthesis_ms, rtf: duration > 0 ? result.synthesis_ms / duration : undefined });
          });
          if (!this.current(run)) return;
          if (run.playing) { this.timing('playback_completed', segment, run.playStarted); this.lastPlaybackEnd = this.now(); }
          run.playing = null;
          this.onActivity(false);
          prepared = run.next || (this.queue.length ? this.prepare(run, this.queue.shift()) : null);
          run.next = null;
        }
      } catch (error) {
        this.fail(run, error);
      } finally {
        if (this.run === run) { this.run = null; this.running = false; this.onActivity(false); }
      }
    }
  }
  return { SpeechQueue, encodeVoiceWav };
});
