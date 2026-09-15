"""Read-only sampled process/port observer on a disposable macOS runner.

Uses production command classifiers. Sampling is evidence at recorded intervals,
not an OS execution prohibition or proof against sub-sample transient processes.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import psutil

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
try:
    while not (logs/'stop-watcher').exists():
        now = time.monotonic()
        max_gap = max(max_gap, now-last); last = now
        samples += 1
        for proc in psutil.process_iter(['pid', 'cmdline', 'uids']):
            try:
                if proc.info['uids'].real != os.getuid():
                    continue
                argv = proc.info['cmdline'] or []
                if not argv or proc.pid == os.getpid():
                    continue
                command = shlex.join(argv)
                subcommand = _hermes_holder_subcommand(command)
                gateway = looks_like_gateway_command_line(command)
                direct_agent = any(Path(token).name in {'run_agent.py', 'cli.py'} and str(source) in token for token in argv)
                if gateway or direct_agent or subcommand in {'serve', 'dashboard', 'gateway', 'chat'}:
                    violations[proc.pid] = {'pid':proc.pid, 'entry':subcommand or ('gateway' if gateway else 'direct-agent')}
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except psutil.AccessDenied:
                errors.append('AccessDenied on current-user process')
        if now >= next_sockets:
            sockets = subprocess.run(['/usr/sbin/lsof','-nP','-iTCP','-sTCP:LISTEN'], capture_output=True, text=True)
            if sockets.returncode not in (0, 1):
                errors.append('lsof failed')
            with (logs/'listening-sockets.log').open('a') as f:
                f.write(f'=== sample {samples} ===\n'+sockets.stdout)
            next_sockets = now+1
        time.sleep(.05)
finally:
    (logs/'process-watch.json').write_text(json.dumps({'samples':samples,
        'maximumSampleGapSeconds':max_gap, 'violations':list(violations.values()),
        'observerErrors':sorted(set(errors)), 'method':'read-only sampled psutil production classifiers + lsof'}, indent=2)+'\n')
if violations or errors or samples < 10:
    sys.exit(1)
