'use strict';
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.aiNekoMedia = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  // Browser-decoded audio stays in memory. A real PCM header and mono samples
  // are produced for both providers so a concurrent provider switch is safe.
  function encodeVoiceWav(audio, { maxSeconds = 60, maxBytes = 8 * 1024 * 1024 } = {}) {
    const rate = audio.sampleRate, count = audio.length, channels = audio.numberOfChannels;
    if (!Number.isFinite(rate) || rate < 8000 || rate > 192000 || !Number.isSafeInteger(count) || count <= 0 ||
        !Number.isInteger(channels) || channels < 1 || channels > 32 || count / rate > maxSeconds) {
      throw new Error('录音为空、格式不受支持或超过60秒，请重新录制。');
    }
    const targetRate = 16000, frames = Math.max(1, Math.floor(count * targetRate / rate));
    if (44 + frames * 2 > maxBytes) throw new Error('录音超过8MB，请缩短问题。');
    const input = Array.from({ length: channels }, (_, channel) => audio.getChannelData(channel));
    if (input.some((samples) => samples.length !== count)) throw new Error('录音数据不完整，请重新录制。');
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
  class SpeechQueue {
    constructor({ synthesize, play, onError = () => {}, onActivity = () => {} }) {
      Object.assign(this, { synthesize, play, onError, onActivity });
      this.generation = 0; this.controller = new AbortController();
      this.buffer = ''; this.queue = []; this.running = false; this.failed = false; this.context = null;
      this.cursor = 0; this.pendingStart = 0;
    }
    stop() {
      this.generation += 1;
      this.controller.abort(); this.controller = new AbortController();
      this.buffer = ''; this.queue = []; this.running = false; this.failed = false; this.context = null;
      this.cursor = 0; this.pendingStart = 0;
      this.onActivity(false);
    }
    begin(context) { this.stop(); this.context = context; }
    append(text, final = false) {
      if (this.failed) return;
      this.buffer += text;
      while (this.buffer) {
        const searchable = this.buffer.replace(/https?:\/\/[^\s)]+/g, (url) => ' '.repeat(url.length));
        const end = searchable.search(/[。！？；….!?;\n]/u);
        const points = Array.from(this.buffer);
        let count = end >= 0 ? end + 1 : points.length >= 240 ? points.slice(0, 240).join('').length : final ? this.buffer.length : 0;
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
          this.queue.push({ text: sentence, text_start: this.pendingStart, text_end: this.cursor,
            context: this.context, segment_id: globalThis.crypto.randomUUID().replaceAll('-', '') });
          this.pendingStart = this.cursor;
        }
      }
      void this.drain();
    }
    async drain() {
      if (this.running || !this.queue.length) return;
      this.running = true;
      const generation = this.generation;
      const signal = this.controller.signal;
      try {
        while (this.queue.length && generation === this.generation && !signal.aborted) {
          const segment = this.queue.shift();
          const audio = await this.synthesize(segment.text, signal, segment);
          if (signal.aborted || generation !== this.generation) return;
          await this.play(audio, signal, segment, () => this.onActivity(true));
          if (signal.aborted || generation !== this.generation) return;
          this.onActivity(false);
        }
      } catch (error) {
        if (!signal.aborted && generation === this.generation) { this.queue = []; this.buffer = ''; this.failed = true; this.onError(error); }
      } finally {
        if (generation === this.generation) { this.running = false; this.onActivity(false); }
      }
    }
  }
  return { SpeechQueue, encodeVoiceWav };
});
