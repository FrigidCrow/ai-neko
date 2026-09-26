"""Validate documentation artifacts and reference isolation; not a product test."""
import datetime
import hashlib
import json
import os
import platform
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[3]
SOURCE = Path('/Users/frigidcrow/Dev/neko-companion')
issues = []


def sha(value):
    return hashlib.sha256(value).hexdigest()


def git(*args, cwd=ROOT):
    return subprocess.check_output(['git', *args], cwd=cwd)


files = []
excluded = {'.git', 'node_modules', '.venv', '__pycache__', '.pytest_cache', '.ruff_cache', 'artifacts', 'dist', 'build'}
for directory, subdirs, names in os.walk(ROOT):
    subdirs[:] = [name for name in subdirs if name not in excluded]
    files.extend(Path(directory)/name for name in names)
markdown = [p for p in files if p.suffix == '.md']
links = 0
for p in markdown:
    content = p.read_text()
    fences = [line for line in content.splitlines() if line.startswith('```')]
    if len(fences) % 2:
        issues.append('Unclosed fence: '+str(p.relative_to(ROOT)))
    for line in content.splitlines():
        if line.rstrip() != line:
            issues.append('Trailing whitespace: '+str(p.relative_to(ROOT)))
    for raw in re.findall(r'\]\(([^\n]*?)\)', content):
        target = raw.strip().strip('<>')
        if urlparse(target).scheme in ('http', 'https', 'mailto'):
            continue
        path_part, _, fragment = target.partition('#')
        if path_part:
            line_match = re.search(r':(\d+)$', path_part)
            if line_match:
                path_part = path_part[:line_match.start()]
            resolved = Path(unquote(path_part))
            if not resolved.is_absolute():
                resolved = p.parent/resolved
        else:
            resolved = p
        links += 1
        if not resolved.exists():
            issues.append(f'Missing link {p.relative_to(ROOT)}: {target}')
        elif fragment and resolved.suffix == '.md':
            data = resolved.read_text()
            anchors = set(re.findall(r'id="([^"]+)"', data))
            counts = {}
            for heading in re.findall(r'^#{1,6}\s+(.+?)\s*$', data, re.M):
                base = re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
                number = counts.get(base, 0)
                counts[base] = number+1
                anchors.add(base+('-'+str(number) if number else ''))
            if unquote(fragment) not in anchors:
                issues.append(f'Missing anchor {p.relative_to(ROOT)}: {target}')
manifest = json.loads((ROOT/'docs/reference-manifest.json').read_text())
for item in manifest['files']:
    p = Path(item['absolutePath'])
    if not p.exists() or sha(p.read_bytes()) != item['sha256']:
        issues.append('Source hash changed: '+str(p))
initial = json.loads((ROOT/'docs/validation-initial.json').read_text())
source_fingerprint = {'status_sha256': sha(git('status', '--porcelain=v1', '-z', cwd=SOURCE)), 'tracked_diff_sha256': sha(git('diff', cwd=SOURCE))}
if source_fingerprint != initial['sourceFingerprint']:
    issues.append('Source repository status/diff changed from initial baseline')
old = ROOT.parent/'langgraph-companion'
if old.exists():
    issues.append('Old project directory still exists')
if git('rev-parse', '--show-toplevel').decode().strip() != str(ROOT):
    issues.append('Git root mismatch')
allowed_remote_urls = {'git@github.com:FrigidCrow/ai-neko.git', 'https://github.com/FrigidCrow/ai-neko.git'}
remotes = []
for name in git('remote').decode().splitlines():
    fetch_urls = git('remote', 'get-url', '--all', name).decode().splitlines()
    push_urls = git('remote', 'get-url', '--push', '--all', name).decode().splitlines()
    remotes.append({'name': name, 'fetchUrls': fetch_urls, 'pushUrls': push_urls})
    if name != 'origin' or any(url not in allowed_remote_urls for url in fetch_urls + push_urls):
        issues.append('Remote differs from user-authorized ai-neko repository: '+name)
