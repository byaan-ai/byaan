---
name: posthog
display_name: PostHog
description: Query events, run HogQL analytics, and access product data
emoji: "🦔"
homepage: https://posthog.com

api:
  base_url: https://us.posthog.com
  domain: posthog.com
  type: rest
  auth:
    type: bearer

credentials:
  - key: api_key
    label: Personal API Key
    placeholder: "phx_..."
    help: "From PostHog → Avatar → Settings → Personal API Keys"
  - key: project_id
    label: Project ID
    placeholder: "12345"
    help: "From Project Settings, numeric ID shown in URL"
---

# PostHog

Use the PostHog API to query events, run HogQL analytics, and access product data like persons, insights, and feature flags.

## Setup Guide

Before using this skill, you need to create a Personal API Key and find your Project ID.

### 1. Create a Personal API Key

1. Open PostHog and click your avatar in the bottom-left corner
2. Click **"Settings"**
3. Navigate to the **"Personal API Keys"** tab
4. Click **"Create personal API key"**
5. Give it a label (e.g., "Byaan")
6. Configure scopes as described below
7. Click **"Create key"**
8. Copy the key (starts with `phx_`)
9. Paste this key in **Settings > Skills > PostHog** in the app

### 2. Find Your Project ID

1. In PostHog, go to **Project Settings** (gear icon in sidebar)
2. Look at the URL — it contains the numeric project ID: `https://us.posthog.com/project/12345/settings`
3. The number after `/project/` is your Project ID (e.g., `12345`)
4. Enter this ID in **Settings > Skills > PostHog** in the app

### 3. Required Scopes

When creating the Personal API Key, enable **Read** access for the following scopes:

| Scope | Access | Purpose |
|-------|--------|---------|
| Query | Read | Execute HogQL queries |
| Event | Read | Access event data |
| Person | Read | Access person/user data |
| Insight | Read | Access saved insights |
| Dashboard | Read | Access dashboards |
| Feature flag | Read | Access feature flag configurations |
| Cohort | Read | Access cohort definitions |

All other scopes can be set to "No access" for maximum security.

### 4. Base URL (Region)

The default base URL is `https://us.posthog.com` (US Cloud). If you use a different region:

| Region | Base URL |
|--------|----------|
| US Cloud | `https://us.posthog.com` |
| EU Cloud | `https://eu.posthog.com` |
| Self-hosted | Your custom domain |

## How Authentication Works

**You do not need to handle authentication manually.** When you call `execute_skill_api()`, the system automatically:

1. Retrieves the API key from the configured credentials
2. Adds the `Authorization: Bearer <api_key>` header
3. Sends the request with proper content-type headers

Just call the API with skill name, endpoint path, method, and body — authentication is handled for you.

## How to Call the API

PostHog uses REST with HogQL queries for analytics. The main query endpoint is:

```
POST /api/projects/{project_id}/query/
```

Call `execute_skill_api()` with these parameters:

```
execute_skill_api(
  skill_name="posthog",
  endpoint_path="/api/projects/{project_id}/query/",
  method="POST",
  body={
    "query": {
      "kind": "HogQLQuery",
      "query": "SELECT event, count() FROM events GROUP BY event LIMIT 10"
    }
  }
)
```

**Note:** Replace `{project_id}` with the actual project ID from the credentials.

## HogQL Syntax

HogQL is PostHog's SQL dialect for querying analytics data. It supports standard SQL with some extensions.

### Available Tables

| Table | Description |
|-------|-------------|
| `events` | All tracked events with properties |
| `persons` | User/person data and properties |
| `sessions` | Session aggregations |
| `groups` | Group analytics data |

### Events Table Columns

| Column | Type | Description |
|--------|------|-------------|
| `event` | String | Event name (e.g., `$pageview`, `$autocapture`) |
| `timestamp` | DateTime | When the event occurred |
| `distinct_id` | String | User identifier |
| `properties` | Object | Event properties (access with `properties.$key`) |
| `person_id` | UUID | Internal person identifier |
| `person` | Object | Person properties |

### Accessing Properties

Use dot notation for nested properties:

```sql
-- Event properties
properties.$current_url
properties.$browser
properties.$device_type
properties.$referrer
properties.custom_property

-- Person properties
person.properties.email
person.properties.name
```

### Common Functions

| Function | Description |
|----------|-------------|
| `count()` | Count rows |
| `countDistinct(column)` | Count unique values |
| `sum(column)` | Sum values |
| `avg(column)` | Average |
| `min(column)` / `max(column)` | Min/max |
| `toDate(timestamp)` | Convert to date |
| `dateDiff('day', start, end)` | Date difference |
| `now()` | Current timestamp |
| `today()` | Current date |

## Common Queries

### Recent Events

```sql
SELECT event, timestamp, distinct_id, properties.$current_url
FROM events
ORDER BY timestamp DESC
LIMIT 100
```

### Event Counts by Type

```sql
SELECT event, count() as count
FROM events
WHERE timestamp > now() - INTERVAL 7 DAY
GROUP BY event
ORDER BY count DESC
LIMIT 20
```

### Pageviews by URL

```sql
SELECT
  properties.$current_url as url,
  count() as views
FROM events
WHERE event = '$pageview'
  AND timestamp > now() - INTERVAL 7 DAY
GROUP BY url
ORDER BY views DESC
LIMIT 20
```

