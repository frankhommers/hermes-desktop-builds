# Hermes Desktop Builds

Community builds of the **real Nous Research Hermes Electron Desktop**, with a small,
explicit UI patch applied before building and signing.
This is a build/distribution repository, not a Hermes fork, website wrapper, or the
Tauri `Hermes-Setup` agent bootstrap installer. Not an official Nous Research release.

## Distribution status

**Release and tag 0.17.0.1 have been withdrawn. Do not use its Mac ZIPs.** They lack CodeResources seals while the Electron
executables retain signatures, producing `code has no resources but signature indicates
they must be present`. The earlier native-start/Brew checks did not detect this defect.

[Release 0.17.0.3](https://github.com/frankhommers/hermes-desktop-builds/releases/tag/v0.17.0.3)
is the first automatically published release with the small remote-client UI patch.
Its [four-platform native build](https://github.com/frankhommers/hermes-desktop-builds/actions/runs/33984384497)
passed the distribution gates, and the separate [automatic publication](https://github.com/frankhommers/hermes-desktop-builds/actions/runs/33985138041)
verified its artifacts before publishing. Both Mac architectures passed final-bundle and
extracted-ZIP signature checks, ASAR integrity, negative resource/seal tests, and real
direct-remote-first-run/native-PTY smoke tests. All targets built the same verified patched tree.

For the current version, use [Latest release](https://github.com/frankhommers/hermes-desktop-builds/releases/latest)
and the [tap](https://github.com/frankhommers/homebrew-tap/blob/main/casks/hermes-desktop.rb).
The tap publishes an update only after actual Homebrew installation and deep/strict
`codesign` verification on macOS 15 Apple Silicon and Intel.

Upstream has separate version domains inside one source tag. For the current pin,
`v2026.9.11` is the calendar release tag, Hermes Agent/backend is `0.21.2`, and the
Electron Desktop package is `0.17.2`. This repository appends its immutable packaging
revision, so its first build from that Desktop source is `0.17.2.1`. Those values are
not expected to be numerically equal; the exact upstream commit proves which versions
belong together.

**Gatekeeper still rejects the quarantined ad-hoc publisher.** These are valid code seals,
not Developer ID signing or Apple notarization. Raw per-target evidence and any narrowly
commit/platform/test/error-bound fixture exception accompany the release; no failing suite
is labelled green.

## Scope

Native CI lanes: macOS Apple Silicon (`arm64`), macOS Intel (`x64`), Windows x64,
and Linux x64. Each build packages Electron, the real UI and real native modules.
No Python runtime, agent checkout, venv, credentials or personal backend URL is bundled.
No global installs, signing credentials, sudo or system configuration changes are used.
All caches, source and test homes live in `.work/`; outputs live in `out/`.
Linux CI uses one private short directory under `RUNNER_TEMP` for Chromium socket
files, because its Unix-domain socket paths cannot exceed the kernel length limit.

**Mac builds are ad-hoc signed, not Apple Developer ID signed or notarized.** Windows
builds have no Authenticode signature. A valid Mac signature seals the app contents;
it does not establish an Apple-trusted publisher. Native CI separately records the
Gatekeeper rejection of the quarantined ad-hoc app and never calls that acceptance.
Do not disable OS security or strip quarantine automatically.

## Remote use: important

New patched builds open **Connect to existing Hermes** directly on a clean first start.
They hide the local installation offer (including the alternate install-command screen),
and omit the unused local gateway icons while the active gateway is remote. A genuine
local failure is not hidden as an idle connection. The settings registry is unchanged.
The older 0.17.0.2 release predates this UI patch; patched releases start at 0.17.0.3.

The remote form does not start the local installer. A running remote Hermes
`serve`/dashboard backend is required; no local Node, Python or Hermes CLI is required.

This is **not a hard-locked remote-only fork**: local runtime internals remain intact;
the patch changes presentation, not the backend's installation/discovery capabilities.
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

The `darwin-<arch>-adhoc.zip` release ZIP contains a complete, ad-hoc signed `Hermes.app`.
Use the archive for your CPU. Signing happens during the build, never in cask hooks.
macOS 12+ is the initial binary metadata floor, not a tested compatibility matrix.

Install via the verified cask in [frankhommers/tap](https://github.com/frankhommers/homebrew-tap):

```sh
brew install --cask frankhommers/tap/hermes-desktop
# Later:
brew update
brew upgrade --cask frankhommers/tap/hermes-desktop
```

Homebrew installs the app only. It does not install a local Hermes agent or bypass
Gatekeeper. Starting with community build `0.17.2.1`, the packaged Mac app identifies
itself as Homebrew-managed: its in-app update check reads only this tap's cask version,
**Update now** runs fixed argument arrays equivalent to the two commands above (never a
shell string), then re-reads the bundle at the running app path and requires a newer valid
distribution stamp at least equal to the checked cask target before it reports success or
restarts Hermes Desktop. It does not update or modify the connected remote backend. The
first installation of an updater-capable build must still be performed through Homebrew.

For manual installation, verify the ZIP's SHA256 against `SHA256SUMS`
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
# Use the actual installation path: Brew normally uses /Applications.
APP="/Applications/Hermes.app"
codesign --verify --deep --strict --verbose=2 "$APP"
spctl --assess --type execute -vv "$APP"
```

The `codesign` check MUST pass for the corrected Mac release. `spctl` still rejects
the unnotarized ad-hoc publisher; that is distinct from a damaged bundle. No local
re-signing is necessary. A fully Apple-trusted distribution requires Developer ID
signing and notarization credentials in a separate credentialed release lane.

### Windows

Check SHA256 using `Get-FileHash -Algorithm SHA256 <zip>`. Extract the **entire** ZIP
into a new directory, then run `Hermes/Hermes.exe`; do not copy the EXE alone.
Do not disable Defender or SmartScreen. If a policy blocks the untrusted publisher,
stop and use a properly signed distribution rather than bypassing organizational policy.

### Linux

Check `sha256sum <archive.tar.gz>`, extract into a new directory with
`tar -xzf <archive.tar.gz>`, then run `./Hermes/Hermes` in a graphical desktop session.
System Electron/Chromium GUI libraries are required; see the upstream Electron Linux
requirements. The bundled Linux window-enumeration implementation additionally invokes
`xprop` and `xwininfo` against an X11 display; the startup/PTY smoke test does not exercise
that feature or certify native Wayland support. Across platforms, `get-windows` payloads
are inspected, but their actual window-enumeration/permission behavior is not covered by
the native PTY test. This tarball is not a distro package and does not register a launcher.
Linux CI launches the real Electron renderer with `--ozone-platform=headless` and
`--no-sandbox` because hosted/headless environments may restrict
Chromium user namespaces. That flag is **not** baked into the app or normal run advice.

For all platforms choose the existing-remote route and configure your HTTPS backend.
Updating or replacing a client does not update your server. Quit the app and keep the
previous version for rollback; do not delete external client state. The packaged Mac
community build uses the Homebrew updater described above. Other standalone packages
still require manual replacement; they never pretend a missing local Git checkout is an
app update source.

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
conversion, verifies and applies the checked-in UI/test patch, then runs lockfile npm ci
with lifecycle scripts initially disabled, explicitly
installs Electron, then uses upstream build/staging/builder hooks. Native dependencies
are never replaced by stubs. Unknown test failures block the distribution.

The full upstream UI/Electron suite runs on Linux; targeted first-run, updater,
SSH/storage, and native-packaging tests plus typechecking run on every host. The reviewed
long-HOME SSH failure is fixed in the patched product path with a short uid-scoped control
directory whose ownership, type and mode are checked before use. The two voice-preference
failures were broken spies; the corrected fixtures now inject the storage exceptions and
pass.

Windows may report one explicit cross-Darwin fixture exception: the exact pinned test
expects POSIX mode 0755 while Windows exposes 0666. It is accepted only for the exact
upstream commit, Windows platform, test path/title and reviewed `438 !== 493` assertion
signature. Actual Mac helper modes and native PTY execution are tested on both Mac lanes;
no native feature is faked or removed. Any other failure or changed signature blocks.
Temporary Git test directories use GIT_CEILING_DIRECTORIES so they cannot accidentally
discover or change the enclosing build repository.

Binary architecture checks, ASAR file/integrity/secret-pattern scans, compiled-JS syntax,
archive CRC/roundtrip checks and a real launch of the **extracted distribution** are gates.
On Mac, the original upstream beforePack hook still runs. A wrapper then ad-hoc signs
its final staged native files BEFORE electron-builder hashes them into ASAR. After all
license resources are copied, the pinned `@electron/osx-sign` signs the app inside-out,
using upstream entitlements and hardened runtime, without touching the already-hashed
native payload. Only Mach-O code/bundles are signed; data-file xattr signatures are avoided.
The final bundle and extracted ZIP must pass deep/strict codesign plus explicit unpacked
native-module verification. Deliberately tampered resources and a missing CodeResources
seal must be rejected, and restoring them must recover a byte/mode-identical valid bundle.
Only afterwards is quarantine added to the extracted CI copy for an honest Gatekeeper
assessment. No quarantine removal, security-policy change or local post-install repair.
Pattern scanning is not a universal secrets/malware guarantee. No live credentials are read.
Native Mac/Windows GUI/GPU/peripheral integration, microphone/screen/camera permissions,
Keychain and Finder/app-specific Open Anyway still need user-machine testing.

## CI, releases and updates

Run **Actions → Build standalone desktops → Run workflow**. Standard public-repository
runners are used, never paid larger runners. Builds have only `contents: read` and receive
no signing/API secrets. Artifacts expire after 7 days; Releases are the durable downloads.
Actions are pinned by commit. PRs run tooling tests, not credentialed publish operations.

The daily **Update official Hermes release** workflow follows published, non-prerelease
official releases of `NousResearch/hermes-agent`, not its moving `main` branch. It resolves
the tag to an exact commit, checks ancestry to prevent source downgrades, reads the Desktop
version at that commit, and updates only `upstream.json`. A write race stops the update.
An explicit dispatch then starts the native build at the expected build-repository commit.

The current source pin is newer than the latest official release available when this
automation was introduced. That older release is skipped, not installed as a downgrade.
Node/toolchain changes and patch conflicts still require a maintainer; the workflow never
auto-edits a patch, invents test exceptions, or blindly updates dependencies to make CI pass.
Build revisions are monotonic: upstream Desktop `0.17.2`, revision `1` becomes `0.17.2.1`.
Any exact-commit fixture exception does not carry forward to another upstream commit.
The packaged Mac build checks the public Homebrew cask for Desktop updates; it never uses
backend `0.21.x` as the Desktop version and never updates the remote server.

After a main-branch build succeeds, **Publish verified release** starts automatically via
`workflow_run`. Manual dispatch with the numeric run ID remains available for recovery.
The privilege boundary rejects forks, PR runs, other workflows, incomplete/failed runs,
missing native lanes and commits outside main history before trusting artifacts. It uses
the exact build revision's pin, patch set and validators, even if main advanced meanwhile.
This verifies the successful main-branch run, all four manifests
and local checksums, creates a draft, checks uploaded asset sizes and GitHub SHA256
digests, and only then publishes a normal release with its version number as the title
and explicitly marks it Latest. This controls GitHub presentation, not Apple/Windows
publisher trust or an assertion that all upstream tests pass. A failed pre-publication
validation remains a draft, never a partial public release. Existing versions/assets
are never overwritten. A delayed older build cannot become Latest over a newer version.

Patch hashes and the resulting source state are verified; patched sources are not described
as unmodified. The first-run UI is checked on each real native packaged application. Patch
conflicts, changed behavior, unknown test failures or invalid signatures fail closed: the
last published release and existing tap remain available. Review failed Actions runs;
GitHub failure notifications depend on the repository/user notification settings.

The Homebrew tap has a separate daily/manual sync: generate the cask from that public
release manifest, audit/fetch/install it on Apple Silicon and Intel, then commit only
the tested cask. No cross-repository PAT or Apple credential is required.

Publication and tap synchronization are separate from the untrusted dependency/build
process. Never overwrite an existing release asset/version. Keep the full build run URL
and pin in the release manifest. The tap must reference immutable URLs and SHA256 values,
with no install hooks invoking an agent installer or disabling OS security.
