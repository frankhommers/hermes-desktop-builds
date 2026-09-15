"""Exercise the actual migrator staging function on the native built app."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]/'mainstream'))
from installer import stage_app


def signature(app):
    entitlement = subprocess.run(['/usr/bin/codesign', '-d', '--entitlements', ':-', str(app)], capture_output=True, check=True)
    requirement = subprocess.run(['/usr/bin/codesign', '-d', '-r-', str(app)], capture_output=True, text=True, check=True)
    requirements = [line for line in (requirement.stdout+'\n'+requirement.stderr).splitlines() if line.startswith('designated =>')]
    assert len(requirements) == 1
    assert entitlement.stdout, 'Missing entitlement output'
    return {'entitlementsSha256':hashlib.sha256(entitlement.stdout).hexdigest(), 'designatedRequirement':requirements[0]}


def main():
    source, stage, logs = map(Path, sys.argv[1:])
    stage_app(source, stage)
    original, copied = signature(source), signature(stage)
    assert copied == original, 'Migrator staging changed entitlements or designated requirement'
    (logs/'migrator-staging.json').write_text(json.dumps({'upstream':original, 'staged':copied, 'signaturePreserved':True}, indent=2)+'\n')

if __name__ == '__main__': main()
