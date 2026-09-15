"""Native seeded Chromium copy audit. Fixtures are not real user auth."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]/'mainstream'))
from installer import Refusal, TILE_KEYS, validate_restore_storage


def hashes(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def main():
    source, sandbox, logs = map(Path, sys.argv[1:])
    electron = source/'node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'
    if not electron.exists():
        electron = source/'apps/desktop/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'
    results = []
    for mode in ('remote', 'local'):
        original = sandbox/f'storage-original-{mode}'; original.mkdir()
        subprocess.run([str(electron), str(HERE/'storage-seed.cjs'), str(original), mode], check=True, timeout=45)
        before = hashes(original)
        private_copy = sandbox/f'storage-copy-{mode}'
        shutil.copytree(original, private_copy)
        output = sandbox/f'storage-result-{mode}.json'
        subprocess.run([str(electron), str(HERE.parents[1]/'mainstream/storage-audit.cjs'), str(private_copy), str(output)], check=True, timeout=45)
        result = json.loads(output.read_text())
        assert result['auditVersion'] == 1 and result['origin'] == 'file://'
        restored = json.loads(result['keys'][TILE_KEYS[1]])
        assert restored['default'][0]['ownerRoute']['mode'] == mode, 'Audit must see the seeded original origin, not empty new storage'
        refused = False
        try:
            validate_restore_storage(original, result['keys'], {'remote-proof'})
        except Refusal:
            refused = True
        assert refused == (mode == 'local')
        assert hashes(original) == before, 'Original userData bytes changed during copy audit'
        results.append({'seed':mode, 'detected':True, 'refused':refused, 'originalBytesPreserved':True})
    (logs/'storage-native.json').write_text(json.dumps(results, indent=2)+'\n')

if __name__ == '__main__': main()
