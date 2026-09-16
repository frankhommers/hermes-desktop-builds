# Mainstream client installation — acceptance contract

This is a replacement direction for the community Homebrew updater, not another update provider.

## User outcome

- Install once on macOS, then use the **official Hermes Desktop update flow**.
- No recurring terminal commands and no dependence on this repository's release cadence for later updates.
- Local runtime files are acceptable; a local Agent/server must not start automatically.
- Frank explicitly accepts **no local autostart**, not a hard prohibition against intentional local use or lost/changed routing settings. Persist and validate supported remote-primary settings; do not add a custom execution-policy fork.
- Official updates may install/repair their normal `.[all]` Python dependencies. Minimize initial extras, but do not promise a permanently base-only runtime.
- Remote backend remains authoritative for conversations and work.
- Minimize ancillary Python extras, bundled skills, browser tooling, local service registration, and first-run provider setup.
- Do not replace the official updater with Homebrew, Sparkle, electron-updater, or a custom updater feed.

## Boundaries

- Do not modify or restart the user's VPS backend.
- Do not modify unrelated profiles, credentials, or existing Mac applications during discovery.
- Preserve rollback and old installs; detect existing application/install ownership before migration.
- No false `.git` directory, disabling Gatekeeper, post-signing ASAR edits, or token embedded in scripts.
- Packaging an initial installer is permitted; future official updates must not need community patches reapplied.

## Acceptance tests before delivery

1. Native Mac install finishes with official tracked source and usable update prerequisites.
2. No local `serve`, `dashboard`, agent loop, messaging gateway, or registered autostart service is introduced by installation.
3. App starts in remote setup/remote route; normal roster polling does not wake the local runtime.
4. An unavailable or authentication-failed remote does not fall back to a local server.
5. App's client-specific update check uses official upstream, not the Homebrew stamp/cask.
6. Client-specific apply performs the real official handoff, obtains a newer official revision, replaces the running app bundle, and relaunches it.
7. After update/relaunch, remote routing survives and local backend remains off.
8. Installed app identity, source revision, update receipt, process evidence, and test limitations are recorded. Mock/DI tests do not count as native end-to-end proof.
9. The installed application directory contains no extra launcher-visible rollback `.app`. Retain the original bundle with unchanged bytes/signature in a private `.noindex` backup outside application discovery.
10. Seed the native OS launcher with the old installation before migration, then verify NSWorkspace resolves the installed bundle afterward without the test repairing registration. This is not proof of third-party launcher/Dock cache behavior. Never reset their global databases.
11. Inject a post-swap verification failure on native macOS with real signed bundles; require original bytes and signature restored, with staged new data retained. Refuse cross-filesystem atomic-backup moves before changing the app; do not substitute a lossy copy/delete fallback.

## Initial evidence

- Current inspected official source: `f13a87e610611ce6d9fd82bff8c2d2a642312183`.
- Official docs: <https://hermes-agent.nousresearch.com/docs/user-guide/desktop#updating>.
- Executed `scripts/install.sh --manifest --include-desktop --skip-setup --skip-browser --skip-computer-use --no-skills --non-interactive` with isolated HOME/HERMES_HOME on Linux: exit 0. Stages: prerequisites, repository, venv, python-deps, node-deps, path, config, setup, gateway, desktop, complete. The manifest invocation created no files in that isolated HOME. This only validates discovery; no installation or Mac test was performed by this invocation.
- Two independent read-only audits cover minimum official update prerequisites and no-local-backend lifecycle. Their findings gate implementation; no unsupported setting is presumed to exist.
