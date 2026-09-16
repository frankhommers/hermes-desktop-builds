# One-time official Hermes Desktop migration

For a published archive, the attached `native-verification.json` binds the exact
installer bytes to the completed native Homebrew migration and official-update
checks. Source-branch changes remain candidates until those gates pass. Synthetic
CI does not prove real-account VPS authentication, personal-Mac Finder toolchain
discovery, sleep/reconnect or Gatekeeper acceptance.

## Installer 1.0.1: third-party launchd files

An invalid `battery.plist` or unreadable vendor plist no longer blocks migration
merely because the file cannot be parsed. Such non-Hermes-named files produce a
warning with the filename and error type; they are not repaired, removed or
chmodded. Their contents are **not claimed to be audited** when unreadable.
Hermes-named registrations still block even if unreadable or stopped, as do
Hermes references in readable files and loaded launchd services. The installer
also continues checking running Hermes processes. This checks normal Hermes
startup/update behavior; it is not a general audit of every third-party service.

This installs a real, unmodified official Git checkout on `main` at initial
commit `f13a87e610611ce6d9fd82bff8c2d2a642312183`, tracking `origin/main` from
`https://github.com/NousResearch/hermes-agent.git`, at
`~/.hermes/hermes-agent`. It creates `venv`, installs the project's **base**
Python dependencies, and runs `venv/bin/hermes desktop --force-build --build-only`.
The official app/updater and source build artifacts are retained. Later updates
belong exclusively to the official in-app source updater and can grow the Python
dependencies to official `.[all]`. No custom updater/feed, persistent patch,
setup wizard, gateway installation, or app launch is used. The Homebrew entrypoint
only installs prerequisites and pins the legacy community cask once.

## Scope and prerequisites

- Existing **Apple Silicon macOS** Hermes Electron client, closed during migration.
- Python >=3.11,<3.14 with `venv`/pip; Xcode Command Line Tools (git/clang).
- Node `^22.22.0 || ^24.11.0 || >=26.0.0`; npm `<11.10.0 || >=11.17.0`.
  Install prerequisites yourself first. Keep Node/npm available after migration;
  prefer a stable standard `/usr/local/bin`, `/opt/homebrew/bin`, or
  `~/.hermes/node/bin` installation discoverable by the official updater. A
  transient activated NVM shell alone is **not native Finder acceptance**.
- Existing writable `/Applications/Hermes.app` and its writable parent (or pass
  `--app "$HOME/Applications/Hermes.app"` for an existing user-local app).
- Existing userData must be `~/Library/Application Support/Hermes`.
  Custom/exported `--user-data` paths are refused: reading a different directory
  does not establish the unmodified app's saved Finder-launch routing.
- No Hermes launchd plist or loaded registration, including stopped/unloaded
  registrations. The migrator refuses these; it never uninstalls a service.
- No `HERMES_*` environment overrides, no symlinked managed paths, no root/sudo.
- An existing source directory must already be a clean real clone at the exact
  initial revision and official origin/main. An existing venv is refused, not
  overlaid. Move/review conflicting installations manually with backups; this
  installer does not reset or erase an existing source installation.

In the **old app**, save/authenticate the VPS as the **machine-global Remote**
connection and matching registry primary. Select **Primary gateway** startup,
not Last used. Remove legacy per-profile connection overrides through Settings.
Close any local or ambiguously owned restored session/Bot tiles and quit the app.
An opaque Chromium `Local Storage` directory is normal and is **not rejected**.

## Use

### Initial installation through Homebrew

Once the checksum-pinned release and tap entry are published:

```sh
brew install --cask frankhommers/tap/hermes-desktop-mainstream
```

This command performs the migration itself; it does not merely download an
installer that needs another manual command. The supported starting point is
an existing Apple Silicon Hermes.app with the saved remote configuration above.
The cask requires macOS Sequoia or newer (the native tested OS baseline), installs
Node and Python 3.12, and builds the unmodified official client without launching it.
For another existing application folder pass `--appdir="$HOME/Applications"`.

If the legacy `frankhommers/tap/hermes-desktop` cask is installed, it is **pinned**
before migration so `brew upgrade` cannot overwrite the official client later.
A failed migration restores its previous pin state. Keep that cask pinned; do
not reinstall it. It retains its old uninstall receipt: uninstalling that legacy
cask still removes Hermes.app. Nothing is silently deleted or forgotten.

The new bootstrap cask uses `auto_updates true`, has no `app` artifact and does
not manage app updates. Keep its receipt installed so Homebrew retains the
Python/Node dependencies. Its uninstall hook does not delete the app or settings,
but Homebrew autoremove may remove dependencies if no other formula needs them.
Use **Hermes's in-app updater** for every later app update, not `brew upgrade`.
This is an initial Homebrew install followed by mainstream updates, not a custom
update channel. It is a migration route, not fresh-account onboarding.

