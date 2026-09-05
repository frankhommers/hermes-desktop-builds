# Hermes Desktop Builds

Community builds of the **real, unmodified Nous Research Hermes Electron Desktop**.
This is a build/distribution repository, not a Hermes fork, website wrapper, or the
Tauri `Hermes-Setup` agent bootstrap installer. Not an official Nous Research release.

## Scope

Native CI lanes: macOS Apple Silicon (`arm64`), macOS Intel (`x64`), Windows x64,
and Linux x64. Each build packages Electron, the real UI and real native modules.
No Python runtime, agent checkout, venv, credentials or personal backend URL is bundled.
No global installs, signing credentials, sudo or system configuration changes are used.
All caches, source and test homes live in `.work/`; outputs live in `out/`.

**These are unsigned community previews**, not notarized/Developer-ID or Authenticode
releases. A native CI starttest does not prove Gatekeeper/SmartScreen acceptance of
a downloaded application. Do not disable OS security or strip quarantine automatically.

## Remote use: important

On a clean first start choose **Connect to existing Hermes**, NOT **Install Hermes locally**.
The remote form does not start the local installer. A running remote Hermes
`serve`/dashboard backend is required; no local Node, Python or Hermes CLI is required.

This is **not a hard-locked remote-only fork**: the upstream local-install button remains.
If Hermes is already installed locally, upstream discovery may start that existing runtime
before first-run setup. Review your existing client/runtime configuration first; do not
blindly launch on such a machine when local agent startup must be avoided. Runtime
discovery may execute a short system-Python import probe even on an otherwise clean host;
that is not an agent/server startup, and is not a bundled Python runtime.

No live remote credentials/login/WebSocket/chat session is exercised by this build.
The real smoke test checks the first-run UI, remote form, a rejected connection to a
closed loopback port, inactive bootstrap state and the actual packaged native PTY.

## Downloads and installation