allowed_old = {'WORKLOG.md', 'docs/validation-initial.json'}
old_pattern = re.compile(r'langgraph-companion|LangGraph Companion|LangGraphCompanion|LGC_DATA_DIR|langgraph_companion')
for p in files:
    rel = p.relative_to(ROOT).as_posix()
    if rel in allowed_old or p == Path(__file__).resolve() or p.suffix not in ('.md', '.json', '.html', '.css', '.mjs', '.mmd'):
        continue
    if old_pattern.search(p.read_text()):
        issues.append('Stale current name: '+rel)
product_stages = {}
for name in ['docs/PLAN.md', 'REVIEW.md']:
    text = (ROOT/name).read_text()
    product_stages[name] = {}
    for i in range(7):
        match = re.search(r'^\| M'+str(i)+r'[^\n]+$', text, re.M)
        stage = match.group(0).split('|') if match else []
        status_column = 3 if name == 'docs/PLAN.md' else 2
        status = stage[status_column].strip() if len(stage) > status_column else 'MISSING'
        if status not in {'Pending', 'In progress', 'Partial', 'PASS', 'Failed', 'Blocked'}:
            issues.append(f'Invalid product M{i} status in {name}: {status}')
        product_stages[name]['M'+str(i)] = status
if product_stages['docs/PLAN.md'] != product_stages['REVIEW.md']:
    issues.append('Product stage status differs between PLAN and REVIEW')
for p in files:
    if p.suffix in ('.md', '.mmd', '.mjs', '.css', '.html', '.py'):
        run = subprocess.run(['git', 'diff', '--no-index', '--check', '--', '/dev/null', str(p)], capture_output=True, text=True)
        if run.returncode not in (0, 1) or run.stdout.strip() or run.stderr.strip():
            issues.append('Whitespace diff: '+str(p.relative_to(ROOT))+' '+run.stdout+run.stderr)
render = json.loads((ROOT/'docs/diagrams/render-validation.json').read_text())
browser = json.loads((ROOT/'docs/diagrams/browser-validation.json').read_text())
for item in render['diagrams']:
    for kind, key in [('src', 'sourceSha256'), ('svg', 'svgSha256')]:
        ext = 'mmd' if kind == 'src' else 'svg'
        p = ROOT/'docs/diagrams'/kind/(item['id']+'.'+ext)
        if sha(p.read_bytes()) != item[key]:
            issues.append('Render hash mismatch: '+str(p.relative_to(ROOT)))
if len(render['diagrams']) != 20 or browser['status'] != 'PASS':
    issues.append('Diagram validation incomplete')
report = {'checkedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'scope': 'Rename, documentation, diagrams, reference integrity; no ai-neko product runtime or Windows validation', 'environment': {'system': platform.system(), 'machine': platform.machine(), 'python': platform.python_version()}, 'gitRoot': str(ROOT), 'branch': git('branch', '--show-current').decode().strip(), 'remotes': remotes, 'oldDirectoryAbsent': not old.exists(), 'oldNamesAllowedOnlyInHistory': list(sorted(allowed_old)), 'markdownFiles': len(markdown), 'localLinks': links, 'referenceFiles': len(manifest['files']), 'courses': len(manifest['courses']), 'referenceGroups': len(manifest['groups']), 'sourceHead': git('rev-parse', 'HEAD', cwd=SOURCE).decode().strip(), 'sourceFingerprintCommands': ['git status --porcelain=v1 -z', 'git diff'], 'sourceFingerprint': source_fingerprint, 'sourceTrackedDiffAndStatusUnchanged': source_fingerprint == initial['sourceFingerprint'], 'diagramCount': len(render['diagrams']), 'browserValidation': browser['status'], 'productStages': product_stages, 'issues': issues, 'result': 'PASS' if not issues else 'FAIL'}
(ROOT/'docs/validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
print(json.dumps(report, ensure_ascii=False, indent=2))
raise SystemExit(bool(issues))
