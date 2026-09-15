"""Read-only native process/port sampling on a disposable macOS runner.

Native ps avoids psutil's macOS proc_cmdline SystemError for protected processes.
Production classifiers stay unchanged. Sampling is not an execution prohibition
or proof against sub-sample transient processes. Any observer failure stays fatal.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from process_snapshot import current_user_commands

source, logs = map(Path, sys.argv[1:3])
sys.path.insert(0, str(source))
from hermes_cli.update_cmd_windows import _hermes_holder_subcommand
from gateway.status import looks_like_gateway_command_line

violations = {}
samples = 0
max_gap = 0.0
last = time.monotonic()
next_sockets = last
errors = []
observed_until_stop = False
started_at = time.time()
roots = (source, Path.home()/'.hermes/hermes-agent')
try:
    while not (logs/'stop-watcher').exists():
        now = time.monotonic()
        max_gap = max(max_gap, now-last); last = now
        snapshot = subprocess.run(['/bin/ps','-ww','-axo','uid=,pid=,command='], capture_output=True, text=True, timeout=10, check=True)
        rows = current_user_commands(snapshot.stdout, os.getuid())
        if not any(pid == os.getpid() for pid, _ in rows):
            raise RuntimeError('Process snapshot omitted the observer itself')
        samples += 1
        for pid, command in rows:
            if pid == os.getpid():
                continue
            subcommand = _hermes_holder_subcommand(command)
            gateway = looks_like_gateway_command_line(command)
            direct_agent = any(re.search(re.escape(str(root)) + r'/(?:run_agent|cli)\.py(?:\s|$)', command) for root in roots)
            if gateway or direct_agent or subcommand in {'serve', 'dashboard', 'chat'}:
                violations[pid] = {'pid':pid, 'entry':subcommand or ('gateway' if gateway else 'direct-agent')}
        if now >= next_sockets:
            sockets = subprocess.run(['/usr/sbin/lsof','-nP','-iTCP','-sTCP:LISTEN'], capture_output=True, text=True, timeout=10)
            if sockets.returncode not in (0, 1):
                raise RuntimeError('lsof failed')
            with (logs/'listening-sockets.log').open('a') as f:
                f.write(f'=== sample {samples} ===\n'+sockets.stdout)
            next_sockets = now+1
        time.sleep(.05)
    observed_until_stop = True
except Exception as error:
    errors.append(type(error).__name__)
    raise
finally:
    (logs/'process-watch.json').write_text(json.dumps({'samples':samples,
        'maximumSampleGapSeconds':max_gap, 'violations':list(violations.values()),
        'observerErrors':errors, 'observedUntilStop':observed_until_stop,
        'startedAt':started_at, 'finishedAt':time.time(),
        'method':'read-only native ps + production classifiers + lsof'}, indent=2)+'\n')
if violations or errors or samples < 10 or not observed_until_stop:
    sys.exit(1)
