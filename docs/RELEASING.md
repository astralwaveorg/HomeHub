# Homehub release engineering

## Scope and development contract

This is an independent Git repository; the initial `main` baseline is `e264e7c`, tagged `v1.0.0`. Current source release is **1.1.0**. Work on branches, run checks, review the diff, then let the repository owner commit/merge. Never commit live data, `.env`, credentials, passwords, screenshots or backups. No remote repository, push, NAS deployment or password operation is performed by merely adding these scripts.

From the repository root:

```sh
python3 scripts/check.py
# unittest (application + scripts), node --check for static JS, JSON catalog checks
```

Python 3.12+ and Node.js are local/CI prerequisites; application runtime is stdlib-only. Keep `VERSION` synchronized with the intended release. Package only reviewed, **clean committed** source. Untracked files also fail the clean gate. Output must be outside this repository. Packaging uses `git archive` of HEAD, fixed selected paths and Git timestamps, so the same commit produces identical tar bytes and SHA256. No working-tree or live-data content is archived. Tar is deliberately uncompressed for reproducibility and easy inspection.

## Today: operator-driven release

First the owner commits the reviewed changes (scripts do **not** commit). Then:

```sh
python3 scripts/release.py package --output /tmp/homehub-release-1.1.0
# Prints /tmp/homehub-release-1.1.0/homehub-1.1.0-<fullcommit>.tar.json
python3 scripts/release.py deploy \
  --manifest /tmp/homehub-release-1.1.0/homehub-1.1.0-<fullcommit>.tar.json \
  --dry-run
```

Replace `<fullcommit>` with the printed filename. Dry-run verifies local artifact integrity and prints scope; it does not connect, build or probe NAS. To apply the specifically approved one-time publication of existing **seed-managed** entries:

```sh
python3 scripts/release.py deploy \
  --manifest /tmp/homehub-release-1.1.0/homehub-1.1.0-<fullcommit>.tar.json \
  --publish-existing
```

**Later releases omit `--publish-existing`.** Their `sync-catalog` adds missing defaults and respects existing edits, visibility, names and catalog tombstones. The one-time option is an explicit content change, not a generic deployment default. Catalog migration must stay backward-compatible with retained older images. A code rollback does not undo a successful catalog change.

Optional arguments: `--host root@10.0.1.2` (default), `--public-url https://homehub.stream:8443` (default). The public origin is checked from NAS, with normal certificate verification; DNS/hairpin routing must therefore work there. HTTP/IP addresses are not a replacement for production HTTPS health.

### NAS prerequisites (verify before first deployment)

- Authorized SSH access, known_hosts enrolled from a trusted source; existing identity/agent available. Client always uses BatchMode, StrictHostKeyChecking=yes, ConnectTimeout=10. Never auto-accept changed keys, send passwords or expose a public SSH port.
- NAS `python3` with stdlib `sqlite3`, `fcntl`, TLS CA certificates; Docker CLI and Compose v2 supporting `--pull never`. Executor runs on the NAS host, not inside an unrelated app.
- Existing `/vol1/1000/docker/homehub` is the actual Compose root, existing container `homehub`, existing `data/homehub.db`; script intentionally refuses first-install/bootstrap. Root and scoped paths must not be symlinks. Adequate space for build context, retained image and private backups. Existing data ownership 1000:1000 stays unchanged.
- NAS can build the digest-pinned Python base image (cached or registry reachable). No registry image publishing is required; release images remain local.
- No concurrent manual changes to this service during deployment. Script locks serialize scripted deploys/rollbacks, not manual 1Panel operations or arbitrary shell commands. Existing tools must continue using this same project root.

## Deployment transaction and isolation

1. Verify archive SHA256 locally, stream only artifact+manifest over strict SSH. A small bootstrap stores immutable content under `/vol1/1000/docker/homehub/releases/<fullcommit>/`; executor is loaded from that checked archive. Nothing is downloaded from NAS.
2. Acquire nonblocking flock at the fixed project's `.release.lock`. Validate all runtime paths and archive regular-file types. Never extract arbitrary tar paths. Build the allowlisted source **before replacing live files** into `homehub-portal:<version>-<fullcommit>`. Existing immutable tags must have matching revision labels; they are not rebuilt.
3. Make a private `backups/<UTCtimestamp>-<random8>/`: online SQLite backup (including WAL state via SQLite API), media copy, allowlisted old source and filtered runtime metadata (image ID/tag only). The current container image ID is retained under a dedicated tag, including the original v1.0.0 image when no old release manifest exists. Backups contain sensitive data; stay on NAS with restrictive permissions, never uploaded as Actions artifacts. Initial-password and `.env` are neither read nor copied. The online database backup and media copy are not a single cross-file snapshot; avoid concurrent music uploads while deploying.
4. Briefly stop **only homehub**, replace individual allowlisted runtime files with atomic renames, and set installed Compose's fallback image to the immutable release. `HOMEHUB_IMAGE` can override this at invocation. The default in the deployed Compose file is pinned so normal 1Panel/Compose restarts do not revert to a mutable old image. Existing `.env` must not define an unintended `HOMEHUB_IMAGE` override; no script reads or rewrites it. Root/data/project stay in place.
5. `docker compose --project-directory <fixed-root> -f <fixed-compose> up -d --no-deps --no-build --pull never homehub`. No OpenResty/router/reverse-proxy/other-container modification, no moving the stack or data, no Compose down, no image prune.
6. Check backend `/api/health` plus public `/api/health`, `/`, `/nav`; the release's health version must match VERSION. Execute `python /app/app.py sync-catalog` (optional one-time flag), then recheck. Record `releases/current.json` only on success.
7. Any replacement/start/health/catalog failure attempts automatic **code/image-only** rollback from the freshly retained source+actual image. Failure exits nonzero even if rollback succeeds. If rollback itself fails, retain all backups/artifacts and inspect the service locally; scripts never broaden scope or delete data to "repair" it.

