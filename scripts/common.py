"""Shared pin validation, isolated environment, logs and explicit test gates."""
import json
from collections import Counter
import os
from pathlib import Path
import re
import subprocess
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


KNOWN_COMMIT = 'b0ab2e163a50d4e6c36507eba955a6067fde6abc'
KNOWN_FAILURES = {
    ('src/components/ui/__tests__/no-native-title.test.ts', 'no native title= on button elements uses <Tip> instead of native title= on all button elements'):
        'Upstream listing-embed.tsx uses a native title; style rule fails, no source patch applied.',
    ('electron/ssh-connection.test.ts', 'controlSocketPath default base stays under sun_path even with the temp-listener suffix'):
        'Isolated long HOME exceeds the SSH control socket test path budget; URL remote smoke does not use SSH.'
}


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
            match = next((reason for (file,title),reason in KNOWN_FAILURES.items()
                          if name.endswith('/'+file) and a['fullName']==title),None)
            if platform=='win32' and name.endswith('/scripts/stage-native-deps.test.mjs') and a['fullName']=='darwin staging ships the Swift helper executable and the rewritten windows.js':
                match='Cross-Darwin fixture asserts POSIX 0755 on Windows (0666); actual Mac helper mode is verified in native Mac builds.'
            if pin['commit'] != KNOWN_COMMIT or not match:
                raise ValueError(f'Unreviewed upstream failure: {name}: {a["fullName"]}')
            failures.append({'file':name,'test':a['fullName'],'reason':match})
    if len(failures)!=report.get('numFailedTests',0) or (returncode and not failures):
        raise ValueError('Test totals/exit code inconsistent with assertion failures')
    return {'total':report['numTotalTests'], 'passed':report.get('numPassedTests'),
            'failed':report.get('numFailedTests',0), 'pending':report.get('numPendingTests',0),
            'allowedFailures':failures, 'suiteGreen':returncode==0, 'releaseGatePassed':True}
