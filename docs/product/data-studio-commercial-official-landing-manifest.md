# Data Studio Commercial Official Landing Manifest

This manifest defines the selective migration from the staging branch
`veadk-data-studio/integration/data-studio-commercial-p0` at
`082f209c476b26c4d5c891849532a7018a42a1f4` into the official repository history.
The staging branch is evidence-only input. It is not a base branch, merge source,
or tree to copy wholesale.

## Baseline and safety constraints

- Official target repository: `byaan-ai/byaan`
- Official fork used for publication: `marchpure/byaan`
- Official landing branch: `integration/data-studio-commercial-official-p0`
- Official base at landing start: `1e3410fd29c09eb2dd01f88d31650120dd1aeb84`
- Staging final SHA: `082f209c476b26c4d5c891849532a7018a42a1f4`
- Staging connector/modeling input `54c1f299800e0c3957b31f482e11fed21b75024f`
  is an ancestor of staging final.
- Staging dashboard/evaluation/sharing input
  `acd2749869626b66b146b88a03fe96fd75f44cc7` is an ancestor of staging final.
- Staging and official `origin/main` have unrelated histories; this landing must
  not use `--allow-unrelated-histories`.
- The branch `official-fork/integration/data-studio-commercial-p0` is a known
  wrong staging-tree push and must not be used as a base, merge source, PR head,
  or continued push target. It is intentionally left for later cleanup.

## Landing method

Each domain must be ported in small, reviewable batches after reading the
official implementation it touches. Changes should be behavior- and
contract-oriented, using path-scoped patches or manual reconciliation. The
landing must preserve official workflows and avoid copying the staging tree.

## Connector and modeling

Contracts to migrate:

- Governed source connection/resource model for local files, public web URLs,
  Feishu/Lark, Volcengine TOS, SQL databases, MongoDB, DynamoDB, and Databricks.
- Connector catalog with honest availability states. Connector/modeling readiness
  remains `PARTIAL`, not production-ready: `0 ready / 14 beta / 26 planned /
  0 blocked / 40 total`.
- Source resource snapshots with raw locators, parser metadata, lineage, sync
  status, retry/error state, and tombstone/delete behavior.
- Tabular projection review for CSV, Excel, JSON/JSONL, Parquet, Feishu
  Sheet/Base, and TOS objects, with projected dataset handoff.
- Source understanding run, evidence fragments, relationship/candidate review,
  semantic draft creation, immutable publish, reload, and MCP `query_metric`
  contracts.
- NoSQL guardrails: MongoDB and DynamoDB profile evidence may exist, but semantic
  candidates remain blocked until reviewed tabular projection is proven.
- OpenHuman-style provenance gate for semi-structured context extraction remains
  unverified; context rows stay beta until runtime metadata proves algorithm,
  config digest, source revision, confidence, evidence locator, provenance, and
  warnings.

Expected file areas:

- `server/models/source_connections.py`
- `server/models/source_resources.py`
- `server/models/source_snapshots.py`
- `server/models/source_understanding.py`
- `server/models/knowledge_resources.py`
- `server/models/semantic_models.py`
- `server/schemas/source_connections.py`
- `server/schemas/source_resources.py`
- `server/schemas/source_overview.py`
- `server/schemas/source_understanding.py`
- `server/routers/source_connections.py`
- `server/routers/source_resources.py`
- `server/routers/semantic_models.py`
- `server/services/connector_catalog.py`
- `server/services/analysis_artifacts.py`
- `server/services/assets.py`
- `server/services/connections.py`
- `server/services/database_operations.py`
- `server/services/folder_service.py`
- `server/mcp/tools.py`
- `server/mcp/tool_wrappers.py`
- `client/src/features/data-modeling/**`
- `client/src/pages/SourceDetailPage.tsx`
- `client/src/components/SourceConnectorImportPanel.tsx`
- `client/src/services/api.ts`
- Connector/modeling tests under `server/tests/` and browser/API smoke scripts
  under `client/scripts/`.

## Dashboard

Contracts to migrate:

- Structured dashboard assets for saved queries, semantic metrics, and context
  search.
- Explicit legacy dashboard handling so legacy HTML assets do not fall through to
  generic app errors.
- Lifecycle/status states for stale, partial, blocked, permission-denied,
  malformed, policy-blocked, and legacy assets.
- Safe review/notebook preview actions and legacy structured-action gating.
- Dashboard REST, service, repository, MCP, persistence, migration, security, and
  UI coverage.

Expected file areas:

- `server/models/dashboard.py`
- `server/repositories/dashboard.py`
- `server/routers/dashboard.py`
- `server/schemas/dashboard.py`
- `server/services/dashboard.py`
- `server/services/dashboard_cache_service.py`
- `server/services/dashboard_refresh_service.py`
- `client/src/features/dashboard/**`
- `client/src/components/DashboardFilterBar.tsx`
- `client/src/components/DashboardPreviewPanel.tsx`
- `client/src/components/home/SharedDashboardsSection.tsx`
- `client/src/services/dashboard.ts`
- `client/src/types/dashboard.ts`
- Dashboard-focused tests and smoke scripts.

## Evaluation

Contracts to migrate:

- Evaluation suite/case/version/run authoritative model.
- REST APIs for suite creation, case import/listing, draft version creation,
  publish, runner claim/heartbeat/complete/failure, comparison, feedback advisor,
  and promotion decision.
