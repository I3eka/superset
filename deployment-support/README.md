# Apache Superset native AI deployment

This deployment combines official Apache Superset `master` at
`db556ec66eb9c354ea6c33b8df8c1161f49a7d9a` with the preserved Native AI
[PR 42805](https://github.com/apache/superset/pull/42805) at
`0e4a15cca1c5be2b7e68d609a9062f5e51193024`, local Dashboard builder changes,
and the anonymous Home navigation fix from
[PR 44456](https://github.com/apache/superset/pull/44456).

The integration also merges the current heads of the owner's open PRs:
43132 (SQL policy), 43133 (authoring; depends on 43132), 43134 (telemetry),
43135 (run completion), 43136 (identifier search), 43237 (structured output),
and 43278 (SQL Lab database recovery). The six AI PRs depend on 42805.
Published PR branches are unchanged; this is a local deployment integration.

Integrated PR heads (2026-09-20):

| PR | Head | Prerequisite |
| --- | --- | --- |
| 43132 | `9985e7b343` | 42805 |
| 43133 | `4ba174f3e2` | 42805, 43132 |
| 43134 | `96f6569f24` | 42805 |
| 43135 | `05a43720b5` | 42805 |
| 43136 | `8d001ed094` | 42805 |
| 43237 | `9f4a8bd424` | 42805 |
| 43278 | `e23dce755a` | independent |
| 44456 | `07c7697b45` | independent |

The only Git checkout is the project root (`..`), branch `ai-integration`.
The deployed source is tagged `deployment/20260920-native-ai`. Local pre-merge
changes are preserved in `local/ai-preserved-20260920`, commit
`ff9dab26a8935ccf427843c2bd9fae68fb4cf736`; the duplicate source folders and
source archives have been consolidated into Git.
Native AI remains experimental and is not part of official `master`.

## Local branches

Run `git switch master` for official upstream, `git switch ai-integration` for
the combined AI version, or `git switch <branch>` for an individual PR:

| PR | Local branch (tracks the same branch in `fork`) |
| --- | --- |
| 43132 | `native-ai-database-aware-sql-policy` |
| 43133 | `native-ai-authoring-tools` |
| 43134 | `native-ai-tool-error-telemetry` |
| 43135 | `native-ai-step-limit-error` |
| 43136 | `native-ai-search-identifier-words` |
| 43237 | `native-ai-valid-tool-display` |
| 43278 | `fix-sqllab-stale-database` |
| 44456 | `fix-home-anonymous-navigation` |

`master` tracks `origin/master` (Apache); pushes default to `fork` (I3eka).
At consolidation on 2026-09-20, official `master` is `4511c1381930`; the
deployed integration is intentionally left at the verified revision below.
`base/native-ai-pr-42805` preserves the AI prerequisite. `archive/20260813/*`
and `archive/reflog-20260920/*` preserve historical work. Unvalidated feedback
API work is isolated in `local/wip-ai-feedback-20260920`, not deployed.
`archive/legacy-vambery` preserves the retired deployment's non-secret files.
`local/deployment-support` snapshots the current configuration and harness code
under `deployment-support/`, separately from the live untracked `deploy/` path;
credentials, databases, benchmark results and private exports are not committed.

The running container uses an immutable image, not a source bind mount.
Switching Git branches does not change the running application. `deploy/`
and private artifacts are excluded locally through `.git/info/exclude` so
configuration, credentials and backups remain present on every branch.
Do not run `git clean -fdx` in this repository: it would delete that local data.
The local `Dockerfile.dockerignore` symlink and `Dockerfile.master.dockerignore`
exclude deployment credentials, backups and artifacts from image build contexts.

## Build and deployment

There is no third-party chat extension and no Vambery `.supx` mount. The two
authoring adapters run inside the PR's existing agent registry and reuse
Superset's `generate_chart` and `generate_dashboard` MCP implementations in the
same authenticated request context.

The shipped `Assistant` and `Analyst` profiles remain read-only. The explicitly
selected `Dashboard builder` profile can preview or save charts and assemble
saved chart IDs into a draft dashboard. The reused MCP functions enforce the
current Superset user's chart/dashboard permissions and dataset access.

The included config bounds tool results, history, steps, and output tokens for
the configured Qwen model's 24,576-token context window. These are documented
upstream configuration settings, not replacement assistant code.

Native AI runs are exported to the self-hosted Langfuse endpoint configured in
`.env`. Each run contains nested model-generation and tool observations. This
deployment intentionally disables content redaction, so prompts, answers, SQL,
and bounded tool output are visible in Langfuse for answer-quality debugging.

Build frontend assets and the image from the same integration checkout
(Node 24.16+, npm 11.13+). CPU affinity bounds the frontend build's worker count:

```bash
cd ..
git switch ai-integration
(
  cd superset-frontend
  CYPRESS_INSTALL_BINARY=0 npm ci --no-audit --no-fund
  taskset -c 0-3 npm run build -- --parallelism 2
)
docker buildx build --load --target lean --build-arg DEV_MODE=true \
  -t local/superset-native-ai:master-db556ec6-base .
docker buildx build --load \
  --build-context compiled-static="$PWD/superset/static" \
  --build-arg SOURCE_REVISION="$(git rev-parse HEAD)" \
  -f deploy/Dockerfile.master \
  -t local/superset-native-ai:master-db556ec6-native-ai .
cd deploy
```

Make sure `config/` is traversable by the container user after copying it:

```bash
chmod 0755 config
```

The compose stack reuses the existing PostgreSQL, Redis and Superset home
volumes. Back up metadata before an upgrade and validate migrations against a
copy first. `upgrade_test_config.py` selects the isolated
`superset_upgrade_20260920` database and in-process caches. The local no-op
migrations `8e41b6d09a72` and `f962c0a48bd1` join the upstream, deployed AI,
and PR migration heads without dropping AI data. The deployed AI migration's
original parent is preserved so upstream migrations cannot be skipped.

After backup and validation, stop only the web service, migrate, initialize
permissions, and recreate it. Do not run the example/admin initialization script:

```bash
docker compose stop superset
docker compose run --rm --no-deps superset superset db upgrade
docker compose run --rm --no-deps superset superset init
docker compose up -d --no-deps superset
```

The two smoke scripts validate the model protocol and then the authenticated
Superset API, SSE stream, agent tool loop, stored transcript, and matching
Langfuse trace:

```bash
docker run --rm --env-file .env \
  -v "$PWD/smoke_vllm.py:/tmp/smoke_vllm.py:ro" \
  --entrypoint python local/superset-native-ai:master-db556ec6-native-ai \
  /tmp/smoke_vllm.py

docker compose run --rm --no-deps \
  -v "$PWD/smoke_native_ai_api.py:/tmp/smoke_native_ai_api.py:ro" \
  superset python /tmp/smoke_native_ai_api.py
```

The upgrade backup is in `backups/20260920-upstream.VDY8Cy/` (private directory):
deployment configuration, metadata dumps, and test results. Original source
and historical PR branches are preserved in this repository's Git history.
The previous image is retained as
`local/superset-native-ai:rollback-before-home-44456`.

After upstream migrations, rollback requires restoring the pre-cutover metadata
dump while the web service is stopped, then restoring the previous compose
configuration/image. Switching only the image is not a safe database rollback.
The original `Dockerfile` is retained for the previous deployment.

## Verified deployment on 2026-09-20

- Running source: `72261e374b7cc3536887556378bcb6c5bd303e13`.
- Running image: `local/superset-native-ai:master-db556ec6-native-ai`,
  SHA256 `8ed003c2ba062dc63809eb3a24fe23a0eba877fa1452eb596ce3cd88c4dd822c`.
- Metadata head: `f962c0a48bd1`; migrations and permission initialization passed.
- 2767 backend tests and 296 frontend tests passed; one frontend test skipped.
  An additional 52 orchestration/DAO tests passed with PyJWT 2.14.0.
  Changed-file checks passed, including mypy, frontend types, ruff and pylint.
- Post-migration fingerprints match pre-cutover content: 24 dashboards,
  223 charts, 35 datasets, 3 connections, 5 AI threads and 18 messages.
  Deployment credentials and both local configuration files are unchanged.
- Browser login and anonymous-logo redirect passed. Dashboard 42 rendered
  seven charts, all seven data responses HTTP 200, in 8.77 seconds in this check.
- AI profiles and saved conversations are accessible. Successful live inference
  remains unverified: the configured model endpoint returns HTTP 401, including
  a direct authenticated `/models` request outside Superset. The credentials
  file matches the pre-upgrade backup. Do not replace it with unrelated keys.
- Langfuse trace verification is also blocked: the configured service on
  `10.48.140.17:3000` refuses connections. No Langfuse containers were present.
- The one temporary AI validation conversation was removed through its exact
  UUID; final fingerprints again match pre-cutover content. The isolated
  verification container is stopped, and its metadata clone is retained.

`verify_upstream_readonly.py` and `verify_upstream_ui.cjs` reproduce API/browser
checks. The latter uses Node's `--env-file=.env` when run from this directory.
The fresh rollback dump is `backups/20260920-upstream.VDY8Cy/metadata-cutover.dump`;
the final content comparison is saved beside it as `cutover-final.tsv`.
