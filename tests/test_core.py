import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from common import validate_pin, clean_environment, gate_test_report
from package import binary_arches, make_archive, inventory, unpack_archive

PIN = {'repository': 'NousResearch/hermes-agent', 'commit': 'b0ab2e163a50d4e6c36507eba955a6067fde6abc', 'version': '0.17.0', 'revision': 1, 'node': '22.22.2'}

class BuildTests(unittest.TestCase):
    def test_pin_is_exact(self):
        self.assertEqual(validate_pin(PIN)['commit'], PIN['commit'])
        for bad in ['main', PIN['commit']+' ', PIN['commit'].upper(), '0'*40, ';touch nope']:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_pin({**PIN, 'commit': bad})

    def test_no_repository_or_version_injection(self):
        for key, val in [('repository','elsewhere/repo'), ('version','../x'), ('revision',True), ('revision',0)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_pin({**PIN,key:val})

    def test_environment_drops_secrets_and_agent_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            env=clean_environment(Path(tmp), PIN, {'PATH':'/usr/bin', 'GH_TOKEN':'secret', 'OPENAI_API_KEY':'secret', 'HERMES_HOME':'/live', 'GITHUB_SHA':'wrong', 'NODE_OPTIONS':'--require malicious.js'})
            self.assertNotIn('GH_TOKEN',env)
            self.assertNotIn('OPENAI_API_KEY',env)
            self.assertNotEqual(env['HERMES_HOME'],'/live')
            self.assertEqual(env['GITHUB_SHA'],PIN['commit'])
            self.assertNotIn('malicious',env['NODE_OPTIONS'])

    def test_unexpected_test_failures_block(self):
        report={'numTotalTests':1,'numFailedTests':1,'numRuntimeErrorTestSuites':0,'testResults':[{'name':'other.test.ts','status':'failed','assertionResults':[{'fullName':'bad','status':'failed'}]}]}
        with self.assertRaises(ValueError): gate_test_report(report, PIN, 1)
        with self.assertRaises(ValueError): gate_test_report({'numTotalTests':0},PIN,0)
        with self.assertRaises(ValueError): gate_test_report({'numTotalTests':1,'numFailedTests':0,'testResults':[]},PIN,1)

    def test_clean_test_report(self):
        r={'numTotalTests':3,'numPassedTests':3,'numFailedTests':0,'numRuntimeErrorTestSuites':0,'testResults':[]}
        self.assertEqual(gate_test_report(r,PIN,0)['allowedFailures'],[])

    def test_binary_headers(self):
        # Synthetic headers are unit-test fixtures only, never app payloads.
        for cpu,arch in [(0x0100000c,'arm64'),(0x01000007,'x64')]:
            self.assertEqual(binary_arches(struct.pack('<8I',0xfeedfacf,cpu,0,2,0,0,0,0)),('darwin',{arch}))
        elf=bytearray(64);elf[:6]=b'\x7fELF\x02\x01';struct.pack_into('<H',elf,18,62)
        self.assertEqual(binary_arches(elf),('linux',{'x64'}))
        pe=bytearray(128);pe[:2]=b'MZ';struct.pack_into('<I',pe,60,64);pe[64:68]=b'PE\0\0';struct.pack_into('<H',pe,68,0x8664)
        self.assertEqual(binary_arches(pe),('win32',{'x64'}))
        with self.assertRaises(ValueError):binary_arches(b'MZbroken')
        self.assertEqual(binary_arches(b'normal text'),(None,set()))

    @unittest.skipIf(sys.platform=='win32','Unix mode/symlink roundtrip; Windows ZIP tested in CI')
    def test_archive_roundtrip_preserves_native_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); app=base/'Hermes.app';app.mkdir();(app/'bin').mkdir()
            exe=app/'bin/Hermes';exe.write_bytes(b'payload');exe.chmod(0o755)
            (app/'entry').symlink_to('bin/Hermes')
            for suffix in ['.zip','.tar.gz']:
                archive=base/('app'+suffix);make_archive(app,archive)
                target=base/('unpack'+suffix);unpack_archive(archive,target)
                self.assertEqual(inventory(app),inventory(target/app.name))
            (app/'bad').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):inventory(app)

if __name__=='__main__':unittest.main()