### Direct installer alternative

Keep this directory's files together and double-click `Install.command`.
The terminal remains open on success/failure. Type `INSTALL` to consent.
Alternatively:

```sh
python3.12 mainstream/installer.py --preflight-only
python3.12 mainstream/installer.py
```

`--preflight-only` is read-only but does **not** claim restored Chromium scopes
have been inspected: that inspection needs the Electron dependency installed by
the build. `--yes` consents to the same installation/backups without a prompt;
it does not enable deleting data. No production installation is authorized by
this repository containing the tool.

## Data preservation and restore audit

Original userData, cookies, OAuth token storage and encrypted saved connection
credentials are never rewritten, decrypted, printed or cleared. Before installing,
the tool makes a private (0700 parent) full userData backup plus existing
`.hermes/config.yaml`, `.env`, `auth.json`, and `profiles`. Backups may contain
secrets and should stay private. Subprocess output is suppressed in the terminal
because tools can echo credential material; private `commands.log` under the
backup directory retains diagnostics. Do not publish that log without review.

`storage-audit.cjs` uses the newly installed Electron dependency and a **private
copy** of userData, never the live profile. It loads only a blank local HTML page,
blocks HTTP(S)/WebSocket traffic, has no preload/Node renderer access, and never
loads Hermes code or starts its backend. It reads only these actual upstream
restore keys:

- `hermes.desktop.sessionTiles.v1`
- `hermes.desktop.sessionTiles.v2` (including `__bots_workspace__`)

The validator rejects explicit local routes, unknown connection owners and
legacy tiles with ambiguous owner scope. Remote-owned tiles and all unrelated
preferences/auth remain intact. A refusal asks you to close the offending tiles
in the existing app and quit before retrying. There is no `localStorage.clear`,
no synthetic `restore.json` contract, and no automatic data cleanup.

Source anchors at the pinned revision: `src/store/session-states.ts:792-910`
contains the tile serialization/restore formats; `src/store/session.ts:74-139`
scopes last navigation by profile and connection; Electron `window-state.ts` is
geometry-only; secondary window ownership is a runtime query parameter
(`src/store/windows.ts`). Packaged renderer URLs use `file://`
(`electron/main.ts:14835-14840`). **The copy/blank-file storage-origin read was
demonstrated with seeded real Chromium databases on ARM and Intel in the run
linked above**: remote tiles are accepted, local tiles rejected, and original
database bytes preserved. This is not a real-account OAuth persistence test.

## Swap, recovery, and future ownership

The build tree remains in the canonical source for official updater discovery.
The app is copied with `ditto` into a same-parent staging directory, preserving
the official build's signature, entitlements and designated requirement. It is
verified with `codesign --verify --deep --strict` before and after renaming;
the migrator never re-signs the bundle.
The old app is kept next to the destination as `Hermes.pre-mainstream-*.app`.
If the post-swap verification fails, the old app is renamed back automatically.
Ad-hoc signing is **not** Developer ID signing or notarization; quarantine is not
silently stripped and native Gatekeeper acceptance is outstanding.

On an interrupted/failed build, source/venv and private backups are intentionally
retained for diagnosis; there is no destructive automatic source cleanup. A retry
will refuse the existing venv until reviewed. On manual rollback, keep both apps,
close all Hermes processes, move the new app aside and rename the retained old
app to its former name. Settings were not changed, so normally do not restore
userData. If restoring a backup is necessary, keep the present data too and only
restore with the app fully closed. Do not delete source/venv while relying on
the official updater.

The app is **not automatically opened** after installation. Remote-primary plus
no services means *no local autostart under the audited configuration*, not hard
OFF: deliberate local selection, changed/lost routing, or future upstream behavior
can start a local backend. No permanent runtime policy is installed.

## Verification / release gate

```sh
python3 -m unittest discover -s mainstream -p 'test_*.py' -v
node --check mainstream/storage-audit.cjs
bash -n mainstream/Install.command
```

Required native acceptance before delivery: actual clean build and stable
Finder-context prerequisites; seeded storage-copy audit that detects local tiles
and preserves remote tiles/auth; authenticated VPS HTTP/WebSocket/chat; process
and listening-socket capture across first start, roster/Bots refresh, VPS outage,
sleep/reconnect; an actual advancing official update across two real revisions,
source/app replacement and relaunch; signature/quarantine/LaunchServices and
failed-swap rollback. Verify OAuth remains usable and the old app/config backups
remain intact. No Linux fixture substitutes for these gates.
