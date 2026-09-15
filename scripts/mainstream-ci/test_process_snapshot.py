import unittest
from process_snapshot import current_user_commands

class ProcessSnapshots(unittest.TestCase):
    def test_owner_filter_and_command_preservation(self):
        text='    0    1 /sbin/launchd\n  501   99 /Users/u/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main serve\n  501 100 /Applications/Hermes.app/Contents/MacOS/Hermes --user-data-dir=/path with spaces\n'
        self.assertEqual(current_user_commands(text,501),[(99,'/Users/u/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main serve'),(100,'/Applications/Hermes.app/Contents/MacOS/Hermes --user-data-dir=/path with spaces')])
    def test_malformed_snapshot_is_not_silent_success(self):
        for text in ['501 42', 'unknown 23 command', '501 pid command']:
            with self.assertRaises(ValueError):
                current_user_commands(text,501)
    def test_blank_snapshot(self):
        self.assertEqual(current_user_commands(' \n',501),[])

if __name__=='__main__': unittest.main()
