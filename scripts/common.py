"""Shared pin validation, isolated environment, logs and explicit test gates."""
import hashlib
import io
import json
from collections import Counter
import os
from pathlib import Path
import re
import subprocess
import tarfile
import urllib.request
import time

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.work'
OUT = ROOT / 'out'


def validate_pin(pin):
    if pin.get('repository') != 'NousResearch/hermes-agent':
        raise ValueError('Only the reviewed upstream repository is accepted')
    sha = pin.get('commit', '')
    if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha) or sha == '0'*40:
        raise ValueError('Commit must be the exact full lowercase Git SHA (no normalization)')
    for key in ('version', 'node'):
        if not isinstance(pin.get(key), str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', pin[key]):
            raise ValueError(f'Invalid {key}')
    if type(pin.get('revision')) is not int or pin['revision'] < 1:
        raise ValueError('revision must be a positive integer')
    return pin


def load_pin():
    return validate_pin(json.loads((ROOT/'upstream.json').read_text()))


def release_version(pin):
    return f"{pin['version']}.{pin['revision']}"


# Since the semver tags (v0.22+) upstream commits apps/desktop/package.json at this
# placeholder and injects the release version at build time (scripts/bundles/desktop.py).
UNSTAMPED_VERSION = '0.0.0'


def source_desktop_version(package, pin):
    """Desktop version of the pinned source: committed, or the pinned release tag if unstamped."""
    version = package.get('version')
    if version != UNSTAMPED_VERSION:
        return version
    tag = pin.get('releaseTag')
    if not isinstance(tag, str) or not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', tag) or tag[1:] == UNSTAMPED_VERSION:
        raise ValueError('Unstamped upstream package requires an exact nonzero release tag pin')
    return tag[1:]


def archive_name(version, platform, arch):
    suffix = '.tar.gz' if platform == 'linux' else '.zip'
    signing = 'adhoc' if platform == 'darwin' else 'unsigned'
    return f'Hermes-{version}-{platform}-{arch}-{signing}{suffix}'


def clean_environment(work, pin, inherited=None):
    source = os.environ if inherited is None else inherited
    # Toolchain/OS plumbing only. No GitHub tokens, cloud keys, Python env or agent settings.
    allowed = {'PATH','SystemRoot','SYSTEMROOT','SystemDrive','COMSPEC','ComSpec','PATHEXT',
               'ProgramFiles','ProgramFiles(x86)','ProgramW6432','WINDIR','windir',
               'PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS','DISPLAY','XAUTHORITY'}
    env = {k: v for k, v in source.items() if k in allowed}
    home = work/'h'
    for p in (home, work/'tmp', work/'cache', home/'AppData/Roaming', home/'AppData/Local'):
        p.mkdir(parents=True, exist_ok=True)
    env.update({
        'HOME':str(home), 'USERPROFILE':str(home), 'HERMES_HOME':str(home/'.hermes'),
        'APPDATA':str(home/'AppData/Roaming'), 'LOCALAPPDATA':str(home/'AppData/Local'),
        'XDG_CONFIG_HOME':str(home/'.config'), 'XDG_CACHE_HOME':str(work/'cache'),
        'XDG_DATA_HOME':str(home/'.local/share'),
        'TMPDIR':str(work/'tmp'), 'TMP':str(work/'tmp'), 'TEMP':str(work/'tmp'),
        'npm_config_cache':str(work/'cache/npm'),
        'npm_config_userconfig':str(home/'.npmrc'), 'npm_config_globalconfig':str(home/'global.npmrc'),
        'ELECTRON_CACHE':str(work/'cache/electron'), 'ELECTRON_BUILDER_CACHE':str(work/'cache/builder'),
        'PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD':'1', 'CSC_IDENTITY_AUTO_DISCOVERY':'false',
        'GIT_CONFIG_NOSYSTEM':'1', 'GIT_CONFIG_GLOBAL':str(home/'.gitconfig'),
        'GIT_TERMINAL_PROMPT':'0', 'GIT_CEILING_DIRECTORIES':str(work),
        'GITHUB_SHA':pin['commit'], 'GITHUB_REF_NAME':'main',
        'NODE_OPTIONS':'--max-old-space-size=4096', 'TZ':'UTC', 'LANG':'C.UTF-8', 'CI':'1',
        'PYTHONNOUSERSITE':'1', 'PYTHONDONTWRITEBYTECODE':'1', 'PYTHONUTF8':'1',
    })
    return env


# Inputs that only electron-builder/build stamping consume. Upstream tests read
# them too (channel identity, payload version) and must see an unstamped host.
PACKAGING_ONLY_ENV = ('HERMES_PAYLOAD_VERSION', 'HERMES_DESKTOP_DISTRIBUTION',
                      'HERMES_DESKTOP_DISTRIBUTION_VERSION', 'DESKTOP_BUILD_PYTHON',
                      'DESKTOP_BUILD_LOGS', 'DESKTOP_SIGNING_TEST_SOURCE')
# Linux sun_path is 108 bytes including NUL. Upstream fixtures nest sockets
# three directories below os.tmpdir() (vitest.run-tmp.ts adds one more), and
# ssh-connection keeps ControlPath under HOME only within OpenSSH's 104 bytes.
SOCKET_BUDGETS = (
    ('t/vitest-XXXXXX/desktop-gw-XXXXXX/hermes-gw-0123456789abcdef/control.sock', 107),
    ('h/.hermesmagic-test/desktop-ssh/0123456789abcdef.sock.0123456789abcdef', 104),
)


def private_short_root(parent='/tmp', prefix='hb-'):
    """A fresh owner-only directory whose path keeps upstream socket fixtures under sun_path."""
    import stat
    import tempfile
    root = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    st = os.lstat(root)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise RuntimeError(f'Unsafe private test root: {root}')
    for probe, limit in SOCKET_BUDGETS:
        if len(os.fsencode(str(root/probe))) > limit:
            os.rmdir(root)
            raise RuntimeError(f'Private test root too long for AF_UNIX sockets: {root}')
    return root


def full_suite_environment(env, root):
    """Test env for the upstream suite: no packaging inputs, short private HOME and temp."""
    root = Path(root)
    test = {k: v for k, v in env.items() if k not in PACKAGING_ONLY_ENV}
    home, tmp = root/'h', root/'t'
    for p in (home, tmp, home/'.config', home/'.local/share'):
        p.mkdir(parents=True, exist_ok=True)
    test.update({
        'HOME': str(home), 'USERPROFILE': str(home), 'HERMES_HOME': str(home/'.hermes'),
        'XDG_CONFIG_HOME': str(home/'.config'), 'XDG_DATA_HOME': str(home/'.local/share'),
        'TMPDIR': str(tmp), 'TMP': str(tmp), 'TEMP': str(tmp),
    })
    return test


def apply_github_file_commands(env, env_file, path_file):
    """Apply GITHUB_ENV/GITHUB_PATH as the runner would to the next step: last PATH line first."""
    env = dict(env)
    for line in Path(env_file).read_text(encoding='utf-8').splitlines():
        if not line:
            continue
        key, sep, value = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or '<<' in key:
            raise ValueError(f'Unsupported GITHUB_ENV command: {line!r}')
        env[key] = value
    dirs = [line for line in Path(path_file).read_text(encoding='utf-8').splitlines() if line]
    if dirs:
        env['PATH'] = os.pathsep.join(list(reversed(dirs)) + [env['PATH']])
    return env


def hermes_test_runtime(src, env, root, logs, bootstrap, runner=None):
    """Upstream's supported test interpreter: setup-pm's toolchain + --test-environment phases.

    Mirrors .github/actions/setup-pm (js-tests.yml: extras '[]', test-environment true)
    for the Python toolchain only; Node/pnpm stay the pin-verified ones on PATH.
    """
    runner = runner or run
    root = Path(root)
    pm_home, files = root/'pm', root/'gh'
    files.mkdir(parents=True, exist_ok=True)
    commands = {name: files/name for name in ('GITHUB_ENV', 'GITHUB_OUTPUT', 'GITHUB_PATH')}
    for f in commands.values():
        f.write_text('', encoding='utf-8')
    setup = str(Path(src)/'scripts/ci/setup_toolchain.py')
    # The interpreter must come from the exports below, never the build host's.
    env = {k: v for k, v in env.items() if k not in ('HERMES_PYTHON', 'VIRTUAL_ENV', 'PYTHONPATH')}
    current = dict(env, **{k: str(v) for k, v in commands.items()})
    for phase, extra in (('prepare', ['--extras', '[]']), ('install', []),
                         ('dependencies', ['--extras', '[]', '--test-environment'])):
        python = bootstrap if phase != 'dependencies' else current.get('HERMES_PYTHON')
        if not python:
            raise RuntimeError('Upstream test environment did not export its interpreter')
        runner('hermes-test-runtime-'+phase, [python, '-S', setup, phase, '--toolchain', 'python',
                                             '--home', str(pm_home), *extra], src, current, logs)
        current = apply_github_file_commands(dict(env, **{k: str(v) for k, v in commands.items()}),
                                             commands['GITHUB_ENV'], commands['GITHUB_PATH'])
    for key in commands:
        current.pop(key, None)
    python = Path(current.get('HERMES_PYTHON', ''))
    if not current.get('VIRTUAL_ENV') or not python.is_file() or python.parent.parent != Path(current['VIRTUAL_ENV']):
        raise RuntimeError('Upstream test environment did not export its interpreter')
    return current


def run(label, command, cwd, env, logs, allow_failure=False):
    logs.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    with (logs/(label+'.log')).open('w', encoding='utf-8') as log:
        log.write(json.dumps({'command':list(map(str,command)), 'cwd':str(cwd), 'envKeys':sorted(env)})+'\n')
        log.flush()
        p = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        for line in p.stdout:
            print(line, end='', flush=True)
            log.write(line)
        rc = p.wait()
        log.write(f'\nEXIT_CODE={rc}; SECONDS={time.monotonic()-start:.2f}\n')
    if rc and not allow_failure:
        raise RuntimeError(f'{label} failed with exit {rc}; see {logs/(label+".log")}')
    return rc


# Exact upstream commit -> reviewed source line of the same assertion. The line
# moves when upstream edits the test file; each pin is reviewed and bound here.
WINDOWS_DARWIN_MODE_LINES = {
    '939e45c91d751fadd94dcd1b873ac3cb44846213': 597,
    'f97608f178d1ffeca59860195ab7da295f7c8e5f': 625,
}
KNOWN_COMMIT = '939e45c91d751fadd94dcd1b873ac3cb44846213'
WINDOWS_DARWIN_MODE_FIXTURE = (
    'scripts/stage-native-deps.test.mjs',
    'darwin staging ships the Swift helper executable and the rewritten windows.js',
)
WINDOWS_DARWIN_MODE_MESSAGE = (
    'AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:',
    '438 !== 493',
)
def _windows_darwin_mode_stack(line_no):
    return re.compile(
        r'^at .*[\\/]apps[\\/]desktop[\\/]scripts[\\/]stage-native-deps\.test\.mjs:'
        + str(int(line_no)) + r':12$'
    )
ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*m')


def _matches_windows_darwin_mode_fixture(name, assertion, pin, platform):
    messages = assertion.get('failureMessages')
    if not (
        platform == 'win32'
        and pin['commit'] in WINDOWS_DARWIN_MODE_LINES
        and name.endswith('/' + WINDOWS_DARWIN_MODE_FIXTURE[0])
        and assertion.get('fullName') == WINDOWS_DARWIN_MODE_FIXTURE[1]
        and isinstance(messages, list)
        and len(messages) == 1
        and isinstance(messages[0], str)
    ):
        return False
    lines = [line.strip() for line in ANSI_ESCAPE.sub('', messages[0]).splitlines() if line.strip()]
    return (
        len(lines) >= 3
        and tuple(lines[:2]) == WINDOWS_DARWIN_MODE_MESSAGE
        and all(line.startswith('at ') for line in lines[2:])
        and any(_windows_darwin_mode_stack(WINDOWS_DARWIN_MODE_LINES[pin['commit']]).fullmatch(line)
                for line in lines[2:])
    )


def gate_test_report(report, pin, returncode, platform=None):
    completion=report.get('runCompletion',{})
    if not completion or completion.get('unhandledErrors') or completion.get('reason') not in ('passed','failed'):
        raise ValueError('Missing completion receipt, interrupted run or unhandled error')
    if report.get('numTotalTests',0) < 1 or report.get('numRuntimeErrorTestSuites',0):
        raise ValueError('Empty test run or runtime error')
    assertions=[a for s in report.get('testResults',[]) for a in s.get('assertionResults',[])]
    counts=Counter(a['status'] for a in assertions)
    expected={'numTotalTests':len(assertions),'numPassedTests':counts['passed'],
              'numFailedTests':counts['failed'],'numPendingTests':counts['pending']+counts['skipped'],
              'numTodoTests':counts['todo']}
    if set(counts)-{'passed','failed','pending','skipped','todo'} or any(report.get(k,0)!=v for k,v in expected.items()):
        raise ValueError('Inconsistent test totals')
    if returncode!=(1 if counts['failed'] else 0) or completion['reason']!=('failed' if counts['failed'] else 'passed'):
        raise ValueError('Unexpected test exit code/completion reason')
    failures = []
    for suite in report.get('testResults',[]):
        if suite.get('message'):
            raise ValueError('Unreviewed suite/hook error: '+suite['message'])
        assertions = suite.get('assertionResults',[])
        failed = [a for a in assertions if a['status']=='failed']
        if suite.get('status')=='failed' and not failed:
            raise ValueError('Failed suite without assertion results: '+suite['name'])
        for a in failed:
            name = suite['name'].replace('\\','/')
            match = None
            if _matches_windows_darwin_mode_fixture(name, a, pin, platform):
                match = ('Cross-Darwin fixture asserted POSIX 0755 but Windows reported 0666; '
                         'actual Mac helper mode is verified in both native Mac builds.')
            if not match:
                raise ValueError(f'Unreviewed upstream failure: {name}: {a["fullName"]}')
            failures.append({'file':name,'test':a['fullName'],'reason':match})
    if len(failures)!=report.get('numFailedTests',0) or (returncode and not failures):
        raise ValueError('Test totals/exit code inconsistent with assertion failures')
    return {'total':report['numTotalTests'], 'passed':report.get('numPassedTests'),
            'failed':report.get('numFailedTests',0), 'pending':report.get('numPendingTests',0),
            'allowedFailures':failures, 'suiteGreen':returncode==0, 'releaseGatePassed':True}


PNPM_URL = re.compile(r'https://registry\.npmjs\.org/(?:@pnpm/exe\.[a-z0-9-]+/-/exe\.[a-z0-9-]+|pnpm/-/pnpm)-[0-9]+\.[0-9]+\.[0-9]+\.tgz')


def provision_pnpm(src, target, dest, fetch=None):
    """Install upstream's pm/lock.json pnpm (native exe + launcher dist/node-gyp), SHA256-verified.

    No global install: the binary lives in the private build workspace only.
    """
    lock = json.loads((Path(src)/'pm/lock.json').read_text())
    artifacts = lock['packages']['pnpm']['artifacts'][target]
    if not isinstance(artifacts, list) or len(artifacts) != 2:
        raise ValueError('Unexpected pnpm artifact pin for ' + target)
    fetch = fetch or (lambda url: urllib.request.urlopen(url, timeout=120).read())
    dest = Path(dest)
    if dest.exists():
        raise RuntimeError('Refusing to reuse a pnpm tool directory')
    dest.mkdir(parents=True)
    for artifact in artifacts:
        url, sha = artifact['url'], artifact['sha256']
        if not PNPM_URL.fullmatch(url) or not re.fullmatch('[0-9a-f]{64}', sha):
            raise ValueError('Unexpected pnpm artifact URL/hash')
        data = fetch(url)
        if hashlib.sha256(data).hexdigest() != sha:
            raise ValueError('pnpm artifact SHA256 mismatch: ' + url)
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
            members = [m for m in tar.getmembers() if m.name.startswith('package/')]
            if '/@pnpm/exe.' not in url:
                members = [m for m in members if m.name.startswith('package/dist/')]
            for member in members:
                member.name = member.name[len('package/'):]
            tar.extractall(dest, members=members, filter='data')
    binary = dest/('pnpm.exe' if target.startswith('win32') else 'pnpm')
    if not binary.is_file():
        raise RuntimeError('Pinned pnpm executable missing after extraction')
    binary.chmod(0o755)
    return binary