- MCP parity for evaluation workflows.
- Actionable empty-state and explicit demo fixture loading in the UI.
- Tenant isolation, scope protection, idempotency, resumability, redaction, and
  promotion blocking behavior.

Expected file areas:

- `server/models/evaluation.py`
- `server/repositories/evaluation.py`
- `server/routers/evaluation.py`
- `server/schemas/evaluation.py`
- `server/serializers/evaluation.py`
- `server/services/evaluation.py`
- `server/scripts/evaluation_mcp_parity_smoke.py`
- `server/scripts/evaluation_release_gate_8080.py`
- `server/scripts/seed_evaluation_smoke.py`
- `client/src/features/evaluation/**`
- `client/src/services/evaluation.ts`
- `client/src/types/evaluation.ts`
- Evaluation-focused tests and smoke scripts.

## Sharing

Contracts to migrate:

- Canonical sharing grants for notebooks, dashboards, folders, and compatible
  worker-backed sharing surfaces.
- Folder notebook and folder dashboard share/revoke evidence.
- Object authorization checks before read or grant operations.
- Self-hosted external sharing policy: worker-backed external sharing returns an
  explicit unavailable response in self-hosted mode.
- Redaction of secret, token, password, and verifier values in APIs, logs, audit
  events, and release gates.

Expected file areas:

- `server/auth/object_authorizer.py`
- `server/models/sharing.py`
- `server/routers/sharing.py`
- `server/serializers/sharing.py`
- `server/services/sharing.py`
- `server/routers/folders.py`
- `server/routers/exports.py`
- `server/services/folder_service.py`
- `client/src/components/ShareModal.tsx`
- `client/src/types/folder.ts`
- Sharing-focused tests and release gate scripts.

## Shared auth, tenant, MCP, and API wiring

Contracts to migrate:

- Scope constants and dependencies required by the new Data Studio routes.
- Tenant isolation across source resources, dashboards, evaluation, and sharing.
- MCP tool registration/wrappers for semantic model, dashboard, evaluation, and
  sharing read surfaces.
- App route registration in `server/main.py`.
- Client route/sidebar/API service integration.

Expected file areas:

- `server/auth/dependencies.py`
- `server/auth/scopes.py`
- `server/main.py`
- `server/models/__init__.py`
- `server/mcp/tools.py`
- `server/mcp/tool_wrappers.py`
- `client/src/App.tsx`
- `client/src/components/CollapsibleSidebar.tsx`
- `client/src/constants/scopes.ts`
- `client/src/services/api.ts`
- `client/src/stores/**`

## Migration plan

Official migrations must be rebuilt on top of the official Alembic chain, not
copied as a staging migration chain. The final chain must have one head and must
upgrade cleanly on fresh and existing SQLite and PostgreSQL databases.

Expected migration content:

- Source connections/resources/snapshots/knowledge/evidence/source-understanding
  tables and related indexes.
- Semantic model/version lineage tables.
- Governed dashboard asset/version/share/backfill tables or columns.
- Evaluation suite/case/version/run/artifact/advisor/promotion tables.
- Canonical sharing grant/evidence tables.
- Any compatibility bridge required by existing official tables.

Validation commands:

- `cd server && PYTHONPATH=..:tests uv run alembic heads`
- Fresh SQLite upgrade.
- Existing SQLite upgrade from the official base schema.
- Disposable PostgreSQL upgrade when a local PostgreSQL target is available.
- Migration-focused pytest coverage.

## Docker, package, and dependency policy

Dependencies must be merged at dependency level only:

- Add backend packages only when required by landed runtime/test behavior.
- Add frontend packages/scripts only when required by landed UI/smoke behavior.
- Do not replace `uv.lock` or `client/pnpm-lock.yaml` wholesale from staging.
- Preserve official Docker and self-hosted startup behavior while adding only
  necessary route/static/runtime support.

Expected file areas:

- `pyproject.toml`
- `uv.lock`
- `client/package.json`
- `client/pnpm-lock.yaml`
- `Dockerfile.self-hosted`
- `docker-compose.yml`
- `server/Dockerfile`
- `docker/self-hosted/**`

## Tests and validation gates

Each domain batch should include scoped tests before commit where practical.
Before handoff, rerun:

- Connector/modeling pytest gates.
- Dashboard pytest gates.
- Evaluation pytest gates.
- Sharing pytest gates.
- REST/MCP parity checks.
- Tenant isolation and permission tests.
- Alembic single-head and fresh/existing migration checks.
- Frontend typecheck/build and relevant lint.
- Browser/API smokes at 1440x900 and 390x844 for Connector/Modeling,
  Dashboard, Evaluation, and Sharing where scripts exist.
- `git diff --check`.
- Secret scan of the outgoing diff.

Staging test history is not evidence for the official branch.

## Explicit exclusions

The official landing must exclude:

- Any deletion or replacement of official `.github/workflows/**`.
- `client/tmp-data-modeling-screens*/` and other temporary screenshot folders.
- `docs/product/dashboard-browser-smoke/*.png` and other historical smoke images.
- Machine-produced artifacts not required for source review.
- Unrelated product/UX/docs churn from staging.
- Wholesale lockfile replacement.
- Direct pushes to `origin`.
- Force pushes.
- Staging branch pushes to `origin` or `official-fork`.
- Claims that Connector/Modeling is fully ready without real credentials and
  verified OpenHuman-compatible provenance.
- Claims that the work has entered official `main` before maintainer merge.
