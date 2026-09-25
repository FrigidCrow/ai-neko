import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {renderMermaid} from '@mermaid-js/mermaid-cli';
import {launchBrowser} from './browser.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const config = JSON.parse(await fs.readFile(path.join(here, 'mermaid-config.json'), 'utf8'));
const files = (await fs.readdir(path.join(root, 'src'))).filter(name => name.endsWith('.mmd')).sort();
const browser = await launchBrowser();
const results = [];
try {
  for (const file of files) {
    const source = await fs.readFile(path.join(root, 'src', file), 'utf8');
    const result = await renderMermaid(browser, source, 'svg', {
      mermaidConfig: config, backgroundColor: '#ffffff', fontEmbed: false,
      viewport: {width: 1600, height: 1200}, svgId: `diagram-${file.replace('.mmd', '')}`,
    });
    // Give img elements a stable intrinsic size instead of a 100% SVG width.
    let xml = new TextDecoder().decode(result.data);
    xml = xml.replace(/<svg\b[^>]*>/, tag => {
      const viewBox = tag.match(/viewBox="([^"]+)"/)[1].split(/\s+/).map(Number);
      return tag.replace(/\swidth="[^"]*"/, '').replace(/\sheight="[^"]*"/, '').replace('<svg ', `<svg width="${viewBox[2]}" height="${viewBox[3]}" `);
    });
    result.data = new TextEncoder().encode(xml);
    const destination = path.join(root, 'svg', file.replace('.mmd', '.svg'));
    await fs.writeFile(destination, result.data);
    results.push({id: file.replace('.mmd', ''), sourceSha256: createHash('sha256').update(source).digest('hex'), svgSha256: createHash('sha256').update(result.data).digest('hex'), bytes: result.data.length, status: 'PASS'});
    console.log(`Rendered ${file}`);
  }
} finally { await browser.close(); }
await fs.writeFile(path.join(root, 'render-validation.json'), JSON.stringify({checkedAt: new Date().toISOString(), platform: process.platform, arch: process.arch, node: process.version, renderer: '@mermaid-js/mermaid-cli@12.0.0', scope: 'Documentation diagrams only; Windows app unverified', diagrams: results}, null, 2) + '\n');
