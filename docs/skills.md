# Byaan Skills

Byaan skills connect the analysis agent to external APIs. Each skill is defined by a `SKILL.md` file under `server/skills/<name>/` — the YAML frontmatter declares the API endpoint and credential fields (which drive the Settings > Skills form automatically), and the markdown body is the agent-facing documentation loaded on demand via `get_skill_definition`.

Credentials are AES-GCM encrypted at rest, scoped **Personal** (per user) or **Team** (shared with the workspace). Outbound requests are restricted to the host of the skill's `base_url`; the per-skill domain toggle in Settings > Skills > Whitelisted Domains can disable a skill's network access entirely.

## CloudWatch Logs

Query and search AWS CloudWatch Logs and run Logs Insights queries. Read-only.

**Two authentication modes:**

| Mode | When to use | What you enter |
|------|-------------|----------------|
| **Instance IAM role (auto)** | Byaan runs on AWS (EC2/ECS) | Region only — no keys stored |
| **Access keys** | Byaan runs outside AWS | IAM access key ID + secret + region |

### IAM role mode (recommended on AWS)

No long-lived secret ever enters Byaan. Credentials come from the AWS default chain (instance profile / task role) and rotate automatically.

1. Attach a role to the instance/task running Byaan with `CloudWatchLogsReadOnlyAccess`, or a custom policy limited to: `logs:DescribeLogGroups`, `logs:DescribeLogStreams`, `logs:FilterLogEvents`, `logs:GetLogEvents`, `logs:GetLogRecord`, `logs:StartQuery`, `logs:GetQueryResults`, `logs:StopQuery`
2. **If Byaan runs in Docker on EC2** (standard self-hosted setup), raise the IMDSv2 hop limit so the container can reach instance metadata:
   ```bash
   aws ec2 modify-instance-metadata-options --instance-id i-xxxx \
     --http-put-response-hop-limit 2 --http-tokens required
   ```
3. In **Settings > Skills > CloudWatch Logs**, set Authentication to **Instance IAM role (auto)** and enter the Region

### Access keys mode

Create a dedicated IAM user with the same read-only policy, generate an access key, and enter the key pair + region in the skill settings.

## Sentry

List and inspect error issues, query aggregated event data (Discover), and track releases. Read-only.

**Setup:**

1. In Sentry: **Settings → Developer Settings → Organization Tokens → Create New Token**. Organization tokens (prefix `sntrys_`) are preferred over personal tokens — they survive the creator leaving the org and carry read scopes (`org:read`, `project:read`, `event:read`, `project:releases`)
2. In **Settings > Skills > Sentry**, enter the token and your organization slug (from `sentry.io/organizations/<slug>/`)
3. Optionally list default project slugs to focus the agent

**Notes:**

- Default base URL targets US-region SaaS (`sentry.io`). EU-region orgs (`de.sentry.io`) and self-hosted Sentry need a custom skill with the adjusted base URL
- Sentry paginates via `Link` response headers, which the skill executor does not surface — the agent compensates with `per_page=100` plus narrower filters, and uses the aggregated events endpoint for counts

**What the agent can do:** triage unresolved issues by frequency/users affected, inspect issue details and event stack traces, run Discover aggregations for dashboards, and check whether a release introduced new errors (`firstRelease:<version>`).

## PostHog

Run HogQL analytics, query events, and access persons, insights, dashboards, feature flags, and cohorts. Read-only.

**Setup:**

1. In PostHog: avatar → **Settings → Personal API Keys → Create personal API key** (prefix `phx_`). Grant **Read** on: Query, Event, Person, Insight, Dashboard, Feature flag, Cohort — nothing else
2. Find your numeric Project ID in the project settings URL: `https://us.posthog.com/project/<id>/settings`
3. Enter both in **Settings > Skills > PostHog**

**Notes (verified against PostHog docs, July 2026):**

- Default base URL is US Cloud (`us.posthog.com`); EU Cloud is `eu.posthog.com`. `app.posthog.com` is legacy — do not use it. The capture hosts (`*.i.posthog.com`) are write-only and not used here
- The primary interface is the Query API: `POST /api/projects/{id}/query/` with a `HogQLQuery` body. Without an explicit `LIMIT`, results cap at 100 rows (up to 50,000 with a `LIMIT`)
- The legacy `GET /events/` endpoint is deprecated by PostHog; the skill directs the agent to HogQL instead
- Rate limits: Query 2400/hour per organization; analytics list endpoints 240/min & 1200/hour; CRUD endpoints 480/min & 4800/hour
- Project API keys (`phc_`) are public write-only tokens and will not work — the skill needs a Personal API key

**What the agent can do:** DAU/retention/funnel style HogQL queries, event and pageview breakdowns, person lookups, and saving any query to a live dashboard via `save_skill_query`.

## Adding a new skill

Create `server/skills/<name>/SKILL.md`:

- Frontmatter: `name`, `display_name`, `description`, `emoji`, `homepage`, an `api` block (`base_url`, `domain`, `type: rest|graphql|aws`, `auth.type: bearer|api_key|aws`, optional static `headers`), and `credentials` field definitions
- Credential fields support `optional: true`, `type: select` with `options`/`default`, and conditional visibility via `depends_on: {key, value}` (see the CloudWatch skill's `auth_mode` for an example)
- The auth layer injects the credential named `api_key` as the `Authorization` header; other credentials (org slugs, project ids) are documentation the agent substitutes into endpoint paths itself
- Body: setup guide plus agent-facing API documentation with concrete `execute_skill_api()` examples

Discovery is automatic on server restart. The skill appears in Settings > Skills once a user saves credentials for it.