Use the [GitHub Releases](https://github.com/frankhommers/hermes-desktop-builds/releases)
page. Only publish a release after all four native lanes pass the distribution gate.
Checksums, source pin and per-platform validation evidence accompany each release.

### macOS

The release ZIP contains a complete `Hermes.app`. Use the archive for your CPU.
macOS 12+ is the initial binary metadata floor, not a tested compatibility matrix.

Once the cask is published in [frankhommers/tap](https://github.com/frankhommers/homebrew-tap):

```sh
brew install --cask frankhommers/tap/hermes-desktop
# Later:
brew update
brew upgrade --cask frankhommers/tap/hermes-desktop
```

Homebrew installs the app only. It does not install a local Hermes agent or bypass
Gatekeeper. For manual installation, verify the ZIP's SHA256 against `SHA256SUMS`
(`shasum -a 256 <download.zip>`), then use macOS `ditto`, preserving symlinks/modes:

```sh
# Set ZIP to the exact archive you downloaded; do not overwrite an existing app.
ZIP="$HOME/Downloads/<exact-release-filename>.zip"
test ! -e "$HOME/Applications/Hermes.app" &&
  mkdir -p "$HOME/Applications" &&
  /usr/bin/ditto -x -k "$ZIP" "$HOME/Applications"
open "$HOME/Applications/Hermes.app"
```

For an unidentified-developer warning, after verifying origin/checksum, use the
app-specific **System Settings → Privacy & Security → Open Anyway** only if you
trust this build. Never disable Gatekeeper/SIP or broadly remove quarantine.
For “damaged”, `Killed: 9`, a crash or no app-specific exception: stop and diagnose:

```sh
codesign --verify --deep --strict --verbose=2 "$HOME/Applications/Hermes.app"
spctl --assess --type execute -vv "$HOME/Applications/Hermes.app"
```

These checks may legitimately reject an unsigned app. CI does not certify that result.

### Windows

Check SHA256 using `Get-FileHash -Algorithm SHA256 <zip>`. Extract the **entire** ZIP
into a new directory, then run `Hermes/Hermes.exe`; do not copy the EXE alone.
Do not disable Defender or SmartScreen. If a policy blocks the untrusted publisher,
stop and use a properly signed distribution rather than bypassing organizational policy.

### Linux

Check `sha256sum <archive.tar.gz>`, extract into a new directory with
`tar -xzf <archive.tar.gz>`, then run `./Hermes/Hermes` in a graphical desktop session.
System Electron/Chromium GUI libraries are required; see the upstream Electron Linux
requirements. This tarball is not a distro package and does not register a launcher.
Linux CI alone uses `--no-sandbox` because hosted/headless environments may restrict
Chromium user namespaces. That flag is **not** baked into the app or normal run advice.

For all platforms choose the existing-remote route and configure your HTTPS backend.
Client replacement does not update your server. Quit the app and keep the previous
version for rollback. Do not delete external client state. The upstream in-app updater
expects a source/agent install and is not the update mechanism for these standalone builds.

## Reproduce

Inspect `upstream.json` for the exact source SHA, source version, build revision and Node
version. Use a **fresh checkout** of this build repo on each native OS with Git, that
Node/npm version, Python >=3.11 and native build tools where dependencies require them.
No Hermes agent installation is needed. This script refuses to reuse an existing `.work/src`.

```sh
python3 -m unittest discover -s tests -v
python3 scripts/build.py --run
```

The build fetches the exact public upstream commit and checks it out without CRLF
conversion, runs lockfile npm ci with lifecycle scripts initially disabled, explicitly
installs Electron, then uses upstream build/staging/builder hooks. Native dependencies
are never replaced by stubs. Unknown test failures block the distribution.

The full upstream UI/Electron suite runs on Linux; targeted first-run/native-packaging
tests and typechecking run on every host. For the initial pinned commit, two precisely
named pre-existing failures may be reported as explicit exceptions: a native-button-title
style violation, and an SSH control-socket path assertion under a long isolated HOME.
The suite is **not called green** when either fails; raw JSON/logs and the exception
classification are retained. Exceptions apply only to that exact commit.

Binary architecture checks, ASAR file/integrity/secret-pattern scans, compiled-JS syntax,
archive CRC/roundtrip checks and a real launch of the **extracted distribution** are gates.
Pattern scanning is not a universal secrets/malware guarantee. No live credentials are read.
Native Mac/Windows GUI/GPU/peripheral integration, microphone/screen/camera permissions,
Keychain and quarantined download acceptance still need user-machine testing.

## CI, releases and updates

Run **Actions → Build standalone desktops → Run workflow**. Standard public-repository
runners are used, never paid larger runners. Builds have only `contents: read` and receive
no signing/API secrets. Artifacts expire after 7 days; Releases are the durable downloads.
Actions are pinned by commit. PRs run tooling tests, not credentialed publish operations.

Update `upstream.json` deliberately (commit + matching upstream version; increment build
revision for another build of the same app version). Update the workflow Node version
alongside the pin if needed. Re-run all targets before publishing. Build versions append
the revision: upstream `0.17.0`, revision `1` becomes distribution `0.17.0.1`.
No automatic tracking of unreviewed upstream `main`; no in-app update feed is configured.

After the build succeeds, run **Actions → Publish verified release**, supplying the
numeric build run ID. This verifies the successful main-branch run, all four manifests
and local checksums, creates a draft, checks uploaded asset sizes and GitHub SHA256
digests, and only then publishes the preview. A failed publication remains a draft,
never a partial public release. Existing versions/assets are never overwritten.

The Homebrew tap has a separate daily/manual sync: generate the cask from that public
release manifest, audit/fetch/install it on Apple Silicon and Intel, then commit only
the tested cask. No cross-repository PAT or Apple credential is required.

Publication and tap synchronization are separate from the untrusted dependency/build
process. Never overwrite an existing release asset/version. Keep the full build run URL
and pin in the release manifest. The tap must reference immutable URLs and SHA256 values,
with no install hooks invoking an agent installer or disabling OS security.
