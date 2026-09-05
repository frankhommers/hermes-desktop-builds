from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from release import cask_text, prepare

class ReleaseTests(unittest.TestCase):
    def test_cask_selects_arch_and_never_runs_installers(self):
        assets={f'darwin-{a}':{'archive':f'Hermes-0.17.0.1-darwin-{a}-adhoc.zip','sha256':str(i)*64} for i,a in enumerate(('arm64','x64'),1)}
        text=cask_text('0.17.0.1',assets)
        self.assertIn('on_arm do',text);self.assertIn('on_intel do',text)
        self.assertIn('app "Hermes.app"',text)
        for prohibited in ('postflight','preflight','system_command','xattr','zap trash','sha256 :no_check'):
            self.assertNotIn(prohibited,text)
        with self.assertRaises(ValueError):cask_text('0.17.0.1; injection',assets)
        assets['darwin-arm64']['sha256']='bad'
        with self.assertRaises(ValueError):cask_text('0.17.0.1',assets)

    def test_missing_distributions_never_create_publish_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);dest=base/'release'
            with self.assertRaises(ValueError):prepare(base,dest,'https://github.com/frankhommers/hermes-desktop-builds/actions/runs/123')
            self.assertFalse(dest.exists())
            with self.assertRaises(ValueError):prepare(base,dest,'https://evil.example/run/123')
            self.assertFalse(dest.exists())

if __name__=='__main__':unittest.main()