### Unique Users

```sql
SELECT countDistinct(distinct_id) as unique_users
FROM events
WHERE timestamp > now() - INTERVAL 30 DAY
```

### User Activity Summary

```sql
SELECT
  distinct_id,
  count() as total_events,
  countDistinct(event) as unique_events,
  min(timestamp) as first_seen,
  max(timestamp) as last_seen
FROM events
GROUP BY distinct_id
ORDER BY total_events DESC
LIMIT 50
```

### Daily Active Users

```sql
SELECT
  toDate(timestamp) as date,
  countDistinct(distinct_id) as dau
FROM events
WHERE timestamp > now() - INTERVAL 30 DAY
GROUP BY date
ORDER BY date DESC
```

### Events by Browser

```sql
SELECT
  properties.$browser as browser,
  count() as events
FROM events
WHERE timestamp > now() - INTERVAL 7 DAY
GROUP BY browser
ORDER BY events DESC
```

### Funnel Analysis (Manual)

```sql
SELECT
  countDistinct(CASE WHEN event = 'page_visit' THEN distinct_id END) as step1_users,
  countDistinct(CASE WHEN event = 'signup_started' THEN distinct_id END) as step2_users,
  countDistinct(CASE WHEN event = 'signup_completed' THEN distinct_id END) as step3_users
FROM events
WHERE timestamp > now() - INTERVAL 30 DAY
```

### Session Duration

```sql
SELECT
  distinct_id,
  dateDiff('minute', min(timestamp), max(timestamp)) as session_minutes
FROM events
WHERE timestamp > now() - INTERVAL 1 DAY
GROUP BY distinct_id
HAVING session_minutes > 0
ORDER BY session_minutes DESC
LIMIT 20
```

### Top Referrers

```sql
SELECT
  properties.$referrer as referrer,
  count() as visits
FROM events
WHERE event = '$pageview'
  AND properties.$referrer != ''
  AND timestamp > now() - INTERVAL 7 DAY
GROUP BY referrer
ORDER BY visits DESC
LIMIT 20
```

## Other API Endpoints

### Get Persons

```
GET /api/projects/{project_id}/persons/
```

### Get Insights

```
GET /api/projects/{project_id}/insights/
```

### Get Dashboards

```
GET /api/projects/{project_id}/dashboards/
```

### Get Feature Flags

```
GET /api/projects/{project_id}/feature_flags/
```

### Get Cohorts

```
GET /api/projects/{project_id}/cohorts/
```

### Get Events (Non-HogQL) — deprecated

```
GET /api/projects/{project_id}/events/?limit=100
```

PostHog has deprecated this endpoint (kept for backwards compatibility, `offset` capped at 50,000). Prefer the Query endpoint with HogQL for any ad-hoc listing or aggregation of events.

## Pagination

List endpoints paginate in one of two ways:

- **Cursor-style `next` URLs** (persons, insights, dashboards): the response contains `{"next": "<url>", "previous": ..., "results": [...]}` — request the `next` URL (path + query only, same host) for the following page
- **`limit` / `offset`** (events): skip-based paging

For HogQL queries, use `LIMIT` and `OFFSET` clauses:

```sql
SELECT event, timestamp
FROM events
ORDER BY timestamp DESC
LIMIT 100
OFFSET 200
```

## Rate Limits

| Endpoint | Limit |
|----------|-------|
| Query endpoint | 2400/hour (per organization) |
| Analytics endpoints (insights, persons, recordings) | 240/minute, 1200/hour |
| CRUD/list endpoints | 480/minute, 4800/hour |

PostHog reserves the right to throttle or reject queries that look like bulk exports — keep queries analytical, not extractive.

## Common Pitfalls

1. **Project ID is required**: All API calls require the project ID in the URL path. Get it from Project Settings.

2. **HogQL requires proper query structure**: The query body must have `"kind": "HogQLQuery"` and the SQL in `"query"`.

3. **Property access syntax**: Use `properties.$key` for event properties. The `$` prefix is part of PostHog's naming convention for built-in properties.

4. **Date filtering**: Use `timestamp > now() - INTERVAL N DAY` for recent data. Without date filters, queries may be slow.

5. **Result caps**: Without an explicit `LIMIT`, HogQL results are capped at 100 rows. With an explicit `LIMIT`, up to 50,000 rows per query.

6. **Region-specific URLs**: US Cloud uses `us.posthog.com`, EU Cloud uses `eu.posthog.com`. Using the wrong region will fail.

7. **Personal vs Project API Keys**: Use Personal API Keys (start with `phx_`), not Project API Keys (which are for client-side tracking).

8. **Case sensitivity**: Event names and property keys are case-sensitive. `$pageview` is different from `$Pageview`.

9. **Empty results**: If queries return no data, check the date range and ensure events exist for that period.

## Notes

- HogQL supports most standard SQL features including JOINs, subqueries, and window functions
- Built-in events start with `$` (e.g., `$pageview`, `$autocapture`, `$pageleave`)
- Custom events use your own naming convention
- Person properties are updated asynchronously, so recent changes may not appear immediately
- For complex analytics, consider using PostHog's built-in Insights rather than raw HogQL
