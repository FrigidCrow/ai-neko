'use strict';

(() => {
  const el = (id) => document.getElementById(id);
  const node = (tag, className, text) => {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = String(text);
    return result;
  };
  const dimensions = ['game', 'platform', 'mode'];
  const guideId = (value) => typeof value === 'string' && /^guide-[a-f0-9]{32}$/.test(value);
  const revisionId = (value) => typeof value === 'string' && /^revision-[a-f0-9]{32}$/.test(value);
  const publicURL = (value) => {
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch { return null; }
  };
  const date = (value) => {
    if (!value) return '未知';
    const parsed = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value);
    return Number.isNaN(parsed.getTime()) ? '未知' : parsed.toLocaleString('zh-CN', {
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  };
  const gameLabel = (value) => dimensions.map((key) => value?.[key] || ({ game: '游戏未填写', platform: '平台未填写', mode: '模式未填写' }[key])).join(' · ');
  const matchStatus = (value) => ({ active: '本局进行中', needs_update: '局势待更新', ended: '已结束' }[value] || '尚未开始对局');
  const versionLabel = (value) => value?.game_version ? `游戏版本 ${value.game_version}` : '游戏版本未核实';

  function create({ api, runControl, onReview, showPanel, friendlyError }) {
    const state = {
      guideCatalog: { revision: 0, guides: [], selections: [] },
      matchCatalog: { revision: 0, current: null, matches: [] },
      connected: false, busy: false, localBusy: false, sessionId: null,
      candidate: null, selected: null, sourceEpoch: 0, backupEpoch: 0,
      confirmation: null, matchDirty: false, matchFormId: undefined,
      librarySignature: null, matchSignature: null, backupsLoaded: false,
      adoptedMetadata: new Map(), adoptedEpoch: 0, evidenceSignature: null,
    };
    const selectedSource = () => state.selected ? { ...state.selected } : null;
    const locked = () => !state.connected || state.busy || state.localBusy;
    function status(message, error = false) {
      el('guide-status').textContent = message || '';
      el('guide-status').classList.toggle('is-error', error);
    }
    function errorMessage(error, resource = 'guide') {
      if (error?.status === 404) return resource === 'match'
        ? '这局记录已不可用，请重新载入当前对话后再试。'
        : '这份攻略或快照已不可用，请重新载入攻略库后再选择。';
      return error?.status === 409
        ? '资料或对局已更新。请核对当前状态，再重新确认这次操作。'
        : friendlyError(error || {});
    }
    function button(text, className, action) {
      const result = node('button', className, text);
      result.type = 'button';
      result.dataset.guideControl = 'true';
      result.disabled = locked();
      result.addEventListener('click', action);
      return result;
    }
    function setBusy() {
      for (const item of document.querySelectorAll('#guide-panel button, .source-adopt')) {
        if (['close-guides', 'cancel-guide-adoption', 'cancel-guide-action', 'cancel-guide-operation'].includes(item.id)) continue;
        item.disabled = locked() || item.dataset.unavailable === 'true';
      }
      el('guide-panel').setAttribute('aria-busy', String(state.busy || state.localBusy));
    }
    async function task(operation, message, resource = 'guide') {
      if (locked()) return null;
      state.localBusy = true;
      setBusy();
      status('正在处理…');
      try {
        const result = await operation();
        if (message) status(typeof message === 'function' ? message(result) : message);
        else if (el('guide-status').textContent === '正在处理…') status('');
        return result;
      } catch (error) {
        status(errorMessage(error, resource), true);
        return null;
      } finally {
        state.localBusy = false;
        setBusy();
      }
    }
    function control(kind, fields = {}, targetId, expectedRevision = state.guideCatalog.revision) {
      return runControl({ kind, fields, expectedRevision, ...(targetId ? { targetId } : {}) });
    }
    function closeConfirmation() {
      state.confirmation = null;
      el('guide-confirmation').hidden = true;
    }
    function confirm(title, detail, label, operation) {
      if (locked()) return;
      state.confirmation = operation;
      el('guide-confirm-title').textContent = title;
      el('guide-confirm-detail').textContent = detail;
      el('confirm-guide-action').textContent = label;
      el('guide-confirmation').hidden = false;
      el('guide-confirmation').scrollIntoView({ block: 'nearest' });
      el('cancel-guide-action').focus();
    }
    function sourceCandidate(source) {
      const id = source?.guide_id || source?.storage?.guide_id;
      const revision = source?.revision_id || source?.storage?.revision_id;
      return {
        guide_id: guideId(id) ? id : null,
        revision_id: revisionId(revision) ? revision : null,
        url: publicURL(source?.original_url || source?.url || source?.final_url),
        title: typeof source?.title === 'string' ? source.title : '这份公开资料',
        ...Object.fromEntries([...dimensions, 'game_version', 'version_basis'].map((key) => [key, typeof source?.[key] === 'string' ? source[key] : ''])),
      };
    }
    function fillAdoption(candidate) {
      for (const key of dimensions) el(`guide-adopt-${key}`).value = candidate[key] || '';
      el('guide-adopt-version').value = candidate.game_version || '';
      el('guide-adopt-version-basis').value = candidate.version_basis || '';
      const saved = Boolean(candidate.guide_id && candidate.revision_id);
      el('guide-adopt-version').readOnly = saved;
      el('guide-adopt-version-basis').readOnly = saved;
      el('guide-source-title').textContent = candidate.title;
      el('guide-source-state').textContent = saved
        ? `${versionLabel(candidate)} · ${candidate.completeness === 'partial' ? '部分正文' : '本地正文'}。将采用所选正文版本。`
        : '先读取并保存公开正文，成功后采用。摘要或读取失败不会显示采用成功。';
      el('guide-adopt').textContent = saved ? '确认采用这份攻略' : '读取、保存并采用';
    }
    function selectVerified(document) {
      state.selected = dimensions.every((key) => typeof document[key] === 'string' && document[key].trim())
        ? { guide_id: document.guide_id, revision_id: document.revision_id, ...Object.fromEntries(dimensions.map((key) => [key, document[key]])) }
        : null;
    }
    async function openSource(source) {
      if (locked()) return;
      showPanel('guides');
      closeConfirmation();
      const epoch = ++state.sourceEpoch;
      state.selected = null;
      state.candidate = sourceCandidate(source);
      el('guide-adoption').hidden = false;
      fillAdoption(state.candidate);
      if (state.candidate.guide_id && state.candidate.revision_id) {
        status('正在核对保存的正文版本…');
        try {
          const detail = await api(`/api/guides/${encodeURIComponent(state.candidate.guide_id)}?revision_id=${encodeURIComponent(state.candidate.revision_id)}`);
          if (epoch !== state.sourceEpoch) return;
          state.candidate = { ...sourceCandidate(detail.guide), completeness: detail.guide.completeness, catalogRevision: detail.revision };
          fillAdoption(state.candidate);
          selectVerified(state.candidate);
          status('请确认游戏条件，再采用这份正文。');
        } catch (error) {
          if (epoch !== state.sourceEpoch) return;
          state.candidate = null;
          el('guide-source-state').textContent = '这份正文版本暂时无法核对，请从攻略库重新选择。';
          status(errorMessage(error), true);
        }
      } else status('请填写明确的游戏条件；未知游戏版本可以留空。');
      if (epoch === state.sourceEpoch) {
        el('guide-adoption').scrollIntoView({ block: 'start' });
        el('guide-adopt-game').focus();
      }
    }
    function sourceAction(source) {
      const candidate = sourceCandidate(source);
      const result = button('采用这份攻略', 'soft-button source-adopt', () => { void openSource(candidate); });
      if (candidate.guide_id) result.dataset.guideId = candidate.guide_id;
      if (!candidate.url && !(candidate.guide_id && candidate.revision_id)) {
        result.disabled = true;
        result.dataset.unavailable = 'true';
        result.title = '此来源没有可读取的公开地址';
      }
      return result;
    }
    function readFields(prefix, adopt = false) {
      const fields = Object.fromEntries(dimensions.map((key) => [key, el(`${prefix}-${key}`).value.trim()]));
      if (adopt && dimensions.some((key) => !fields[key])) throw new Error('请明确填写游戏、平台和模式。');
      const version = el(`${prefix}-version`).value.trim();
      const basis = el(`${prefix}-version-basis`).value.trim();
      if (version && !basis) throw new Error('请补充游戏版本的依据，或将未知版本留空。');
      if (version) Object.assign(fields, { game_version: version, version_basis: basis });
      return fields;
    }
    function localError(message) { status(message, true); }
    async function adoptSource(event) {
      event.preventDefault();
      if (!state.candidate || !el('guide-selection-form').reportValidity()) return;
      let fields;
      try {
        fields = state.candidate.guide_id
          ? Object.fromEntries(dimensions.map((key) => [key, el(`guide-adopt-${key}`).value.trim()]))
          : readFields('guide-adopt', true);
      } catch (error) { localError(error.message); return; }
      if (dimensions.some((key) => !fields[key])) { localError('请明确填写游戏、平台和模式。'); return; }
      const candidate = { ...state.candidate };
      const epoch = state.sourceEpoch;
      const result = await task(async () => {
        let id = candidate.guide_id;
        let revision = candidate.revision_id;
        if (!id || !revision) {
          if (!candidate.url) throw Object.assign(new Error('Missing URL'), { publicMessage: '请提供可读取的公开网页地址。' });
          const fetched = await control('guide-fetch', { url: candidate.url, ...fields });
          const saved = fetched?.result || fetched;
          id = saved?.guide_id;
          revision = saved?.revision_id;
          if (!guideId(id) || !revisionId(revision)) throw Object.assign(new Error('Guide not saved'), { publicMessage: '正文尚未保存成功，本次没有采用。' });
        }
        const detail = await api(`/api/guides/${encodeURIComponent(id)}?revision_id=${encodeURIComponent(revision)}`);
        if (epoch !== state.sourceEpoch) throw new DOMException('Cancelled', 'AbortError');
        if (!detail.guide?.text?.trim()) throw Object.assign(new Error('Missing body'), { publicMessage: '这份资料没有可采用的正文。' });
        const selected = await control('guide-select', { ...Object.fromEntries(dimensions.map((key) => [key, fields[key]])), guide_id: id, revision_id: revision }, undefined, detail.revision);
        selectVerified({ ...detail.guide, ...fields });
        return selected;
      }, '已采用所选正文版本，之后提问会优先参考这份攻略。');
      if (result) el('guide-adoption').hidden = true;
    }
    async function saveGuide(adopt = false) {
      if (!el('guide-save-form').reportValidity()) return;
      const url = publicURL(el('guide-url').value.trim());
      if (!url) { localError('请填写不含账号密码的公开 HTTP 或 HTTPS 网页地址。'); return; }
      let fields;
      try { fields = readFields('guide', adopt); }
      catch (error) { localError(error.message); return; }
      if (adopt) { await openSource({ url, ...fields, title: '你指定的公开网页' }); return; }
      await task(async () => {
        const response = await control('guide-fetch', { url, ...fields });
        const saved = response?.result || response;
        if (!guideId(saved?.guide_id) || !revisionId(saved?.revision_id)) throw Object.assign(new Error('Guide not saved'), { publicMessage: '正文尚未保存成功，请核对任务结果后重试。' });
        return saved;
      }, '正文已保存到本机；尚未采用，可在攻略库选择。');
    }
    function renderSelections() {
      const container = el('guide-selection-list');
      container.replaceChildren();
      for (const selection of state.guideCatalog.selections || []) {
        const document = (state.guideCatalog.guides || []).find((item) => item.guide_id === selection.guide_id);
        const card = node('div', 'guide-selection');
        card.append(node('strong', '', document?.title || '已采用的攻略'), node('p', 'field-help', gameLabel(selection)));
        const unselect = button('取消采用', 'text-button guide-unadopt', () => {
          void task(() => control('guide-unselect', { ...Object.fromEntries(dimensions.map((key) => [key, selection[key]])), guide_id: null, revision_id: null }), '已取消采用，正文仍保留在攻略库。');
        });
        card.append(unselect);
        container.append(card);
      }
    }
    function descriptor(document) {
      return `${gameLabel(document)}\n${versionLabel(document)} · ${document.completeness === 'partial' ? '部分正文' : '已保存正文'}\n内容日期：${date(document.content_date)}\n最后核查：${date(document.last_checked_at)}`;
    }
    function renderLibrary() {
      const container = el('guide-library-list');
      container.replaceChildren();
      renderSelections();
      const guides = state.guideCatalog.guides || [];
      if (!guides.length) container.append(node('p', 'guide-empty', '攻略库还是空的。可以从聊天来源卡片采用，或保存一个公开网页。'));
      for (const document of guides) {
        const card = node('article', 'guide-card');
        card.dataset.guideId = document.guide_id;
        card.append(node('strong', 'guide-document-title', document.title || '公开攻略'), node('p', 'guide-metadata', descriptor(document)));
        const selections = (state.guideCatalog.selections || []).filter((item) => item.guide_id === document.guide_id);
        const latestSelected = selections.some((item) => item.revision_id === document.revision_id);
        if (selections.length) card.append(node('p', 'guide-selection-note', latestSelected ? '正在采用这份正文' : '已有新正文版本；当前采用版本保持不变'));
        const actions = node('div', 'guide-actions');
        actions.append(button(selections.length && !latestSelected ? '切换到这份新正文' : '采用这份攻略', `soft-button ${selections.length && !latestSelected ? 'guide-switch' : 'guide-select'}`, () => { void openSource(document); }));
        actions.append(button('核查网页更新', 'text-button guide-refresh', () => {
          void task(() => control('guide-refresh', {}, document.guide_id), (value) => (value?.result || value)?.not_modified ? '原文未变，已更新核查时间；游戏版本仍需单独核实。' : '网页核查完成。若有新正文，请明确选择后再切换采用版本。');
        }));
        actions.append(button('删除本地攻略', 'text-button guide-delete', () => {
          const revision = state.guideCatalog.revision;
          confirm('删除这份本地攻略？', `${document.title || '这份攻略'}\n将删除正文及相关派生回答，并解除采用关系。之后恢复旧快照也不会恢复它。`, '删除攻略', () => task(() => control('guide-delete', { confirm: true }, document.guide_id, revision), '本地攻略及相关资料已删除。'));
        }));
        card.append(actions);
        const details = node('details', 'guide-document-detail');
        details.append(node('summary', '', '查看正文与已保存版本'));
        const content = node('div', 'guide-detail-content');
        details.append(content);
        let detailEpoch = 0;
        details.addEventListener('toggle', async () => {
          const epoch = ++detailEpoch;
          if (!details.open) { content.replaceChildren(); return; }
          content.replaceChildren(node('p', 'field-help', '正在读取本地正文…'));
          try {
            const response = await api(`/api/guides/${encodeURIComponent(document.guide_id)}`);
            if (epoch !== detailEpoch || !details.isConnected || !details.open) return;
            const full = response.guide;
            selectVerified(full);
            content.replaceChildren(node('p', 'guide-body', full.text || '暂无可显示的正文。'));
            const versions = node('div', 'guide-revisions');
            for (const revision of [...(full.revisions || [])].reverse()) {
              const line = node('div', 'guide-revision');
              line.append(node('p', 'field-help', `${versionLabel(revision)} · 保存于 ${date(revision.revision_created_at)}`));
              line.append(button('采用此正文版本', 'text-button guide-select-revision', () => { void openSource(revision); }));
              versions.append(line);
            }
            content.append(versions);
          } catch (error) {
            if (epoch === detailEpoch && details.isConnected) content.replaceChildren(node('p', 'settings-message is-error', errorMessage(error)));
          }
        });
        card.append(details);
        container.append(card);
      }
    }
    function matchFields() {
      return { ...Object.fromEntries(dimensions.map((key) => [key, el(`match-${key}`).value.trim()])), game_version: el('match-version').value.trim() || null, goal: el('match-goal').value.trim() };
    }
    async function changeMatch(kind) {
      const current = state.matchCatalog.current;
      if (['match-start', 'match-new'].includes(kind) && !el('match-form').reportValidity()) return;
      const all = matchFields();
      if (['match-start', 'match-new'].includes(kind) && dimensions.some((key) => !all[key])) { localError('请明确填写游戏、平台和模式。'); return; }
      const fields = ['match-start', 'match-new'].includes(kind) ? all : kind === 'match-update' ? { goal: all.goal, game_version: all.game_version } : {};
      const result = await task(() => control(kind, fields, kind === 'match-start' ? undefined : current?.match_id, state.matchCatalog.revision), {
        'match-start': '本局已开始。请提供当前描述，或允许观察新画面。',
        'match-new': '已开始新对局，旧局势与旧建议保留在历史中。',
        'match-update': '本局目标与游戏版本已更新。',
        'match-end': '本局已结束。仍可查攻略或明确复盘已有对局。',
        'match-close-observation': '本局观察已关闭，可继续描述当前局势。',
      }[kind], 'match');
      if (result) { state.matchDirty = false; renderMatch(); }
    }
    function evidenceStatus(current) {
      if (!current) return '尚未开始对局';
      if (current.status !== 'active') return matchStatus(current.status);
      const now = Date.now() / 1000;
      const observations = current.observations || [];
      const valid = observations.filter((item) => item.expires_at >= now && item.observed_at <= now);
      if (valid.some((item) => !item.unknown)) return '本局进行中 · 有当前证据';
      if (valid.length) return '本局进行中 · 画面信息未知';
      return observations.length ? '局势已过期 · 等待更新' : '本局进行中 · 等待当前局势';
    }
    function renderEvidence() {
      const current = state.matchCatalog.current;
      el('current-match-label').textContent = current ? `${current.game} · ${evidenceStatus(current)}` : '尚未开始对局';
      const now = Date.now() / 1000;
      const observations = current?.status === 'active' ? (current.observations || []).filter((item) => item.expires_at >= now && item.observed_at <= now) : [];
      const signature = JSON.stringify([current?.match_id, evidenceStatus(current), observations.map((item) => [item.observation_id, item.unknown, item.text, item.expires_at])]);
      if (signature === state.evidenceSignature) return;
      state.evidenceSignature = signature;
      const container = el('match-evidence');
      container.replaceChildren();
      if (!current) return;
      if (!observations.length) {
        container.append(node('p', 'field-help', `${evidenceStatus(current)}。请提供新的描述或画面，旧数值不会作为当前依据。`));
        return;
      }
      for (const observation of observations) {
        const source = observation.source_kind === 'vision' ? '本轮画面' : observation.source_kind === 'user_correction' ? '你确认的描述' : '你的描述';
        const card = node('div', 'match-observation');
        card.append(node('strong', '', `${source} · ${observation.unknown ? '未知' : '当前有效'}`));
        card.append(node('p', '', observation.unknown ? '没有识别出可确认的局势字段，请补充描述或新画面。' : observation.text));
        card.append(node('small', '', `记录于 ${date(observation.observed_at)} · 有效至 ${date(observation.expires_at)}`));
        container.append(card);
      }
    }
    function renderGuideContext() {
      const active = state.matchCatalog.current;
      const selections = state.guideCatalog.selections || [];
      const chosen = active ? active.selection : selections.length === 1 ? selections[0] : null;
      const label = el('current-guide-label');
      if (!chosen) {
        label.textContent = active ? '本局尚未采用匹配攻略' : selections.length ? `已采用 ${selections.length} 份攻略 · 按游戏条件使用` : '尚未采用攻略';
        state.adoptedEpoch += 1;
        return;
      }
      const listed = (state.guideCatalog.guides || []).find((item) => item.guide_id === chosen.guide_id);
      const key = `${chosen.guide_id}:${chosen.revision_id}`;
      const exact = state.adoptedMetadata.get(key) || (listed?.revision_id === chosen.revision_id ? listed : null);
      label.textContent = `参考：${exact?.title || listed?.title || '已采用攻略'} · ${versionLabel(exact)}`;
      if (exact) return;
      const epoch = ++state.adoptedEpoch;
      void api(`/api/guides/${encodeURIComponent(chosen.guide_id)}?revision_id=${encodeURIComponent(chosen.revision_id)}`).then((detail) => {
        if (epoch !== state.adoptedEpoch) return;
        // Retain only display metadata; never leave a second body copy in panel state.
        state.adoptedMetadata.set(key, { title: detail.guide.title, game_version: detail.guide.game_version });
        renderGuideContext();
      }).catch(() => { /* Keep the explicit unknown-version label until a successful reload. */ });
    }
    function renderMatch() {
      const current = state.matchCatalog.current;
      renderEvidence();
      el('manage-match').textContent = current ? '管理本局' : '开始陪玩';
      el('match-summary').textContent = current ? `${gameLabel(current)}\n${matchStatus(current.status)} · ${versionLabel(current)}${current.goal ? `\n本局目标：${current.goal}` : ''}` : '开始一局，让小猫只结合这一局的当前证据陪你。';
      if (state.matchFormId !== (current?.match_id || null) || !state.matchDirty) {
        for (const key of dimensions) el(`match-${key}`).value = current?.[key] || '';
        el('match-version').value = current?.game_version || '';
        el('match-goal').value = current?.goal || '';
        state.matchFormId = current?.match_id || null;
        state.matchDirty = false;
      }
      el('match-start').hidden = Boolean(current);
      for (const id of ['match-new', 'match-update', 'match-end', 'match-close-observation']) el(id).hidden = !current;
      const container = el('match-list');
      container.replaceChildren();
      if (!(state.matchCatalog.matches || []).length) container.append(node('p', 'guide-empty', '还没有对局记录。'));
      for (const match of state.matchCatalog.matches || []) {
        const card = node('article', 'match-card');
        card.dataset.matchId = match.match_id;
        card.append(node('strong', '', gameLabel(match)), node('p', 'field-help', `${matchStatus(match.status)} · ${date(match.started_at || match.created_at)}`));
        if (match.goal) card.append(node('p', 'field-help', `当时目标：${match.goal}`));
        const label = node('label', '', '想复盘什么？');
        const input = node('input', 'match-review-question');
        input.maxLength = 8000;
        input.value = '请复盘这一局已经讨论过的决策，不要把旧局势当作当前状态。';
        label.append(input);
        card.append(label, button('复盘此局', 'soft-button match-review', () => {
          if (!input.value.trim()) { localError('请填写要复盘的问题。'); return; }
          void task(() => onReview(match.match_id, input.value.trim()), undefined, 'match');
        }));
        container.append(card);
      }
    }
    async function loadBackups() {
      const epoch = ++state.backupEpoch;
      const container = el('guide-backup-list');
      container.replaceChildren(node('p', 'field-help', '正在读取攻略快照…'));
      try {
        const response = await api('/api/guide-backups');
        if (epoch !== state.backupEpoch) return;
        state.backupsLoaded = true;
        container.replaceChildren();
        if (!response.backups?.length) container.append(node('p', 'guide-empty', '还没有攻略库快照。'));
        for (const snapshot of response.backups || []) {
          const card = node('article', 'backup-card');
          const damaged = snapshot.restorable === false || snapshot.status === 'corrupt';
          card.append(node('strong', '', `攻略快照 · ${date(snapshot.created_at)}`), node('small', '', damaged ? '文件损坏，不能恢复；可以删除。' : `${snapshot.documents || 0} 份攻略 · ${snapshot.selections || 0} 个采用关系`));
          const restore = button('恢复此攻略快照', 'soft-button guide-backup-restore', () => {
            const revision = state.guideCatalog.revision;
            confirm('恢复攻略库快照？', `将恢复 ${date(snapshot.created_at)} 的攻略库与采用关系，保留之后的删除标记。个人记忆和当前局势不会从这份快照恢复。`, '恢复攻略快照', () => task(() => control('guide-restore', { confirm: true }, snapshot.backup_id, revision), '攻略库快照已恢复；当前局势仍需新的有效证据。'));
          });
          if (damaged) { restore.disabled = true; restore.dataset.unavailable = 'true'; }
          card.append(restore, button('删除快照', 'text-button guide-backup-delete', () => {
            confirm('删除这份攻略快照？', `仅删除 ${date(snapshot.created_at)} 的快照文件，不删除当前攻略库。`, '删除快照', () => task(async () => { await control('guide-delete-backup', {}, snapshot.backup_id); await loadBackups(); }, '攻略快照已删除。'));
          }));
          container.append(card);
        }
        setBusy();
      } catch (error) {
        if (epoch === state.backupEpoch) container.replaceChildren(node('p', 'settings-message is-error', errorMessage(error)));
      }
    }
    async function reloadLibrary() {
      await task(async () => {
        const guideCatalog = await api('/api/guides');
        render({ ...state, guideCatalog });
      }, '已重新载入本地攻略库。');
    }
    function render(value) {
      const priorSession = state.sessionId;
      const priorRevision = state.guideCatalog.revision;
      for (const key of ['guideCatalog', 'matchCatalog', 'sessionId', 'busy', 'connected']) {
        if (value[key] !== undefined && value[key] !== null) state[key] = value[key];
        else if (key === 'guideCatalog' && key in value) state.guideCatalog = { revision: 0, guides: [], selections: [] };
        else if (key === 'matchCatalog' && key in value) state.matchCatalog = { revision: 0, current: null, matches: [] };
        else if (key === 'sessionId' && key in value) state[key] = null;
      }
      if (priorSession !== state.sessionId || priorRevision !== state.guideCatalog.revision) {
        state.selected = null;
        // A save/adopt chain receives the coordinator's own catalog refresh
        // between its commits. That refresh does not revoke the user's intent.
        if (priorSession !== state.sessionId || !state.localBusy) state.sourceEpoch += 1;
        state.adoptedEpoch += 1;
        state.adoptedMetadata.clear();
        closeConfirmation();
      }
      if (priorRevision !== state.guideCatalog.revision) {
        // Deleting a guide also removes snapshots containing its body. Never
        // keep a restore action for that now-removed file in a closed panel.
        state.backupsLoaded = false;
        state.backupEpoch += 1;
        el('guide-backup-list').replaceChildren();
        if (state.connected && el('guide-backups').open) void loadBackups();
      }
      if (priorSession !== state.sessionId) {
        state.matchDirty = false;
        state.matchFormId = undefined;
        state.candidate = null;
        el('guide-adoption').hidden = true;
      }
      renderGuideContext();
      const librarySignature = JSON.stringify(state.guideCatalog);
      if (state.librarySignature !== librarySignature) { state.librarySignature = librarySignature; renderLibrary(); }
      const matchSignature = JSON.stringify([state.sessionId, state.matchCatalog]);
      if (state.matchSignature !== matchSignature) { state.matchSignature = matchSignature; renderMatch(); }
      setBusy();
    }

    el('open-guides').addEventListener('click', () => showPanel('guides'));
    el('manage-match').addEventListener('click', () => { showPanel('guides'); el('match-form').scrollIntoView({ block: 'start' }); el('match-game').focus(); });
    el('close-guides').addEventListener('click', () => showPanel('chat'));
    el('guide-selection-form').addEventListener('submit', adoptSource);
    el('cancel-guide-adoption').addEventListener('click', () => { state.sourceEpoch += 1; state.candidate = null; state.selected = null; el('guide-adoption').hidden = true; });
    el('guide-save-form').addEventListener('submit', (event) => { event.preventDefault(); void saveGuide(); });
    el('guide-save-adopt').addEventListener('click', () => { void saveGuide(true); });
    el('refresh-guide-library').addEventListener('click', () => { void reloadLibrary(); });
    el('cancel-guide-action').addEventListener('click', closeConfirmation);
    el('confirm-guide-action').addEventListener('click', () => { const operation = state.confirmation; closeConfirmation(); if (operation) void operation(); });
    el('match-form').addEventListener('input', () => { state.matchDirty = true; });
    el('match-form').addEventListener('submit', (event) => { event.preventDefault(); void changeMatch(state.matchCatalog.current ? 'match-update' : 'match-start'); });
    for (const [id, kind] of [['match-new', 'match-new'], ['match-update', 'match-update'], ['match-end', 'match-end'], ['match-close-observation', 'match-close-observation']]) el(id).addEventListener('click', () => { void changeMatch(kind); });
    el('guide-backups').addEventListener('toggle', () => { if (el('guide-backups').open && !state.backupsLoaded) void loadBackups(); });
    el('refresh-guide-backups').addEventListener('click', () => { void loadBackups(); });
    el('create-guide-backup').addEventListener('click', () => { void task(async () => { await control('guide-backup'); await loadBackups(); }, '攻略库快照已创建。'); });
    const expiryTimer = setInterval(() => {
      if (document.visibilityState !== 'hidden' && (!el('guide-panel').hidden || !el('chat-panel').hidden)) renderEvidence();
    }, 1000);
    window.addEventListener('pagehide', () => clearInterval(expiryTimer), { once: true });
    render({ connected: false });
    return Object.freeze({ render, sourceAction, openSource, selectedSource, status });
  }
  window.aiNekoGuidePanel = Object.freeze({ create });
})();
