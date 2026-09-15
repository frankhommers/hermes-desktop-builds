"""Exercise the actual release archive and Homebrew producer contract."""
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
import package_installer as p


class PackageTests(unittest.TestCase):
    def test_reproducible_archive_matches_cask_and_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, sha = p.package(Path(tmp)/'one')
            b, other = p.package(Path(tmp)/'two')
            self.assertEqual(a.read_bytes(), b.read_bytes())
            self.assertEqual(sha, other)
            self.assertEqual(sha, hashlib.sha256(a.read_bytes()).hexdigest())
            with zipfile.ZipFile(a) as z:
                self.assertEqual(set(z.namelist()), {'Hermes-mainstream/'+name for name in (*p.FILES, 'LICENSE')})
                for name in p.FILES:
                    self.assertEqual(z.read('Hermes-mainstream/'+name), (p.REPO/'mainstream'/name).read_bytes())
            cask = (a.parent/(p.TOKEN+'.rb')).read_text()
            self.assertIn(f'sha256 "{sha}"', cask)
            self.assertIn(p.PUBLIC_URL, cask)
            self.assertIn('auto_updates true', cask)
            self.assertNotIn('app "Hermes.app"', cask)

    def test_metadata_cannot_inject_ruby(self):
        for sha, url in [('bad', p.PUBLIC_URL), ('a'*64, 'file:///tmp/a"; evil'), ('a'*64, 'https://attacker.invalid/archive')]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                p.cask_text(sha, url)


if __name__ == '__main__':
    unittest.main()
