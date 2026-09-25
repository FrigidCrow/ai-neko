import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const escape = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const names = ['overview-captions.json', 'core-captions.json', 'memory-media-captions.json'];
const diagrams = (await Promise.all(names.map(name => fs.readFile(path.join(root, name), 'utf8').then(JSON.parse)))).flat().sort((a,b) => a.id.localeCompare(b.id));
if (diagrams.length !== 20 || new Set(diagrams.map(d => d.id)).size !== 20) throw new Error('Expected exactly 20 unique diagrams.');
const required = ['title','kind','phase','module','question','summary','input','process','output','failure','lessons','reuse'];
for (const d of diagrams) {
  for (const key of required) if (!d[key]) throw new Error(`${d.id} missing ${key}`);
  await fs.access(path.join(root, 'src', `${d.id}.mmd`));
  await fs.access(path.join(root, 'svg', `${d.id}.svg`));
  d.reference = '../REFERENCES.md';
}
const groups = [
  {title:'先看整体',start:0,end:3},
  {title:'对话怎样运行',start:4,end:9},
  {title:'记忆如何留下',start:10,end:12},
  {title:'听说看与角色',start:13,end:15},
  {title:'独立应用与交付',start:16,end:19},
];
const coverage = [
  ['M0–M6 实施计划','00-roadmap','顺序与验收，不虚构工期'],
  ['系统架构与完整回合','01-architecture,02-turn','本机/云端边界与一次对话'],
  ['app：本机 API、事件协议','03-api','连接校验、身份与事件归属'],
  ['graph：状态、节点、路由','04-graph','唯一对话决策与工具循环'],
  ['runtime：回合与取消','05-runtime,06-cancellation','状态与独立持久收尾'],
  ['adapters：模型协议','07-model','模型事件标准化，不自建第二个 agent'],
  ['tools：工具与插件边界','08-tools','预算、授权、结果与副作用防重放'],
  ['checkpoints：会话持久化','09-checkpoints','内部 ID 与恢复时记忆版本校验'],
  ['memory：写入、检索、整理任务','10-memory,11-memory-data','单一长期权威与概念数据关系'],
  ['memory：纠正与遗忘','12-memory-forget','派生物、旧副本、在途任务和恢复'],
  ['media：ASR、TTS、播放','13-audio,06-cancellation','听说链路与立即停止旧音频'],
  ['视觉与主动事件','14-vision-proactive','门控与同一 Runtime 入口'],
  ['frontend：界面、角色、口型','15-ui-avatar','事件适配与状态呈现'],
  ['config：路径、凭据、存储隔离','16-isolation','ai-neko 独立应用资料'],
  ['desktop：窗口、托盘、进程','17-desktop','条件复用与本工程生命周期'],
  ['tests / build：Windows 交付','18-delivery','干净机、升级恢复与观察证据'],
  ['来源迁入与后续扩展','19-reuse','教程到模块，首版与后续边界'],
];
const nav = groups.map(g => `<div class="nav-group"><p>${g.title}</p>${diagrams.slice(g.start,g.end+1).map(d => `<a href="#${d.id}"><span>${d.id.slice(0,2)}</span>${escape(d.title)}</a>`).join('')}</div>`).join('');
const sections = diagrams.map(d => `<section class="diagram-section" id="${d.id}" aria-labelledby="title-${d.id}">
  <div class="eyebrow">${d.id.slice(0,2)} / ${escape(d.kind)} · ${escape(d.phase)} · 待实现设计</div>
  <h2 id="title-${d.id}">${escape(d.title)}</h2>
  <p class="question">${escape(d.question)}</p><p>${escape(d.summary)}</p>
  <p class="diagram-help">窄屏可左右滑动图形，或 <a href="svg/${d.id}.svg" target="_blank" rel="noopener">打开完整大图</a>。</p>
  <figure><div class="diagram-scroll"><img src="svg/${d.id}.svg" alt="${escape(d.title+'。'+d.summary)}"></div>
    <figcaption><span>${escape(d.module)}</span><a href="svg/${d.id}.svg" target="_blank" rel="noopener">打开大图 ↗</a><a href="src/${d.id}.mmd">Mermaid 源文件</a></figcaption></figure>
  <dl class="io"><div><dt>输入什么</dt><dd>${escape(d.input)}</dd></div><div><dt>怎么处理</dt><dd>${escape(d.process)}</dd></div><div><dt>输出什么</dt><dd>${escape(d.output)}</dd></div></dl>
  <p class="boundary"><strong>容易误解的地方</strong> ${escape(d.failure)}</p>
  <details><summary>复用方式与学习入口 · ${escape(d.lessons.join(' / '))}</summary><p>${escape(d.reuse)}</p><a href="../REFERENCES.md">查看对应教程与源码映射</a></details>
</section>`).join('\n');
const html = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ai-neko · 计划与模块图册</title><link rel="stylesheet" href="atlas.css"></head>
<body><a class="skip" href="#content">跳到图册正文</a>
<aside><a class="brand" href="#top">ai-neko <span>设计图册</span></a><nav aria-label="图册目录">${nav}</nav><p class="aside-note">20 张图 · 本地离线阅读<br>P1 设计基线 · 进度见验收记录</p></aside>
<main id="content"><header id="top"><div class="eyebrow">WINDOWS 优先 / LANGGRAPH / 本地长期记忆</div><h1>把 ai-neko 看明白</h1><p class="lead">先看做事顺序和整体分工，再沿着一句话，走进对话、记忆、语音与桌面模块。</p><p class="status">这些图保留 P1 设计基线。M0 已进入实现，当前进度以实施计划和验收记录为准；完整产品能力仍需逐阶段验证。</p><div class="quick"><a href="#00-roadmap">① 开发路线</a><a href="#01-architecture">② 整体架构</a><a href="#02-turn">③ 一次对话</a><a href="#10-memory">④ 本地记忆</a><a href="#13-audio">⑤ 语音交互</a></div><p class="read-guide">流程图沿箭头读；时序图从上往下读；状态图看触发条件；ER 图看记录间关系。所有图都可打开大图查看。</p><div class="docs"><a href="../PLAN.md">实施计划</a><a href="../ARCHITECTURE.md">接口设计</a><a href="../DIAGRAM-SKILLS.md">技能调研</a><a href="../../REVIEW.md">验证记录</a></div></header>
<details class="mobile-nav"><summary>展开全部 20 张图的目录</summary><nav aria-label="移动端图册目录">${nav}</nav></details>
${sections}
<section class="coverage" id="coverage"><h2>模块覆盖表</h2><p>目录是规划中的职责划分，不代表这些模块已写出代码。概念数据关系也不是最终数据库 DDL。</p><div class="table-scroll"><table><thead><tr><th>计划 / 模块</th><th>对应图</th><th>重点</th></tr></thead><tbody>${coverage.map(([module,ids,note]) => `<tr><td>${escape(module)}</td><td>${ids.split(',').map(id=>`<a href="#${id}">${id.slice(0,2)}</a>`).join(' / ')}</td><td>${escape(note)}</td></tr>`).join('')}</tbody></table></div></section>
<footer>图册可直接离线打开；教程与源码的绝对路径依赖本机参考工程。修改计划后，更新图源并重新生成 SVG。<a href="tools/README.md">维护方法</a></footer></main></body></html>`;
await fs.writeFile(path.join(root, 'index.html'), html+'\n');
await fs.writeFile(path.join(root, 'atlas-manifest.json'), JSON.stringify({project:'ai-neko',status:'planned-not-implemented',diagramCount:20,coverage:coverage.map(([module,ids,purpose])=>({module,diagrams:ids.split(','),purpose})),diagrams},null,2)+'\n');
const md = ['# ai-neko 计划与模块图册','', '**图册保留 P1 设计基线，当前实施进度见 PLAN 和 REVIEW。** M0 已进入实现；图册与文档完成不代表 Windows App 已经可用。','', '[打开离线图册](diagrams/index.html) · [技能调研](DIAGRAM-SKILLS.md) · [实施计划](PLAN.md) · [架构接口](ARCHITECTURE.md)','', '推荐先读 00 → 01 → 02，再按模块阅读。流程图沿箭头读，时序图从上往下读；状态图看触发条件，ER 图只表示概念关系。图册不虚构开发工期。','', '## 模块覆盖','', '| 计划 / 模块 | 对应图 | 理解重点 |','| --- | --- | --- |', ...coverage.map(([m,ids,n])=>`| ${m} | ${ids.split(',').map(id=>`[${id.slice(0,2)}](#diagram-${id})`).join(' / ')} | ${n} |`),''];
for (const d of diagrams) md.push(`<a id="diagram-${d.id}"></a>`,`## ${d.id.slice(0,2)} · ${d.title}`,'',`**${d.kind} · ${d.phase} · 设计未实现**`,'',d.question,'',d.summary,'',`![${d.title}](diagrams/svg/${d.id}.svg)`,'',`[查看 SVG 大图](diagrams/svg/${d.id}.svg) · [编辑 Mermaid 源文件](diagrams/src/${d.id}.mmd)`,'',`- 输入：${d.input}`,`- 处理：${d.process}`,`- 输出：${d.output}`,`- 边界：${d.failure}`,'',`复用与学习：${d.reuse} 对应课程 ${d.lessons.join('、')}，见 [教程与源码映射](REFERENCES.md)。`,'');
await fs.writeFile(path.resolve(root,'../DIAGRAMS.md'),md.join('\n').trimEnd()+'\n');
console.log(`Built offline atlas and Markdown with ${diagrams.length} diagrams; ${coverage.length} coverage rows.`);
