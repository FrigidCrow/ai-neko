import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {launchBrowser} from './browser.mjs';
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const browser = await launchBrowser();
const failures = [];
const evidence = path.join(root,'evidence');
await fs.mkdir(evidence, {recursive:true});
const manifest = JSON.parse(await fs.readFile(path.join(root,'atlas-manifest.json'),'utf8'));
const results = [];
const requests = [];
try {
  const page = await browser.newPage();
  page.on('pageerror', error => failures.push(`Browser: ${error.message}`));
  page.on('request', request => { if (/^https?:/.test(request.url())) requests.push(request.url()); });
  await page.setOfflineMode(true);
  for (const width of [1440,390]) {
    await page.setViewport({width,height:1000,deviceScaleFactor:1});
    await page.goto(pathToFileURL(path.join(root,'index.html')).href, {waitUntil:'load'});
    await page.evaluate(() => Promise.all([...document.images].map(image => image.decode())));
    const state = await page.evaluate(() => ({
      images:[...document.images].map(i=>({src:i.getAttribute('src'),loaded:i.complete&&i.naturalWidth>0})),
      sections:document.querySelectorAll('.diagram-section').length,
      bodyWidth:document.documentElement.scrollWidth,
      viewportWidth:innerWidth,
      brokenAnchors:[...document.querySelectorAll('a[href^="#"]')].map(a=>a.hash.slice(1)).filter(id=>!document.getElementById(id)),
      hrefs:[...document.querySelectorAll('a[href]')].map(a=>a.href),
    }));
    if (state.sections!==20 || state.images.length!==20 || state.images.some(i=>!i.loaded)) failures.push(`Missing diagram at ${width}`);
    if (state.bodyWidth > width+1) failures.push(`Body overflow at ${width}: ${state.bodyWidth}`);
    if (state.brokenAnchors.length) failures.push(`Broken anchors: ${state.brokenAnchors}`);
    for (const href of new Set(state.hrefs)) {
      const url = new URL(href);
      if (url.protocol==='file:') {
        try { await fs.access(fileURLToPath(url)); } catch { failures.push(`Missing link: ${href}`); }
      }
    }
    await page.screenshot({path:path.join(evidence,`atlas-${width}.png`)});
    await page.evaluate(() => document.getElementById('13-audio').scrollIntoView({behavior:'instant'}));
    await page.screenshot({path:path.join(evidence,`audio-${width}.png`)});
    results.push({width,sections:state.sections,images:state.images.length,allImagesLoaded:state.images.every(i=>i.loaded),bodyOverflow:state.bodyWidth>width+1,brokenAnchors:state.brokenAnchors.length});
  }
  await page.setViewport({width:1440,height:1100,deviceScaleFactor:1});
  const svgGeometry = [];
  for (const d of manifest.diagrams) {
    await page.goto(pathToFileURL(path.join(root,'svg',d.id+'.svg')).href, {waitUntil:'load'});
    const g = await page.evaluate(() => {
      const svg = document.querySelector('svg');
      const v = svg.viewBox.baseVal;
      const rect = svg.getBoundingClientRect();
      const clipped = [...svg.querySelectorAll('text')].filter(t => {
        const r = t.getBoundingClientRect();
        return r.width && (r.left < rect.left-3 || r.top < rect.top-3 || r.right > rect.right+3 || r.bottom > rect.bottom+3);
      }).map(t=>t.textContent);
      return {width:v.width,height:v.height,textLabels:svg.querySelectorAll('text').length,clippedText:clipped};
    });
    if (g.width<=0 || g.height<=0 || g.textLabels===0) failures.push(`Invalid SVG geometry ${d.id}`);
    if (g.clippedText.length) failures.push(`Text outside SVG ${d.id}: ${g.clippedText.join(',')}`);
    svgGeometry.push({id:d.id,...g});
    if (['01-architecture','05-runtime','06-cancellation','11-memory-data','12-memory-forget'].includes(d.id)) {
      await page.$eval('svg', svg => {svg.style.maxWidth='1400px';svg.style.width='100%';svg.style.height='auto';});
      await (await page.$('svg')).screenshot({path:path.join(evidence,`${d.id}.png`)});
    }
  }
  if (requests.length) failures.push(`Unexpected network requests: ${requests.join(',')}`);
  const report = {checkedAt:new Date().toISOString(),platform:process.platform,arch:process.arch,node:process.version,scope:'Offline documentation browser validation, not Windows product validation',networkRequests:requests.length,viewports:results,svgGeometry,failures,status:failures.length?'FAIL':'PASS'};
  await fs.writeFile(path.join(root,'browser-validation.json'),JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({status:report.status,viewports:results,svgCount:svgGeometry.length,failures},null,2));
} finally {await browser.close();}
if (failures.length) process.exitCode=1;
