'use strict';
const { randomUUID } = require('node:crypto');
const metadataOptions = () => ({ types: ['window'], thumbnailSize: { width: 0, height: 0 }, fetchWindowIcons: false });
const revoked = () => new Error('观察已关闭，过期图片已丢弃。');
const unavailable = () => new Error('所选窗口已关闭、变化或无法采集；请重新选择，不会切换到其他画面。');

// Enumeration never requests pixels. Native identity stays in main; the
// renderer may select a listed ID but cannot supply its own capture identity.
class VisionCapture {
  constructor(getSources, adapter, decode) {
    this.getSources = getSources; this.adapter = adapter; this.decode = decode;
    this.epoch = 0; this.listRevision = 0; this.selected = null; this.listed = new Map();
    this.operations = new Set(); this.capturing = null;
  }
  current(epoch, selected = this.selected) {
    if (this.epoch !== epoch || this.selected !== selected) throw revoked();
  }
  async native(operation, value) {
    if (!this.adapter) throw new Error('此平台暂不支持严格限定窗口的观察。');
    const record = { controller: new AbortController(), promise: null };
    this.operations.add(record);
    record.promise = Promise.resolve().then(() => {
      if (record.controller.signal.aborted) throw revoked();
      return this.adapter[operation](value, { signal: record.controller.signal });
    });
    try { return await record.promise; }
    finally { this.operations.delete(record); }
  }
  async list() {
    this.disable();
    this.listed.clear();
    const epoch = this.epoch, revision = ++this.listRevision;
    await this.settled();
    this.current(epoch);
    const sources = (await this.getSources(metadataOptions())).filter((source) =>
      /^window:[1-9][0-9]*:[01]$/.test(source.id) && typeof source.name === 'string' && source.name.length <= 4096).slice(0, 250);
    this.current(epoch);
    if (revision !== this.listRevision) throw revoked();
    const identities = await this.native('identify', sources.map(({ id, name }) => ({ id, name })));
    this.current(epoch);
    if (revision !== this.listRevision) throw revoked();
    this.listed = new Map(identities.filter((identity) => sources.some((source) =>
      source.id === identity.id && source.name === identity.name)).map((identity) => [identity.id, Object.freeze({ ...identity, kind: 'window' })]));
    return [...this.listed.values()].map(({ id, name, kind }) => ({ id, name: name.slice(0, 240), kind }));
  }
  disable() {
    this.epoch += 1; this.listRevision += 1; this.selected = null;
    for (const record of this.operations) record.controller.abort();
    return { enabled: false };
  }
  async settled() { await Promise.allSettled([...this.operations].map((record) => record.promise)); }
  async stop() { const result = this.disable(); await this.settled(); return result; }
  async select(id) {
    this.disable();
    if (typeof id !== 'string' || !this.listed.has(id)) throw new Error('请刷新列表并明确选择一个窗口来源。');
    this.selected = this.listed.get(id);
    const epoch = this.epoch;
    try { await this.settled(); this.current(epoch); return await this.capture(); }
    catch (error) { if (this.epoch === epoch) this.disable(); throw error; }
  }
  async capture() {
    const selected = this.selected, epoch = this.epoch;
    if (!selected) throw new Error('观察未开启，请先选择窗口。');
    if (this.capturing?.epoch === epoch) throw new Error('窗口截图正在进行，请稍后重试。');
    const capture = { epoch }; this.capturing = capture;
    try {
      const matches = (sources) => sources.some((source) => source.id === selected.id && source.name === selected.name);
      const sources = await this.getSources(metadataOptions());
      this.current(epoch, selected);
      if (!matches(sources)) throw unavailable();
      const bytes = await this.native('capture', selected);
      this.current(epoch, selected);
      const after = await this.getSources(metadataOptions());
      this.current(epoch, selected);
      if (!matches(after)) throw unavailable();
      if (!Buffer.isBuffer(bytes) || !bytes.length || bytes.length > 2 * 1024 * 1024) throw new Error('截图为空或过大，请重新选择来源。');
      const frame = this.decode(bytes);
      if (frame.isEmpty()) throw unavailable();
      const size = frame.getSize();
      if (!size.width || !size.height || size.width > 1366 || size.height > 768) throw unavailable();
      const pixels = frame.toBitmap();
      const stride = pixels.length > 262144 ? 64 : 4;
      let visible = false;
      for (let offset = 0; offset + 3 < pixels.length; offset += stride) {
        if (pixels[offset + 3] > 0 && (pixels[offset] > 3 || pixels[offset + 1] > 3 || pixels[offset + 2] > 3)) { visible = true; break; }
      }
      if (!visible) throw new Error('所选来源是黑屏或透明画面，请恢复窗口后重试。');
      return { frame_id: randomUUID(), source_id: selected.id, source_name: selected.name.slice(0, 240),
        captured_at: Date.now(), data_url: `data:image/jpeg;base64,${bytes.toString('base64')}` };
    } catch (error) {
      if (this.epoch !== epoch || this.selected !== selected) throw revoked();
      this.disable(); throw error;
    } finally { if (this.capturing === capture) this.capturing = null; }
  }
}
function microphonePermission(contents, mainContents, permission, details, allowedUntil, now = Date.now()) {
  return Boolean(contents && contents === mainContents && !contents.isDestroyed() &&
    contents.mainFrame?.url === 'ai-neko://app/renderer/index.html' &&
    permission === 'media' && now < allowedUntil && details?.isMainFrame !== false &&
    (!details?.requestingUrl || details.requestingUrl === 'ai-neko://app/renderer/index.html') &&
    (details?.mediaType === 'audio' || (Array.isArray(details?.mediaTypes) &&
      details.mediaTypes.length === 1 && details.mediaTypes[0] === 'audio')));
}
module.exports = { VisionCapture, microphonePermission };
