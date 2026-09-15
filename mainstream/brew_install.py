#!/usr/bin/env python3
"""Homebrew installation only; all later app updates belong to official Hermes."""
import argparse
from pathlib import Path
import sys
import installer as i

LEGACY = 'frankhommers/tap/hermes-desktop'


def handoff(brew, app):
    userdata = Path.home()/'Library/Application Support/Hermes'
    i.preflight(userdata, app)
    installed = i.run([brew, 'list', '--cask', '--full-name']).splitlines()
    owners = [name for name in installed if name.split('/')[-1] == 'hermes-desktop']
    if owners and owners != [LEGACY]:
        raise i.Refusal('An unrecognized Hermes cask is installed; review its update ownership first.')
    changed_pin = False
    try:
        if owners:
            if '--cask' not in i.run([brew, 'pin', '--help']):
                raise i.Refusal('This Homebrew cannot pin casks. Run brew update before migration.')
            pinned = i.run([brew, 'list', '--cask', '--pinned']).splitlines()
            if 'hermes-desktop' not in pinned:
                i.run([brew, 'pin', '--cask', LEGACY])
                changed_pin = True
            if 'hermes-desktop' not in i.run([brew, 'list', '--cask', '--pinned']).splitlines():
                raise i.Refusal('Legacy Homebrew updates could not be disabled; app was not replaced.')
        i.install(userdata, app, confirmed=True)
    except BaseException:
        if changed_pin:
            # Restore only a pin introduced by this migration, never the user's pin.
            i.run([brew, 'unpin', '--cask', LEGACY])
        raise
    print('Future Hermes app updates: use the official in-app updater, not brew upgrade.')
    if owners:
        print('The old hermes-desktop cask is pinned to prevent Homebrew overwriting the official app.')
        print('Do not unpin/reinstall it. Its uninstall still removes Hermes.app; no user data was deleted.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--brew', type=Path, required=True)
    parser.add_argument('--app', type=Path, required=True)
    args = parser.parse_args()
    if str(args.brew) not in ('/opt/homebrew/bin/brew', '/usr/local/bin/brew'):
        parser.error('Use the standard native Homebrew executable.')
    try:
        handoff(args.brew, args.app)
    except (i.Refusal, OSError, KeyboardInterrupt) as error:
        print('Stopped: '+(str(error) if isinstance(error, i.Refusal) else 'Interrupted; retain migration backups.'), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
