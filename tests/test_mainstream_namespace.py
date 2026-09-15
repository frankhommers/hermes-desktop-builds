"""Synthetic namespace contracts: installer tags are not Desktop versions."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import update_upstream as u


class NamespaceTests(unittest.TestCase):
    def api(self, tag='mainstream-v1.0.0'):
        release = {'id':1,'tag_name':tag,'draft':False,'prerelease':False}
        def get(path):
            if '/releases?' in path:
                return [release, {'id':2,'tag_name':'v0.17.2.1','draft':False,'prerelease':False}]
            return [{'name':tag}, {'name':'v0.17.2.2'}]
        return get

    def test_installer_namespace_does_not_break_legacy_version_reservations(self):
        self.assertEqual(u.reserved_versions(self.api()), {(0,17,2,1),(0,17,2,2)})

    def test_unknown_or_malformed_namespace_still_fails_closed(self):
        for tag in ('mainstream-v1.0.0\n','mainstream-v01.0.0','mainstream-v1.0','mainstream-v1.0.0-rc1',
                    'MAINSTREAM-v1.0.0','other-v1.0.0','v1.0.0','mainstream-v'+'9'*100+'.0.0'):
            with self.subTest(tag=tag), self.assertRaises(u.UpdateError):
                u.reserved_versions(self.api(tag))

    def test_namespace_does_not_skip_visibility_validation(self):
        api = self.api()
        def invalid(path):
            rows = api(path)
            if '/releases?' in path:
                rows[0]['draft'] = 'false'
            return rows
        with self.assertRaises(u.UpdateError):
            u.reserved_versions(invalid)
