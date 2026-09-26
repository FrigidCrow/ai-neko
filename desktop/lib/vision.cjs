'use strict';
const { randomUUID } = require('node:crypto');

// No stream, timer, disk cache or whole-screen fallback. Only the chosen source
// crosses IPC, and revoking permission invalidates captures already in flight.
class VisionCapture {
  constructor(getSources) { this.getSources = getSources; this.epoch = 0; this.selected = null; this.listed = new Map(); }
  async list() {
    const sources = await this.getSources({ types: ['window', 'screen'], thumbnailSize: { width: 0, height: 0 }, fetchWindowIcons: false });
    this.listed = new Map(sources.slice(0, 250).map((source) => [source.id, { id: source.id, name: source.name.slice(0, 240), kind: source.id.startsWith('screen:') ? 'screen' : 'window' }]));
    return [...this.listed.values()];
  }
  disable() { this.epoch += 1; this.selected = null; return { enabled: false }; }
  async select(id) {
    this.disable();
    if (typeof id !== 'string' || !this.listed.has(id)) throw new Error('请刷新列表并明确选择一个画面来源。');
    this.selected = this.listed.get(id);
    const epoch = this.epoch;
    try { return await this.capture(); }
    catch (error) { if (this.epoch === epoch) this.disable(); throw error; }
  }
  async capture() {
    const selected = this.selected;
    const epoch = this.epoch;
    if (!selected) throw new Error('观察未开启，请先选择窗口或屏幕。');
    const sources = await this.getSources({ types: [selected.kind], thumbnailSize: { width: 1366, height: 768 }, fetchWindowIcons: false });
    if (epoch !== this.epoch || this.selected !== selected) throw new Error('观察已关闭，本次图片已丢弃。');
    const source = sources.find((item) => item.id === selected.id && item.name.slice(0, 240) === selected.name);
    if (!source || source.thumbnail.isEmpty()) { this.disable(); throw new Error('所选窗口已关闭、改名或无法采集；请重新选择，不会切换到其他画面。'); }
    const pixels = source.thumbnail.toBitmap();
    // Black-screen detection needs only one visible pixel; sample a sparse grid
    // instead of walking every pixel synchronously in the main process.
    const stride = pixels.length > 262144 ? 64 : 4;
    let visible = false;
    for (let offset = 0; offset + 3 < pixels.length; offset += stride) {
      if (pixels[offset + 3] > 0 && (pixels[offset] > 3 || pixels[offset + 1] > 3 || pixels[offset + 2] > 3)) { visible = true; break; }
    }
    if (!visible) throw new Error('所选来源是黑屏或透明画面，请恢复窗口后重试。');
    const bytes = source.thumbnail.toJPEG(82);
    if (!bytes.length || bytes.length > 2 * 1024 * 1024) throw new Error('截图为空或过大，请重新选择来源。');
    return { frame_id: randomUUID(), source_id: selected.id, source_name: selected.name,
      captured_at: Date.now(), data_url: `data:image/jpeg;base64,${bytes.toString('base64')}` };
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