Runtime allowlist is explicit: application/catalog modules, seed, ambient audio, VERSION, Dockerfile, Compose, dockerignore, static assets, README/CHANGELOG and Markdown maintenance docs. Updates cannot replace data, initial password, `.env`, sibling apps or arbitrary host paths. No broad deletion/cleanup is performed. Old unused static files may remain on the host after rollback; the retained image, not host source mounts, defines the actual running code. Image/container recreation retains the same data mount, port `127.0.0.1:48080`, read-only root, UID/GID 1000, resource limits and security options.

The transaction spans source files and a Docker recreation; it is not a filesystem-wide atomic swap. Individual files are atomically renamed while homehub is stopped, and failures restore previous source/image. A host power loss can interrupt this; retained images/backups provide manual recovery. Docker build/exec/Compose output is withheld to avoid accidental secret diagnostics; the client prints phase/backup/result metadata, never credentials. Diagnose unexpected tool failures in an authorized NAS terminal without copying full environment/inspect output into logs.

## Code rollback (data retained)

Use the exact backup basename printed by deploy:

```sh
python3 scripts/release.py rollback --backup 20260101T010101Z-12345678 --dry-run
python3 scripts/release.py rollback --backup 20260101T010101Z-12345678
```

Manual rollback first saves the current state to a new NAS-only backup. It verifies retained image ID, restores allowlisted source and Compose image selection, recreates only homehub and checks backend/public health. Works for original v1.0.0 even though that source has no VERSION/catalog module/release manifest. **It never restores SQLite or media**, so administrator hashes, sessions, settings, custom links and post-deploy data remain intact. Do not prune retained images/backups until the rollback window ends.

**Data rollback is a separate destructive operation and is intentionally not automated.** It loses newer changes/sessions and can regress passwords; require explicit authorization, take a new backup, stop only this service and plan DB+WAL/media consistency. Ordinary code failure is not permission to restore an old database. Forward schema migrations must be reviewed for compatibility with the intended rollback image.

## Future private GitHub and one-click deployment

No GitHub remote/repository is created or pushed by this task. When the owner chooses to publish:

- Prefer a trusted private repository. Review members, branch protection and CI; protect `main` and require Checks. CI push/PR checks run on independent `ubuntu-latest` runners without NAS credentials.
- Manually register a dedicated self-hosted runner labelled **homehub-deploy**, on the trusted private network/VPN with NAS access, Python 3.12+ and Node. Do not expose NAS SSH to the Internet. Do not register this privileged runner for untrusted repositories or run untrusted PR code on it. A persistent privileged runner is a trust boundary, not a sandbox.
- Create protected GitHub environment **homehub-production**, restrict deployment branches to main and require authorized reviewer approval. Ensure your GitHub plan actually supports the required environment protection; otherwise leave deployment disabled.
- Provision known_hosts from trusted host identity and the narrowest available NAS deployment SSH identity through the runner administrator, not through repository files. Docker access is effectively root; a genuinely restricted account needs a separately reviewed forced-command/privilege boundary. Do not call an unrestricted root key "least privilege". No credentials are stored in workflow YAML, artifacts or logs.
- Optional repo variable `HOMEHUB_SSH_HOST`; explicitly set repo variable **HOMEHUB_DEPLOY_ENABLED=true** only after prerequisites and authorization. Until then the manual job is skipped. Workflow dispatch on main + protected environment + runner label is required; there is no push/PR deployment trigger. Enable the one-time catalog option only when separately approved.
- Audit third-party Actions and consider pinning full reviewed action SHAs before enabling remote automation. Checkout credentials are not persisted; token permission is contents:read. No SSH action/plugin or registry credential is needed.

The manual workflow checks/packages the clean checked-out commit outside the checkout and invokes the same client. GitHub concurrency and the NAS lock prevent overlapping scripted deployments. No separate deployment server or public NAS ingress is needed.

## Test coverage and boundaries

`python3 -m unittest discover -s scripts -v` exercises clean temporary Git archive reproducibility, SHA256 corruption rejection, path/symlink exclusions, image pinning, original-image rollback retaining live data and auto rollback on mocked health failure. Temporary fixture repositories commit only generated test data; production/repository commits are not performed. `python3 scripts/check.py` also runs application tests and JavaScript syntax checks.

These local tests do **not** assert a real NAS Docker/Compose/SSH deployment succeeded. First authorized NAS deployment must verify prerequisites and observe real backend/public health. Keep the printed rollback backup until the release is accepted.
