---
name: sentry
display_name: Sentry
description: Query Sentry issues, events, and releases for error monitoring and triage
emoji: "🛡️"
homepage: https://sentry.io

api:
  base_url: https://sentry.io/api/0
  domain: sentry.io
  type: rest
  auth:
    type: bearer

credentials:
  - key: api_key
    label: Auth Token
    placeholder: "sntrys_..."
    help: "Organization auth token from Sentry → Settings → Developer Settings → Organization Tokens. Starts with 'sntrys_'."
  - key: organization_slug
    label: Organization Slug
    placeholder: "my-org"
    help: "From your Sentry URL: sentry.io/organizations/<slug>/ — or Settings → General Settings."
  - key: project_slugs
    label: Projects (optional)
    placeholder: "backend, frontend"
    help: "Comma-separated project slugs to use as defaults. The agent can still explore other projects if needed."
    optional: true
---

# Sentry API

Use the Sentry API to list and inspect error issues, query event data, and track releases. All access is read-only.

## Setup Guide

### 1. Create an Organization Auth Token

Organization tokens are recommended over personal tokens — they are not tied to a user account, so they keep working if the creator leaves the org.

1. In Sentry, go to **Settings → Developer Settings → Organization Tokens**
2. Click **"Create New Token"**
3. Give it a name (e.g., "Byaan")
4. Copy the token (starts with `sntrys_`) — it is shown only once
5. Paste it in **Settings > Skills > Sentry** in the app

Organization tokens carry a fixed read-oriented permission set (`org:read`, `project:read`, `event:read`, `project:releases`), which is exactly what this skill needs. If you need custom scopes instead, create an **Internal Integration** (Settings → Custom Integrations) and use its token.

### 2. Find Your Organization Slug

Your organization slug appears in every Sentry URL: `https://sentry.io/organizations/<slug>/`. Enter it in the skill settings.

### 3. Region / Self-hosted

The default base URL is `https://sentry.io/api/0` and works for US-region SaaS organizations.

| Deployment | Base URL |
|-----------|----------|
| SaaS (US region) | `https://sentry.io/api/0` |
| SaaS (EU region) | `https://de.sentry.io/api/0` |
| Self-hosted | `https://your-sentry-domain/api/0` |

EU-region or self-hosted organizations need the base URL adjusted — create a custom skill pointing at the right host, reusing the endpoints documented here.

## How Authentication Works

**You do not need to handle authentication manually.** When you call `execute_skill_api()`, the system automatically adds the `Authorization: Bearer <token>` header.

**Note:** Replace `{organization_slug}` in endpoint paths with the value from the credentials (`credentials["organization_slug"]`). It is not substituted automatically.

## How to Call the API

```
execute_skill_api(
  skill_name="sentry",
  endpoint_path="/organizations/{organization_slug}/issues/?query=is:unresolved&statsPeriod=24h",
  method="GET"
)
```

## Key Endpoints

### List Projects

```
GET /organizations/{organization_slug}/projects/
```

Returns project slugs, ids, and platforms. Check `credentials["project_slugs"]` first — if the user pre-configured default projects, scope queries to those unless asked otherwise.

### List Issues (primary triage endpoint)

```
GET /organizations/{organization_slug}/issues/?query=is:unresolved&statsPeriod=24h&sort=freq&project=-1
```

Query params:

- `query` — Sentry search syntax (defaults to `is:unresolved`; pass `query=` empty for all)
- `statsPeriod` — relative window: `1h`, `24h`, `14d`, `90d`
- `sort` — `date` (last seen), `freq` (event count), `new` (first seen), `user` (users affected)
- `project` — numeric project id; `-1` for all projects
- `per_page` — up to 100

Each issue includes `id`, `title`, `culprit`, `level`, `count`, `userCount`, `firstSeen`, `lastSeen`, `permalink`.

### Issue Search Syntax

- `is:unresolved` / `is:resolved` / `is:ignored`
- `level:error`, `level:fatal`
- `release:1.2.0`, `firstRelease:1.2.0`
- `assigned:user@example.com`, `is:unassigned`
- `environment:production`
- Free text matches the issue title
- Combine: `is:unresolved level:error environment:production`

### Get Issue Details

```
GET /organizations/{organization_slug}/issues/{issue_id}/
```

Includes full metadata, stats buckets (`stats.24h` / `stats.30d` time series usable for charts), and counts.

### List Events for an Issue

```
GET /organizations/{organization_slug}/issues/{issue_id}/events/?per_page=25
```

Individual occurrences with tags, message, and timestamps. Use the latest event to inspect stack traces and context.

### Aggregated Event Queries (Discover)

```
GET /organizations/{organization_slug}/events/?field=title&field=count()&query=event.type:error&statsPeriod=24h&sort=-count
```

- `field` — up to 20 fields; built-ins (`title`, `release`, `environment`, `user.email`), tags via `tag[name]`, aggregates like `count()`, `count_unique(user)`, `p95(transaction.duration)`
- `query` — same search DSL, supports boolean grouping
- `sort` — must be one of the requested fields, prefix `-` for descending

This returns a flat table — ideal for dashboards. It is intended for aggregation, not bulk export.

### List Releases

```
GET /organizations/{organization_slug}/releases/?per_page=50
```

Optional `query=` substring-matches the version. Useful for "did the last deploy introduce errors" analysis: get recent releases, then filter issues with `firstRelease:<version>`.

## Pagination

Sentry paginates with `Link` response headers, which are not surfaced by `execute_skill_api`. Instead of paginating:

- Set `per_page=100` (the maximum)
- Narrow with `query`, `statsPeriod`, or `project` filters
- Use the aggregated events endpoint for counts instead of listing raw rows

If a result set is clearly truncated at 100 rows, tell the user and suggest a narrower filter.

## Rate Limits

Limits are enforced per endpoint with standard headers; a `429` includes `Retry-After`. Space out polling and avoid tight loops over many issues.

## Common Workflows

### Error Triage ("what's broken right now?")

1. `GET /organizations/{slug}/issues/?query=is:unresolved&statsPeriod=24h&sort=freq` — top issues by volume
2. For the top offenders, fetch issue details for trend (`stats.24h`) and users affected
3. Summarize: issue title, count, users affected, first/last seen, permalink

### Regression Check After a Deploy

1. `GET /organizations/{slug}/releases/` — find the latest version
2. `GET /organizations/{slug}/issues/?query=firstRelease:<version>` — issues introduced by it

### Error Trends for Dashboards

Use the Discover events endpoint with `count()` aggregates, or an issue's `stats` buckets. Save with `save_skill_query` so dashboards re-fetch live data.

### Correlating with Database Data

Sentry events carry tags (request ids, user ids). Query the database for affected records, then search issues/events for the same identifiers to build a unified incident picture — same pattern as the CloudWatch skill.

## Troubleshooting

| Error | Likely Cause | Fix |
|-------|-------------|-----|
| `401 Unauthorized` | Invalid or revoked token | Recreate the organization token and update the skill credentials |
| `403 Forbidden` | Token lacks a scope | Use an organization token, or an internal integration with `org:read`, `project:read`, `event:read` |
| `404` on org endpoints | Wrong organization slug, or org lives in another region | Verify the slug; EU-region orgs need the `de.sentry.io` base URL |
| Empty issue list | Default `is:unresolved` filter hides resolved issues | Pass `query=` explicitly, widen `statsPeriod` |
| Results capped at 100 | Link-header pagination not available | Narrow filters or use aggregated Discover queries |
