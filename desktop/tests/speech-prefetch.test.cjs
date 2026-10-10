'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { SpeechQueue } = require('../renderer/media.js');
const tick = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

function fixture() {
  const synth = [], played = [], errors = [], timing = [];
  const queue = new SpeechQueue({
    synthesize(text, signal) { const wait = deferred(); synth.push({ text, signal, ...wait }); return wait.promise; },
    play(audio, signal, segment, started) {
      const wait = deferred(); played.push({ audio, signal, segment, ...wait });
      signal.addEventListener('abort', () => wait.reject(new DOMException('Stopped', 'AbortError')), { once: true });
      started(); return wait.promise;
    },
    onError: (error) => errors.push(error), onTiming: (value) => timing.push(value),
  });
  return { queue, synth, played, errors, timing };
}

test('only one successor is synthesized during playback, and ready audio is reused in order', async () => {
  const f = fixture(); f.queue.append('One. Two. Three. Four.');
  assert.equal(f.synth.length, 1);
  f.synth[0].resolve('audio-1'); await tick();
  assert.equal(f.played.length, 1); assert.equal(f.synth.length, 2);
  f.synth[1].resolve('audio-2'); await tick();
  assert.equal(f.synth.length, 2, 'a completed prefetch must not start a third synthesis');
  f.played[0].resolve(); await tick();
  assert.deepEqual(f.played.map((p) => p.audio), ['audio-1', 'audio-2']);
  assert.equal(f.synth.length, 3);
  f.queue.stop(); f.synth[2].resolve('late-3'); await tick();
  assert.equal(f.played.length, 2); assert.equal(f.synth.length, 3);
});

test('text arriving during playback fills the single successor slot', async () => {
  const f = fixture(); f.queue.append('One.'); f.synth[0].resolve('audio-1'); await tick();
  f.queue.append(' Two.'); f.queue.append(' Three. Four.');
  assert.equal(f.synth.length, 2);
  f.synth[1].resolve('audio-2'); await tick();
  assert.equal(f.synth.length, 2); f.queue.stop(); await tick();
});

test('stop and generation change abort both playback and prefetch without replaying late audio', async () => {
  const f = fixture(); f.queue.begin({ turnId: 'synthetic-old' }); f.queue.append('Old. Next.');
  f.synth[0].resolve('old'); await tick();
  f.queue.begin({ turnId: 'synthetic-new' }); f.queue.append('New.');
  assert.equal(f.played[0].signal.aborted, true); assert.equal(f.synth[1].signal.aborted, true);
  f.synth[1].resolve('old-next'); f.synth[2].resolve('new'); await tick();
  assert.deepEqual(f.played.map((p) => p.audio), ['old', 'new']);
  assert.equal(f.played[1].segment.context.turnId, 'synthetic-new');
  f.played[1].resolve(); await tick(); assert.deepEqual(f.errors, []);
});

test('prefetch failure stops the current source and prevents later synthesis until a new generation', async () => {
  const f = fixture(); f.queue.append('One. Two. Three.'); f.synth[0].resolve('one'); await tick();
  f.synth[1].reject(new Error('synthetic failure')); await tick();
  assert.equal(f.played[0].signal.aborted, true); assert.equal(f.queue.failed, true);
  f.queue.append('Four.'); await tick();
  assert.equal(f.synth.length, 2); assert.equal(f.played.length, 1); assert.equal(f.errors.length, 1);
  f.queue.stop(); f.queue.append('New.'); f.synth[2].resolve('new'); await tick();
  assert.equal(f.played[1].audio, 'new'); f.played[1].resolve(); await tick();
});

test('playback failure cancels the prepared successor and cannot start another segment', async () => {
  const f = fixture(); f.queue.append('One. Two. Three.'); f.synth[0].resolve('one'); await tick();
  f.played[0].reject(new Error('synthetic output failure')); await tick();
  assert.equal(f.synth[1].signal.aborted, true);
  f.synth[1].reject(new Error('late cancelled synthesis')); await tick();
  assert.equal(f.errors.length, 1); assert.equal(f.synth.length, 2); assert.equal(f.played.length, 1);
});

test('stopping during the successor synthesis prevents a later playback start', async () => {
  const f = fixture(); f.queue.append('One. Two.'); f.synth[0].resolve('one'); await tick();
  f.played[0].resolve(); await tick();
  f.queue.stop(); f.synth[1].resolve('late-two'); await tick();
  assert.equal(f.played.length, 1); assert.equal(f.queue.running, false);
});

test('lifecycle timing is numeric metadata, preserves ordering, and records real start callbacks only', async () => {
  const f = fixture(); f.queue.append('PRIVATE_SYNTHETIC_TEXT.'); f.synth[0].resolve('PRIVATE_AUDIO'); await tick();
  f.played[0].resolve(); await tick();
  assert.deepEqual(f.timing.map((r) => r.stage), ['first_text', 'segment_queued', 'synthesis_started', 'synthesis_completed', 'playback_started', 'playback_completed']);
  for (const record of f.timing) {
    assert.ok(Object.keys(record).every((key) => ['stage', 'generation', 'segment_index', 'at_ms', 'duration_ms'].includes(key)));
    for (const [key, value] of Object.entries(record)) if (key !== 'stage') assert.ok(Number.isFinite(value) && value >= 0);
  }
  assert.equal(JSON.stringify(f.timing).includes('PRIVATE'), false);
  const gate = deferred(), records = [];
  const q = new SpeechQueue({ synthesize: async () => 'synthetic', play: async (_a, _s, _g, start) => { await gate.promise; start(); }, onTiming: (r) => records.push(r) });
  q.append('One.'); await tick(); assert.equal(records.some((r) => r.stage === 'playback_started'), false);
  q.stop(); gate.resolve(); await tick(); assert.equal(records.some((r) => r.stage === 'playback_started'), false);
});

