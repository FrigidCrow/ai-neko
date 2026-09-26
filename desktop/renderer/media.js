'use strict';
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.aiNekoMedia = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, () => {
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
          const audio = await this.synthesize(segment.text, signal);
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
  return { SpeechQueue };
});
