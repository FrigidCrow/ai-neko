import argparse
import hashlib
import subprocess
import json
import re
import struct
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.cwd()
parser = argparse.ArgumentParser()
parser.add_argument('--run', type=int, required=True)
parser.add_argument('--sha', required=True)
parser.add_argument('--linux-passed', type=int, required=True)
parser.add_argument('--windows-passed', type=int, required=True)
args = parser.parse_args()
RUN, SHA = args.run, args.sha
BASE = ROOT / f'artifacts/mvp2/ci-{RUN}'
PKG = BASE / 'ai-neko-windows-x64'
EVIDENCE = BASE / 'package-evidence'

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def digest(data):
    return hashlib.sha256(data).hexdigest()

def filehash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

run = read(BASE / 'run.json')
assert run['headSha'] == SHA and run['conclusion'] == 'success'
required = [j for j in run['jobs'] if not j['name'].startswith('Publish tagged')]
assert len(required) == 3 and all(j['conclusion'] == 'success' for j in required)
assert all(j['conclusion'] == 'skipped' for j in run['jobs'] if j['name'].startswith('Publish tagged'))
artifacts = read(BASE / 'artifacts.json')['artifacts']
artifact = next(a for a in artifacts if a['name'] == 'ai-neko-windows-x64')
assert not artifact['expired'] and artifact['workflow_run']['head_sha'] == SHA
archives = list(PKG.glob('*.zip'))
assert len(archives) == 1
archive = archives[0]
archive_hash = filehash(archive)
checksums = dict((line.split()[1].lstrip('*'), line.split()[0]) for line in (PKG / 'SHA256SUMS.txt').read_text().splitlines() if line.strip())
assert checksums[archive.name] == archive_hash
build = read(PKG / 'build-info.json')
assert build['source']['commit'] == SHA and build['source']['dirty'] is False
assert build['version'] == f'0.4.0-dev.{SHA[:12]}'
assert str(build['build']['github_run_id']) == str(RUN)
windows_crlf_inputs = []
def matches_checkout(relative, expected):
    try:
        raw = subprocess.check_output(['git', 'show', f'{SHA}:{relative}'], cwd=ROOT, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        assert relative == 'desktop/vendor/live2dcubismcore.min.js', relative
        raw = (ROOT / relative).read_bytes()
        pinned = json.loads(subprocess.check_output(['git', 'show', f'{SHA}:desktop/vendor/sources.json'], cwd=ROOT))['core']
        assert len(raw) == pinned['bytes'] and digest(raw) == pinned['sha256']
    if digest(raw) == expected:
        return
    assert b'\x00' not in raw, relative
    raw.decode('utf-8')
    # GitHub Windows checkout uses autocrlf for ordinary text; compare exact
    # reconstructed checkout bytes, without accepting other content changes.
    assert digest(raw.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')) == expected, relative
    windows_crlf_inputs.append(relative)
for relative, value in build['source']['input_sha256'].items():
    matches_checkout(relative, value)
assert build['lockfile']['sha256'] == build['source']['input_sha256']['uv.lock']
assert build['desktop']['package_lock_sha256'] == build['source']['input_sha256']['desktop/package-lock.json']
with zipfile.ZipFile(archive) as bundle:
    assert bundle.testzip() is None
    roots = {name.split('/')[0] for name in bundle.namelist()}
    assert len(roots) == 1
    prefix = roots.pop() + '/'
    assert json.loads(bundle.read(prefix + 'build-info.json')) == build
    assert json.loads(bundle.read(prefix + 'resources/app/package.json'))['version'] == '0.4.0'
    executables = {}
    for name, entry, hash_key in [('desktop','ai-neko.exe','executable_sha256'),('backend','resources/backend/ai-neko.exe','backend_sha256')]:
        data = bundle.read(prefix + entry)
        assert digest(data) == build['desktop'][hash_key]
        pe = struct.unpack_from('<I', data, 0x3c)[0]
        assert data[:2] == b'MZ' and data[pe:pe+4] == b'PE\0\0'
        assert struct.unpack_from('<H', data, pe+4)[0] == 0x8664
        executables[name] = {'sha256':digest(data), 'machine':'AMD64'}
    for name, expected in build['memory_reuse']['notices'].items():
        paths = [p for p in bundle.namelist() if p.endswith('/'+name)]
        assert paths and all(digest(bundle.read(p)) == expected for p in paths)
    # Verify each copied Python/runtime notice, not only the presence of a folder.
    license_root = prefix + 'third-party-licenses/'
    licenses = json.loads(bundle.read(license_root + 'manifest.json'))
    expected_dependencies = {**build['dependencies'], 'pyinstaller': build['build']['pyinstaller']}
    assert {d['name']: d['version'] for d in licenses['dependencies']} == expected_dependencies
    license_files = {}
    for dependency in licenses['dependencies']:
        assert dependency['files'], dependency['name']
        for record in dependency['files']:
            value = digest(bundle.read(license_root + record['path']))
            assert value == record['sha256'], record['path']
            license_files[record['path']] = value
    python_notice = licenses['python']
    assert python_notice['version'] == build['build']['python']
    assert digest(bundle.read(license_root + python_notice['file'])) == python_notice['sha256']
    runtime_notices = licenses['windows_runtime']
    for record in runtime_notices['files']:
        value = digest(bundle.read(license_root + 'windows-runtime/' + record['file']))
        assert value == record['sha256'], record['file']
        license_files['windows-runtime/' + record['file']] = value
    assert runtime_notices == json.loads(bundle.read(license_root + 'windows-runtime/provenance.json'))
    for notice in ('LICENSE', 'LICENSES.chromium.html'):
        assert len(bundle.read(prefix + notice)) > 100
    assets = json.loads(bundle.read(prefix + 'mvp1-assets-manifest.json'))
    assert digest(bundle.read(prefix + 'mvp1-assets-manifest.json')) == build['desktop']['asset_manifest_sha256']
    for record in assets['files']:
        assert record['path'].startswith('desktop/')
        entry = prefix + 'resources/app/' + record['path'].removeprefix('desktop/')
        value = bundle.read(entry)
        assert len(value) == record['bytes'] and digest(value) == record['sha256'], entry
    assert digest(bundle.read(prefix + 'resources/app/vendor/live2dcubismcore.min.js')) == build['desktop']['core_sha256']
source_tests = []
guide_reports = {}
for platform, passed, skipped in [('Windows',args.windows_passed,0),('Linux',args.linux_passed,1)]:
    path = BASE / ('evidence-'+platform) / 'm0' / (platform+'-smoke.json')
    d = read(path)
    assert d['ci_gate'] == 'PASS' and d['source']['git_commit'] == SHA
    assert d['source']['git_dirty'] is False and d['source_unchanged_during_run']
    assert {k:d['tests'][k] for k in ('passed','skipped','failed','errors')} == dict(passed=passed,skipped=skipped,failed=0,errors=0)
    assert d['tests']['real_model_tests'] == 0
    source_tests.append({'name':path.name,'sha256':filehash(path),'passed':passed,'skipped':skipped,'ci_gate':d['ci_gate'],'status':d['status'],'environment':d['environment']})
    for suffix in ('evaluation', 'benchmark'):
        guide_path = path.parents[1] / 'mvp2' / f'{platform}-guide-{suffix}.json'
        guide = read(guide_path)
        assert guide['summary']['status'] == 'PASS'
        assert guide['summary']['source_files_unchanged'] is True
        for relative, expected in guide['source_hashes'].items():
            matches_checkout(relative, expected)
        if suffix == 'evaluation':
            assert len(guide['questions']) == 30
            for key, expected in {'answerable_questions':24,'top3_evidence_hits':24,'absent_questions':6,'absent_rejections':6,'answerable_zero_network':24,'answerable_skipped_planning':24,'wrong_game_hits':0,'known_conflicting_version_hits':0,'prohibited_hits':0,'source_identity_errors':0,'ingestion_errors':0,'withheld_or_previous_match_leaks':0}.items():
                assert guide['summary'][key] == expected, (platform, key)
            assert guide['summary']['plain_chat_checks_passed'] is True
        else:
            assert guide['dataset']['documents'] == 200
            assert guide['summary']['sample_count'] == 100
            assert guide['summary']['network_attempts'] == 0
            assert guide['summary']['p95_ms'] <= guide['summary']['p95_target_ms'] == 150
            assert not guide['summary']['retrieval_verification_failures']
        guide_reports[guide_path.name] = {'sha256':filehash(guide_path),'summary':guide['summary'],'environment':guide['environment']}
reports = {}
for name, count in [('package-smoke.json',16),('desktop-smoke.json',9),('companion-smoke.json',21),('g6-acceptance.json',8)]:
    path = PKG/name
    assert path.read_bytes() == (EVIDENCE/name).read_bytes()
    d = read(path)
    assert d['status'] == 'PASS' and d['source_commit'] == SHA and d['archive_sha256'] == archive_hash
    cases = d.get('cases',d.get('tests',{}).get('cases'))
    assert len(cases) == count and all(c.get('status',c.get('outcome')) == 'PASS' for c in cases)
    reports[name] = {'sha256':filehash(path),'passed':count}
package = read(PKG/'package-smoke.json')
desktop = read(PKG/'desktop-smoke.json')
companion = read(PKG/'companion-smoke.json')
assert package['executable_sha256'] == executables['backend']['sha256']
assert package['desktop_executable_sha256'] == executables['desktop']['sha256']
for d, harness in [(desktop,'scripts/desktop_smoke.cjs'),(companion,'desktop/tests/companion.smoke.cjs')]:
    assert d['build'] == build and d['source_dirty'] is False
    assert d['executable_sha256'] == executables['desktop']['sha256']
    matches_checkout(harness, d['harness_sha256'])
assert not companion['renderer_errors']
persona_save_wait = companion['persona_save_wait']
for field in ('explicit_promise_gate', 'actual_form_and_ipc',
              'stale_success_predicate_passed_while_put_blocked',
              'current_save_predicate_rejected_while_put_blocked',
              'persisted_new_name_verified'):
    assert persona_save_wait[field] is True, field
assert persona_save_wait['previous_version'] == 2
assert persona_save_wait['saved_version'] == 3
assert companion['real_model_calls'] == companion['real_audio_service_calls'] == companion['actual_user_microphone_captures'] == companion['actual_desktop_captures'] == 0
benchmark = companion['audio_stop_benchmark']
assert benchmark['samples'] == 20 and benchmark['actual_web_audio'] is True
assert benchmark['hardware_acoustic_measurement'] is False
assert benchmark['p95_ms'] <= benchmark['target_ms']
assert all(companion['plain_request_revocation'].values())
screenshots = {}
for item in desktop['screenshots']:
    path = EVIDENCE / item['file']
    assert filehash(path) == item['sha256']
    screenshots[path.name] = filehash(path)
for field in ('screenshot','snapshot_screenshot','search_settings_screenshot'):
    path = EVIDENCE / Path(companion[field].replace('\\','/')).name
    assert filehash(path) == companion[field+'_sha256']
    screenshots[path.name] = filehash(path)
desktop_unit_logs = {}
for platform in ('Windows', 'Linux'):
    path = BASE / (platform.lower() + '-job.log')
    log = path.read_text(encoding='utf-8')
    assert re.search(r'(?:#|ℹ) tests 92\b', log) and re.search(r'(?:#|ℹ) pass 92\b', log), platform
    assert re.search(r'(?:#|ℹ) fail 0\b', log), platform
    desktop_unit_logs[platform] = {'sha256':filehash(path),'passed':92,'failed':0}
g6 = read(PKG / 'g6-acceptance.json')
assert g6['build'] == build and g6['source_dirty'] is False
assert g6['executable_sha256'] == executables['desktop']['sha256']
assert g6['execution'] == 'windows_extracted_executable' and g6['packaged_windows'] == 'PASS'
assert g6['source_files_unchanged'] and not g6['renderer_and_provider_errors']
assert g6['real_provider_calls'] == g6['actual_user_microphone_captures'] == g6['actual_desktop_captures'] == 0
assert g6['user_mutation_api_shortcuts'] == 0
assert len(g6['launches']) == 2 and len({item['pid'] for item in g6['launches']}) == 2
assert all(item['packaged'] for item in g6['launches'])
assert len(g6['questions_observed']) >= 20
assert all(item['status'] == 'completed' and item['search_requests'] == item['page_requests'] == 0 for item in g6['questions_observed'])
for relative, expected in g6['source_hashes'].items():
    matches_checkout(relative, expected)
for item in g6['screenshots']:
    path = EVIDENCE / item['file']
    assert filehash(path) == item['sha256']
    screenshots[path.name] = filehash(path)
result = {
    'status':'PASS','verified_utc':datetime.now(timezone.utc).isoformat(),
    'source_commit':SHA,'source_clean':True,'ci_run':RUN,'ci_url':run['url'],
    'workflow_conclusion':run['conclusion'],
    'jobs':[{'name':j['name'],'conclusion':j['conclusion']} for j in run['jobs']],
    'download_kind':'GitHub Actions artifact; tagged release publication was skipped in this run',
    'verifier':{'path':str(Path(__file__).relative_to(ROOT)),'sha256':filehash(Path(__file__))},
    'artifact':{'id':artifact['id'],'url':f'https://github.com/FrigidCrow/ai-neko/actions/runs/{RUN}/artifacts/{artifact["id"]}','expires_at':artifact['expires_at'],'requires_github_login':True},
    'version':build['version'],'archive':archive.name,'archive_bytes':archive.stat().st_size,
    'archive_sha256':archive_hash,'zip_crc':'PASS','inner_outer_build_info_match':True,
    'source_inputs_verified_with_windows_checkout_line_endings':True,
    'source_input_count':len(build['source']['input_sha256']),
    'windows_crlf_inputs':sorted(set(windows_crlf_inputs)),
    'lockfiles':{'uv.lock':build['lockfile']['sha256'],'desktop/package-lock.json':build['desktop']['package_lock_sha256']},
    'build_environment':build['build'],
    'source_tests':source_tests,'desktop_unit_passed':92,'packaged_reports':reports,'guide_reports':guide_reports,
    'desktop_unit_logs':desktop_unit_logs,
    'license_files_verified':license_files,'desktop_assets_verified':len(assets['files']),
    'g6_packaged':{'status':g6['status'],'case_count':len(g6['cases']),'processes':len(g6['launches']),'questions_observed':len(g6['questions_observed']),'synthetic_calls':g6['synthetic_calls']},
    'executables':executables,'memory_reuse':build['memory_reuse'],'screenshots':screenshots,
    'plain_request_revocation':companion['plain_request_revocation'],
    'persona_save_wait':persona_save_wait,
    'audio_stop_benchmark':benchmark,'synthetic_calls':companion['synthetic_calls'],
    'real_cloud_service_calls':0,'actual_user_microphone_captures':0,'actual_desktop_captures':0,
    'windows_11':'NOT_VERIFIED','local_windows_executable_run':False,
    'mvp2_g1_g6':'LOCAL_AND_WINDOWS_CI_VERIFIED; REAL_SERVICES_AND_WINDOWS_11_NOT_VERIFIED',
}
output = BASE / 'windows-download-verification.json'
output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:result[k] for k in ('status','source_commit','version','archive_sha256','artifact','packaged_reports','audio_stop_benchmark')},ensure_ascii=False,indent=2))
