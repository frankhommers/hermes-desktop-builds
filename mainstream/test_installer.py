"""Stdlib tests: temporary fixtures only, not native Mac acceptance."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import installer as i


def fixture():
    return ({'mode': 'remote', 'remote': {'url': 'https://example.invalid', 'authMode': 'oauth'}, 'profiles': {}},
            {'version': 2, 'primary': 'remote-1', 'launchMode': 'primary', 'connections': [
                {'id': 'local', 'kind': 'local', 'label': 'This device'},
                {'id': 'remote-1', 'kind': 'remote', 'label': 'Remote', 'url': 'https://example.invalid', 'authMode': 'oauth'}]})


class SafetyTests(unittest.TestCase):
    def test_independent_ciphertexts_do_not_change_route_identity(self):
        c, r = fixture()
        c['remote'].update(authMode='token', token={'encrypted': 'envelope-a'})
        r['connections'][1].update(authMode='token', token={'encrypted': 'envelope-b'})
        i.validate_saved_route(c, r)

    def test_incomplete_owner_route_is_rejected(self):
        for profile in (None, 123):
            tile = {'storedSessionId': 's', 'ownerProfile': 'default', 'ownerRoute': {'connectionId': 'remote-1'}}
            if profile is not None:
                tile['ownerRoute']['profile'] = profile
            with self.subTest(profile=profile), self.assertRaises(i.Refusal):
                i.validate_restore_storage(Path('/unused'), {i.TILE_KEYS[1]: json.dumps({'default': [tile]})}, {'remote-1'})

    def test_stage_preserves_upstream_signature(self):
        with patch.object(i, 'run') as run, patch.object(i, 'verify_app') as verify:
            i.stage_app(Path('/source/Hermes.app'), Path('/staged/Hermes.app'))
            run.assert_called_once_with(['/usr/bin/ditto', Path('/source/Hermes.app'), Path('/staged/Hermes.app')])
            verify.assert_called_once_with(Path('/staged/Hermes.app'))

    def test_invalid_restore_shape_is_rejected(self):
        with self.assertRaises(i.Refusal):
            i.validate_restore_storage(Path('/unused'), {i.TILE_KEYS[1]: '[]'})

    def test_valid_route_unchanged(self):
        c, r = fixture(); before = copy.deepcopy((c, r))
        i.validate_saved_route(c, r)
        self.assertEqual((c, r), before)

    def test_reject_unsafe_routes(self):
        mutations = [lambda c,r: c.update(mode='local'), lambda c,r: c.update(profiles={'work': {'mode':'remote'}}),
                     lambda c,r: r.update(primary='local'), lambda c,r: r.update(launchMode='last-used'),
                     lambda c,r: r['connections'][1].update(url='https://other.invalid'),
                     lambda c,r: r.update(connections=r['connections'][1:]),
                     lambda c,r: c['remote'].update(url='https://secret@example.invalid'),
                     lambda c,r: r.update(version=3)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                c,r=fixture(); mutate(c,r)
                with self.assertRaises(i.Refusal): i.validate_saved_route(c,r)

    def test_linux_fails_before_read(self):
        with patch.object(i.platform, 'system', return_value='Linux'), patch.object(i, 'read_json') as read:
            with self.assertRaises(i.Refusal): i.preflight(Path('/absent'), Path('/absent/Hermes.app'))
            read.assert_not_called()

    def test_existing_chromium_storage_not_itself_unsafe(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); (p/'Local Storage').mkdir(); db=p/'Local Storage'/'db'; db.write_bytes(b'opaque')
            i.validate_restore_storage(p, {k: None for k in i.TILE_KEYS})
            self.assertEqual(db.read_bytes(), b'opaque')

    def test_explicit_local_restore_rejected(self):
        snapshot = {i.TILE_KEYS[1]: json.dumps({'default': [{'storedSessionId':'s', 'ownerRoute': {'connectionId':'local','profile':'default','mode':'local'}}]})}
        with self.assertRaises(i.Refusal): i.validate_restore_storage(Path('/unused'), snapshot)

    def test_saved_remote_tile_preserved(self):
        snapshot = {i.TILE_KEYS[1]: json.dumps({'default': [{'storedSessionId':'s', 'ownerRoute': {'connectionId':'remote-1','profile':'work','mode':'remote'}}]})}
        before = copy.deepcopy(snapshot)
        i.validate_restore_storage(Path('/unused'), snapshot, {'remote-1'})
        self.assertEqual(before, snapshot)

    def test_legacy_ambiguous_tile_rejected(self):
        with self.assertRaises(i.Refusal):
            i.validate_restore_storage(Path('/unused'), {i.TILE_KEYS[0]: json.dumps([{'storedSessionId':'s'}])})

    def test_failed_post_swap_verification_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); app=root/'Hermes.app'; stage=root/'new.app'; backup=root/'old.app'
            app.mkdir(); (app/'old').touch(); stage.mkdir(); (stage/'new').touch()
            def verify(path):
                if path == app: raise i.Refusal('verification failed after swap')
            with self.assertRaises(i.Refusal): i.swap_app(stage,app,backup,verify)
            self.assertTrue((app/'old').exists()); self.assertTrue((stage/'new').exists())

    def test_invalid_json_redacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'connection.json'; p.write_text('SECRET-not-json')
            with self.assertRaises(i.Refusal) as e: i.read_json(p)
            self.assertNotIn('SECRET', str(e.exception))

    def test_node_ranges(self):
        for node,npm in [('22.22.0','10.9.0'),('24.11.0','11.17.0'),('26.0.0','12.0.0')]: i.validate_node(node,npm)
        for node,npm in [('22.21.0','10.9.0'),('23.0.0','10.9.0'),('24.11.0','11.10.0')]:
            with self.assertRaises(i.Refusal): i.validate_node(node,npm)

    def test_swap_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); app=root/'Hermes.app'; stage=root/'new.app'; backup=root/'old.app'
            app.mkdir(); (app/'old').write_text('old'); stage.mkdir()
            def verify(path): raise i.Refusal('bad signature')
            with self.assertRaises(i.Refusal): i.swap_app(stage,app,backup,verify)
            self.assertTrue((app/'old').exists()); self.assertFalse(backup.exists())

    def test_swap_success_preserves_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); app=root/'Hermes.app'; stage=root/'new.app'; backup=root/'old.app'
            app.mkdir(); (app/'old').touch(); stage.mkdir(); (stage/'new').touch()
            i.swap_app(stage,app,backup,lambda p: None)
            self.assertTrue((app/'new').exists()); self.assertTrue((backup/'old').exists())

    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); (p/'link').symlink_to(p/'missing')
            with self.assertRaises(i.Refusal): i.no_symlinks(p/'link'/'child')


if __name__ == '__main__': unittest.main()