test('Qwen uses a short natural first clause and combines later short sentences with one prefetch', async () => {
  const f = fixture(); f.queue.begin({ turnId: 'synthetic-qwen' }, { engine: 'qwen' });
  const first = '我会先陪你把眼前这件事理清楚，';
  f.queue.append(first);
  assert.equal(f.synth[0].text, first);
  assert.ok(Array.from(first).length >= 12 && Array.from(first).length <= 30);
  f.synth[0].resolve('first'); await tick();
  const short = '先确认手头的目标。再检查需要的材料。';
  f.queue.append(short); assert.equal(f.synth.length, 1);
  const rest = '接着我们一起安排下一步的行动。';
  f.queue.append(rest); assert.equal(f.synth.length, 2);
  assert.equal(f.synth[1].text, short + rest);
  f.queue.append('这句话留到上一段完成后再准备。'.repeat(4), true);
  f.synth[1].resolve('second'); await tick(); assert.equal(f.synth.length, 2);
  f.queue.stop(); await tick(); assert.equal(f.played.length, 1);
});

test('Qwen does not hard-cut a 30-character first clause and flushes a short final reply', async () => {
  const f = fixture(); f.queue.begin(null, { engine: 'qwen' });
  f.queue.append('字'.repeat(40)); assert.equal(f.synth.length, 0);
  f.queue.append('，'); assert.equal(f.synth[0].text, '字'.repeat(40) + '，');
  f.queue.stop(); f.synth[0].resolve('late'); await tick(); assert.equal(f.played.length, 0);
  f.queue.begin(null, { engine: 'qwen' }); f.queue.append('我在。', true);
  assert.equal(f.synth[1].text, '我在。'); f.queue.stop();
});

test('decimal chunk boundaries, times, versions, initials and English abbreviations stay intact', async () => {
  for (const engine of ['kokoro', 'qwen']) {
    const spoken = [];
    const queue = new SpeechQueue({ synthesize: async (text) => text, play: async (text) => spoken.push(text) });
    queue.begin(null, { engine }); queue.append('3.'); await tick(); assert.deepEqual(spoken, []);
    queue.append('14。', true); await tick(); assert.deepEqual(spoken, ['3.14。']);
    spoken.length = 0; queue.begin(null, { engine });
    queue.append('请在 10:'); queue.append('30 提醒 Dr. Lee 查看 U.S.A. 的 v1.2.3，数值是 3.14。结束。', true); await tick();
    for (const token of ['10:30', 'Dr. Lee', 'U.S.A.', 'v1.2.3', '3.14']) assert.ok(spoken.some((text) => text.includes(token)), `${engine}: ${token}`);
    assert.equal(spoken.some((text) => /(?:Dr|U|S|v1|3)\.$/.test(text)), false);
  }
});

test('one generation connects input, submission, first text, segment, PCM and playback with RTF and gaps', async () => {
  let now = 20; const records = [], synthesis = [], playback = [];
  const queue = new SpeechQueue({
    synthesize() { const wait = deferred(); synthesis.push(wait); return wait.promise; },
    play(_audio, _signal, _segment, started, ready) {
      const wait = deferred(); playback.push(wait);
      now += 10; ready({ audio_duration_ms: 1000, decode_ms: 2, text: 'DO_NOT_RECORD' }); started(); return wait.promise;
    }, onTiming: (record) => records.push(record),
  });
  queue.now = () => now;
  queue.begin(null, { submittedAt: 10, inputEndedAt: 5 }); records.shift();
  queue.append('First.'); now = 50; synthesis[0].resolve('synthetic'); await tick();
  now = 80; playback[0].resolve(); await tick();
  now = 90; queue.append('Second.'); now = 130; synthesis[1].resolve('synthetic'); await tick();
  assert.equal(new Set(records.map((r) => r.generation)).size, 1);
  assert.deepEqual(records.slice(0, 5).map((r) => r.stage), ['input_ended', 'turn_submitted', 'first_text', 'segment_queued', 'synthesis_started']);
  assert.equal(records[0].at_ms, 5); assert.equal(records[1].at_ms, 10);
  const pcm = records.filter((r) => r.stage === 'pcm_ready');
  assert.equal(pcm[0].audio_duration_ms, 1000); assert.equal(pcm[0].synthesis_ms, 30); assert.equal(pcm[0].rtf, 0.03);
  assert.equal(records.filter((r) => r.stage === 'playback_started')[1].gap_ms, 60);
  assert.equal(JSON.stringify(records).includes('DO_NOT_RECORD'), false);
  for (const row of records) for (const [key, value] of Object.entries(row)) if (key !== 'stage') assert.ok(Number.isFinite(value) && value >= 0);
  queue.stop(); playback[1].resolve(); await tick();
});

test('late decode callbacks after stop cannot record PCM-ready or playback-start stages', async () => {
  const wait = deferred(), records = [];
  const queue = new SpeechQueue({ synthesize: async () => 'synthetic',
    play: async (_audio, _signal, _segment, started, ready) => { await wait.promise; ready({ audio_duration_ms: 100 }); started(); },
    onTiming: (record) => records.push(record),
  });
  queue.append('One.'); await tick(); queue.stop(); wait.resolve(); await tick();
  assert.equal(records.some((r) => r.stage === 'pcm_ready' || r.stage === 'playback_started'), false);
});
