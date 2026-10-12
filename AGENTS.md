# Homehub development rules

## Scope and data safety

- This directory is an independent Git repository for the portal only. Parent workspace asset notes are not part of this repository.
- Never commit/download NAS data, credentials, initial passwords, cookies, full configurations or deployment backups. Tests generate credentials only in temporary directories.
- App content lives in SQLite on NAS. Code releases must preserve passwords, active sessions, settings, custom URLs/names, private flags and intentional deletions.
- Keep catalog_key immutable. New default services belong in seed.json and need verified URLs/launch behavior. Normal sync is additive and owner-preserving; --publish-existing needs explicit owner approval and is not a normal-release default.
- fnOS token-protected apps use launch_mode=fnos with desktop instructions. Never fabricate launch tickets, bypass authentication, or create clickable database/data ports.

## Development workflow

1. Work on a branch; inspect existing code before editing.
2. Add/adjust regression tests for behavior changes. Frontend is plain HTML/CSS/JS; backend and release scripts use Python stdlib. Do not add production CDN/build dependencies casually.
3. Run `python3 scripts/check.py` and `git diff --check` from this repository root.
4. Optional frontend formatting: `npx --yes prettier@3.6.2 --write static/app.js static/styles.css static/index.html`.
5. Review diff, then commit. Bump VERSION and update CHANGELOG.md for a release. Check Git author identity before first remote push; do not silently change global Git configuration.
6. Package only clean committed source using scripts/release.py. Never deploy uncommitted ad-hoc files.

## Deployment boundaries

- Deployments need a direct owner request. Use the checked release tool, SHA256 manifest and strict SSH host-key checking.
- Existing NAS stack/data path and loopback-only app port stay unchanged. Only homehub may be rebuilt/restarted; do not modify fnOS trim_nginx, OpenResty sites, router, DNS or other containers as incidental cleanup.
- Backups remain on NAS. Ordinary rollback restores code/image, never live DB/media. Destructive data recovery requires separate approval.
- GitHub CI can run on hosted runners. NAS deployment is opt-in/manual, trusted-main-only, self-hosted, protected-environment; never execute deployment credentials for untrusted PRs or open NAS SSH to the Internet for convenience.
- Do not create/push a remote repository without owner approval.
