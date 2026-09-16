"""Deterministic installer archive and one-time Homebrew cask (no app updater)."""
import argparse
import hashlib
from pathlib import Path
import re
import zipfile

REPO = Path(__file__).resolve().parents[2]
VERSION = '1.0.1'
TOKEN = 'hermes-desktop-mainstream'
ASSET = f'Hermes-mainstream-{VERSION}.zip'
TAG = f'mainstream-v{VERSION}'
PUBLIC_URL = f'https://github.com/frankhommers/hermes-desktop-builds/releases/download/{TAG}/{ASSET}'
FILES = ('Install.command', 'installer.py', 'brew_install.py', 'storage-audit.cjs', 'README.md')


def package(destination):
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination/ASSET
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            info = zipfile.ZipInfo('Hermes-mainstream/'+name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100755 if name == 'Install.command' else 0o100644) << 16
            z.writestr(info, (REPO/'mainstream'/name).read_bytes())
        info = zipfile.ZipInfo('Hermes-mainstream/LICENSE', (2026, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        z.writestr(info, (REPO/'LICENSE').read_bytes())
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    (destination/(TOKEN+'.rb')).write_text(cask_text(sha))
    (destination/'SHA256SUMS').write_text(f'{sha}  {ASSET}\n')
    return archive, sha


def cask_text(sha, url=PUBLIC_URL):
    if not re.fullmatch('[a-f0-9]{64}', sha):
        raise ValueError('Invalid SHA256')
    if not (url == PUBLIC_URL or url.startswith('file:///')) or any(c in url for c in ('"', '\n', '#', '\\')):
        raise ValueError('Only immutable release URL or local native fixture allowed')
    cask_url = url.replace(VERSION, '#{version}') if url == PUBLIC_URL else url
    return f'''# frozen_string_literal: true

cask "{TOKEN}" do
  version "{VERSION}"
  sha256 "{sha}"

  url "{cask_url}"
  name "Hermes Desktop Mainstream"
  desc "One-time migration to the official in-app Desktop updater"
  homepage "https://github.com/frankhommers/hermes-desktop-builds"

  livecheck do
    skip "One-time installer; the installed client uses the official Hermes updater"
  end

  auto_updates true
  depends_on arch: :arm64
  depends_on formula: ["node", "python@3.12"]
  depends_on macos: :sequoia

  installer script: {{
    executable:   "#{{HOMEBREW_PREFIX}}/opt/python@3.12/bin/python3.12",
    args:         ["#{{staged_path}}/Hermes-mainstream/brew_install.py",
                   "--brew", "#{{HOMEBREW_PREFIX}}/bin/brew", "--app", "#{{appdir}}/Hermes.app"],
    must_succeed: true,
    sudo:         false,
  }}

  # The bootstrap receipt does not own/delete the app, source or user data.
  uninstall script: {{ executable: "/usr/bin/true" }}

  caveats <<~EOS
    Migrates an existing, closed Hermes.app with a saved remote-primary connection.
    Builds unmodified official source locally; installs Python/Node prerequisites.
    No local agent autostart, service registration or automatic app launch.
    Existing sources/services or unsafe saved routing cause a refusal, not deletion.
    The old app and private user-data backups are retained.
    An installed frankhommers/tap/hermes-desktop cask is pinned, not uninstalled.
    Keep it pinned: future app updates belong to the official in-app updater.
    Its uninstall still removes Hermes.app; this bootstrap's uninstall removes only its receipt.
    Ad-hoc signing is not Apple notarization; app-specific Gatekeeper approval may be needed.
    Keep Python/Node and ~/.hermes/hermes-agent for the official updater.
  EOS
end
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    archive, sha = package(args.destination)
    print(archive, sha)
