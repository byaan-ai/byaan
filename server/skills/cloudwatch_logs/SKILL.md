---
name: cloudwatch_logs
display_name: CloudWatch Logs
description: Query and search AWS CloudWatch Logs and run Logs Insights queries
emoji: "☁️"
homepage: https://aws.amazon.com/cloudwatch/

api:
  base_url: ""
  domain: amazonaws.com
  type: aws
  aws_service: logs
  auth:
    type: aws

credentials:
  - key: auth_mode
    label: Authentication
    type: select
    default: access_keys
    options:
      - value: access_keys
        label: Access keys
      - value: iam_role
        label: Instance IAM role (auto)
    help: "Use IAM access keys, or — when Byaan runs on AWS — the instance's IAM role via the default credential chain (no keys stored)."
  - key: aws_access_key_id
    label: Access Key ID
    placeholder: "AKIA..."
    help: "AWS Console → IAM → Users → [your user] → Security credentials → Create access key. Starts with 'AKIA'."
    depends_on:
      key: auth_mode
      value: access_keys
  - key: aws_secret_access_key
    label: Secret Access Key
    placeholder: "wJalr..."
    help: "Shown once when creating the access key. If lost, create a new key pair in IAM → Security credentials."
    depends_on:
      key: auth_mode
      value: access_keys
  - key: aws_region
    label: Region
    placeholder: "us-east-1"
    help: "Region where your log groups live. Find in AWS Console top-right dropdown (e.g., us-east-1, eu-west-1, ap-southeast-1)."
  - key: log_group_names
    label: Log Groups (optional)
    placeholder: "/aws/lambda/my-function, /aws/rds/my-db"
    help: "Comma-separated log group names to use as defaults. The agent can still explore other groups if needed. Example: /aws/lambda/auth, /aws/ecs/api-service"
    optional: true
---

# CloudWatch Logs API

Use the AWS CloudWatch Logs API to query log groups, search log events, and run CloudWatch Logs Insights queries.

## Setup Guide

This skill requires **read-only** access to AWS CloudWatch Logs. No write or delete permissions are needed — Byaan only reads your logs, it never modifies them.

### Choose an authentication mode

- **Instance IAM role (recommended when Byaan runs on AWS)** — no keys to create, store, or rotate. Byaan uses the AWS default credential chain (EC2 instance profile / ECS task role / env credentials). Select "Instance IAM role (auto)" and set only the Region.
- **Access keys** — for Byaan deployments outside AWS. An **IAM user** with programmatic access (Access Key ID + Secret Access Key) and read-only CloudWatch Logs permissions. Create a dedicated IAM user for Byaan rather than reusing an admin account.

### Option A: Instance IAM role

1. In the **AWS Console → IAM → Roles**, create (or reuse) a role for the EC2 instance / ECS task running Byaan
2. Attach the `CloudWatchLogsReadOnlyAccess` managed policy, or the custom read-only policy shown below
3. Attach the role to the instance (EC2 → Instance → Actions → Security → Modify IAM role) or the ECS task definition
4. **If Byaan runs in Docker on EC2** (the standard self-hosted setup), the container must be able to reach instance metadata. IMDSv2's default hop limit of 1 blocks containers — raise it once:

```
aws ec2 modify-instance-metadata-options --instance-id i-xxxx --http-put-response-hop-limit 2 --http-tokens required
```

5. In Byaan, go to **Settings > Skills > CloudWatch Logs**, choose **Instance IAM role (auto)**, and set the **Region**

### Option B: Access keys — step-by-step

1. In the **AWS Console → IAM → Users**, create a new user (e.g., `byaan-cloudwatch-reader`)
2. Attach the **AWS managed policy** `CloudWatchLogsReadOnlyAccess`, or create a custom policy with these specific actions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams",
        "logs:FilterLogEvents",
        "logs:GetLogEvents",
        "logs:GetLogRecord",
        "logs:StartQuery",
        "logs:GetQueryResults",
        "logs:StopQuery"
      ],
      "Resource": "*"
    }
  ]
}
```

You can restrict `Resource` to specific log groups if you want tighter scoping (e.g., `"arn:aws:logs:us-east-1:123456789:log-group:/aws/lambda/*"`).

3. Under **Security credentials**, create an **Access Key** (select "Third-party service" as the use case)
4. Copy the **Access Key ID** and **Secret Access Key**
5. In Byaan, go to **Settings > Skills > CloudWatch Logs** and paste:
   - **Access Key ID** — starts with `AKIA...`
   - **Secret Access Key** — the paired secret
   - **Region** — the AWS region where your logs live (e.g., `us-east-1`). CloudWatch Logs are regional, so pick the region matching your services

## How Authentication Works

**You do not need to handle authentication manually.** When you call `execute_skill_api()`, the system automatically:

1. Retrieves the AWS credentials from the configured skill
2. Creates a boto3 CloudWatch Logs client with those credentials
3. Calls the specified action with the provided parameters

Just call the API with skill name, action name, and parameters — authentication is handled for you.

## How to Call the API

CloudWatch Logs uses **action-based invocation**. Set `endpoint_path` to the action name (PascalCase) and `body` to a JSON string of parameters.

```
execute_skill_api(
  skill_name="cloudwatch_logs",
  endpoint_path="DescribeLogGroups",
  method="POST",
  body="{}"
)
```

**Parameters:**

- `skill_name`: Always `"cloudwatch_logs"`
- `endpoint_path`: The action name in PascalCase (e.g., `"FilterLogEvents"`, `"DescribeLogGroups"`)
- `method`: Always `"POST"`
- `body`: JSON string of parameters for the action

## Key Operations

### DescribeLogGroups — List log groups

```
execute_skill_api(
  skill_name="cloudwatch_logs",
  endpoint_path="DescribeLogGroups",
  method="POST",
  body="{}"
)
```

With prefix filter:

```
execute_skill_api(
  skill_name="cloudwatch_logs",
  endpoint_path="DescribeLogGroups",
  method="POST",
  body="{\"logGroupNamePrefix\": \"/aws/lambda/\"}"
)
```

### DescribeLogStreams — List streams in a log group

```
execute_skill_api(
  skill_name="cloudwatch_logs",
  endpoint_path="DescribeLogStreams",
  method="POST",
  body="{\"logGroupName\": \"/aws/lambda/my-function\", \"orderBy\": \"LastEventTime\", \"descending\": true, \"limit\": 10}"
)
```

### FilterLogEvents — Search logs by pattern

This is the primary way to search for log entries. Supports filter patterns.

```
execute_skill_api(
  skill_name="cloudwatch_logs",
  endpoint_path="FilterLogEvents",
  method="POST",
  body="{\"logGroupName\": \"/aws/lambda/my-function\", \"filterPattern\": \"ERROR\", \"startTime\": 1700000000000, \"endTime\": 1700086400000, \"limit\": 100}"
)
```

**Important:** `startTime` and `endTime` are **epoch milliseconds** (not seconds). Multiply Unix timestamps by 1000.

### GetLogEvents — Read a specific stream

```
execute_skill_api(
  skill_name="cloudwatch_logs",
  endpoint_path="GetLogEvents",
  method="POST",
  body="{\"logGroupName\": \"/aws/lambda/my-function\", \"logStreamName\": \"2024/01/15/[$LATEST]abc123\", \"startFromHead\": true, \"limit\": 50}"
)
```

### GetLogRecord — Get full log record by pointer

When Insights queries return results, each record includes a `@ptr` field. Use `GetLogRecord` to retrieve the complete log event from that pointer.

```
execute_skill_api(
  skill_name="cloudwatch_logs",
  endpoint_path="GetLogRecord",
  method="POST",
  body="{\"logRecordPointer\": \"CpMBCgpBY2...(ptr value from Insights)\"}"
)
```

This is useful for drill-down: run an Insights query to find interesting records, then use `@ptr` to get the full untruncated log event.

### StartQuery + GetQueryResults — CloudWatch Logs Insights

Insights queries are **asynchronous**. You must start the query, then poll for results.

**Step 1: Start the query**

```
execute_skill_api(
  skill_name="cloudwatch_logs",
  endpoint_path="StartQuery",
  method="POST",
  body="{\"logGroupNames\": [\"/aws/lambda/my-function\"], \"startTime\": 1700000000, \"endTime\": 1700086400, \"queryString\": \"fields @timestamp, @message | filter @message like /ERROR/ | sort @timestamp desc | limit 50\"}"
)
```

**Note:** For `StartQuery`, `startTime` and `endTime` are **epoch seconds** (not milliseconds, unlike FilterLogEvents).

This returns a `queryId`.

**Step 2: Get results (poll until status is "Complete")**

```
execute_skill_api(
  skill_name="cloudwatch_logs",
  endpoint_path="GetQueryResults",
  method="POST",
  body="{\"queryId\": \"xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx\"}"
)
```

The response includes a `status` field. Poll until it is `"Complete"`. Possible values: `Scheduled`, `Running`, `Complete`, `Failed`, `Cancelled`, `Timeout`.

### StopQuery — Cancel a running Insights query

If a query is taking too long or the user wants to abort, cancel it with `StopQuery`:

```
execute_skill_api(
  skill_name="cloudwatch_logs",
  endpoint_path="StopQuery",
  method="POST",
  body="{\"queryId\": \"xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx\"}"
)
```

Returns `true` if the query was successfully stopped. Use this when `GetQueryResults` keeps returning `Running` after multiple polls.

## CloudWatch Logs Insights Query Syntax

Insights uses a pipe-delimited query language:

```
fields @timestamp, @message, @logStream
| filter @message like /ERROR/
| sort @timestamp desc
| limit 100
```

### Commands

- **fields**: Select fields to display — `fields @timestamp, @message`
- **filter**: Filter results — `filter @message like /pattern/` or `filter statusCode >= 400`
- **sort**: Order results — `sort @timestamp desc`
- **limit**: Cap results — `limit 100`
- **stats**: Aggregation — `stats count(*) by bin(1h)` or `stats avg(duration) by functionName`
- **parse**: Extract fields from messages — `parse @message "Duration: * ms" as duration`
- **display**: Choose which fields appear in output (after stats)

### Built-in Fields

- `@timestamp` — Event timestamp
- `@message` — Full log message
- `@logStream` — Log stream name
- `@log` — Log group identifier
- `@ingestionTime` — When CloudWatch received the event

### Example Insights Queries

**Count errors per hour:**

```
fields @timestamp, @message
| filter @message like /ERROR/
| stats count(*) as errorCount by bin(1h)
| sort errorCount desc
```

**Average Lambda duration:**

```
filter @type = "REPORT"
| parse @message "Duration: * ms" as duration
| stats avg(duration) as avgDuration, max(duration) as maxDuration by bin(1h)
```

**Top 10 most frequent error messages:**

```
filter @message like /ERROR/
| parse @message "ERROR *" as errorMsg
| stats count(*) as cnt by errorMsg
| sort cnt desc
| limit 10
```

**Request tracing by ID:**

```
fields @timestamp, @message, @logStream
| filter @message like /request-id-here/
| sort @timestamp asc
```

**P99 latency:**

```
filter @type = "REPORT"
| parse @message "Duration: * ms" as duration
| stats pct(duration, 99) as p99, pct(duration, 95) as p95, avg(duration) as avg by bin(1h)
```

## Log Analysis Patterns

Use these Insights query recipes for operational analysis. These replicate the kind of analysis that dedicated log analysis tools perform.

### Error Pattern Detection

Identify distinct error types and their frequency:

```
filter @message like /(?i)(error|exception|fatal|fail)/
| parse @message /(?<errorType>[\w.]+Exception|[\w.]+Error|FATAL|FAIL\w*)/
| stats count(*) as occurrences by errorType
| sort occurrences desc
| limit 20
```

### Spike Detection

Compare current error rate against the previous period to detect anomalies:

```
filter @message like /ERROR/
| stats count(*) as errors by bin(5m)
| sort @timestamp desc
```

After retrieving results, compare the most recent bins against the average of earlier bins. A bin with >2x the average signals a spike.

### Log Volume Analysis

Detect unusual log volume changes (potential indicator of issues):

```
stats count(*) as logCount by bin(5m)
| sort @timestamp desc
```

### Message Pattern Extraction

Extract structured data from unstructured log messages:

```
parse @message /\[(?<level>\w+)\]\s+(?<component>[\w.]+)\s+-\s+(?<msg>.+)/
| stats count(*) as cnt by level, component
| sort cnt desc
```

### Lambda Cold Start Analysis

```
filter @type = "REPORT"
| parse @message "Init Duration: * ms" as initDuration
| filter ispresent(initDuration)
| stats count(*) as coldStarts, avg(initDuration) as avgInitMs, max(initDuration) as maxInitMs, pct(initDuration, 99) as p99InitMs by bin(1h)
```

### API Gateway Latency Breakdown

```
filter @message like /requestId/
| parse @message "integrationLatency: *" as integrationLatency
| parse @message "latency: *" as totalLatency
| stats avg(totalLatency) as avgTotal, avg(integrationLatency) as avgBackend, pct(totalLatency, 99) as p99Total by bin(15m)
```

### Error Rate as Percentage

```
stats count(*) as total, sum(@message like /ERROR/) as errors by bin(1h)
| sort @timestamp desc
```

### Cross-Log-Group Analysis

Query multiple log groups simultaneously by passing an array to `logGroupNames` in `StartQuery`:

```
execute_skill_api(
  skill_name="cloudwatch_logs",
  endpoint_path="StartQuery",
  method="POST",
  body="{\"logGroupNames\": [\"/aws/lambda/auth-service\", \"/aws/lambda/api-gateway\", \"/aws/lambda/order-service\"], \"startTime\": 1700000000, \"endTime\": 1700086400, \"queryString\": \"filter @message like /ERROR/ | stats count(*) as errors by @logStream | sort errors desc | limit 20\"}"
)
```

## Operational Troubleshooting Workflow

When a user reports an incident or asks to investigate an issue, follow this structured approach:

### Step 1: Gather Context

Ask the user for:
- Which service or log group is affected?
- When did the issue start? (Time range)
- Any known symptoms? (Error messages, status codes, affected endpoints)
- Are there active CloudWatch Alarms? (User may know from AWS Console)

### Step 2: Assess Scope

Run a broad error scan across relevant log groups:

```
filter @message like /(?i)(error|exception|fatal|timeout|refused)/
| stats count(*) as cnt by bin(5m)
| sort @timestamp desc
```

This reveals whether errors are spiking, steady, or resolved.

### Step 3: Identify Root Cause

Drill into the error types found in Step 2:

```
filter @message like /ERROR/
| parse @message /(?<errorType>[\w.]+Exception|[\w.]+Error)/
| stats count(*) as cnt by errorType
| sort cnt desc
| limit 10
```

### Step 4: Trace Specific Requests

Once you identify the error pattern, trace individual affected requests:

```
fields @timestamp, @message, @logStream
| filter @message like /the-specific-error-or-request-id/
| sort @timestamp asc
| limit 50
```

### Step 5: Correlate with Database Data

If the user has database connections configured, cross-reference:
- Find affected records in the database using timestamps or request IDs from logs
- Check for database-side errors in the same time window
- Present a unified timeline: log events + database records

### Step 6: Summarize and Recommend

Present findings as:
1. **What happened** — the error type, frequency, and affected scope
2. **When** — exact time window with spike detection
3. **Impact** — which services/endpoints/users were affected
4. **Likely cause** — based on error patterns and log context
5. **Next steps** — suggested remediation or further investigation

## Future: CloudWatch Metrics & Alarms

CloudWatch Metrics (`GetMetricData`, `ListMetrics`) and Alarms (`DescribeAlarms`, `DescribeAlarmHistory`) require the `cloudwatch` AWS service, not `logs`. These are planned as a separate skill (`cloudwatch_metrics`) to enable:
- Querying metric time-series data (CPU, memory, custom metrics)
- Checking active alarm states
- Analyzing alarm history and threshold recommendations
- Full alarm→metric→log correlation workflows

Until then, ask the user to share alarm/metric context from the AWS Console when investigating incidents.

## FilterLogEvents Filter Patterns

The `filterPattern` in `FilterLogEvents` supports:

- **Simple text match**: `"ERROR"` — matches any event containing "ERROR"
- **Multiple terms (AND)**: `"ERROR timeout"` — matches events containing both
- **Quoted phrase**: `"\"Connection refused\""` — matches exact phrase
- **JSON field match**: `{ $.statusCode = 500 }` — matches JSON log entries
- **JSON comparison**: `{ $.duration > 1000 }` — numeric comparison
- **JSON compound**: `{ $.statusCode = 500 && $.method = "POST" }` — multiple conditions

## Pagination

Many operations return a `nextToken` when there are more results:

```
execute_skill_api(
  skill_name="cloudwatch_logs",
  endpoint_path="FilterLogEvents",
  method="POST",
  body="{\"logGroupName\": \"/aws/lambda/my-function\", \"filterPattern\": \"ERROR\", \"nextToken\": \"token-from-previous-response\"}"
)
```

Pass the `nextToken` from the previous response to get the next page.

## Common Pitfalls

1. **Timestamps differ by API**: `FilterLogEvents` and `GetLogEvents` use **epoch milliseconds**. `StartQuery` uses **epoch seconds**. Mixing them up returns empty results.

2. **Insights queries are async**: `StartQuery` returns immediately with a `queryId`. You must poll `GetQueryResults` until `status` is `"Complete"`.

3. **Log group names include path**: Always include the full path, e.g., `/aws/lambda/my-function`, not just `my-function`.

4. **FilterLogEvents has a scan limit**: It scans up to 1MB of data per call. For large time ranges, use Insights queries instead.

5. **Results ordering**: `FilterLogEvents` returns events in chronological order by default. Use `startTime`/`endTime` to narrow the window.

6. **Insights query timeout**: Queries can time out after 60 minutes. For large datasets, narrow the time range.

7. **Insights time range**: If no results return, verify `startTime`/`endTime` cover the period when logs were generated.

## Agent Behavior & Workflow

When handling CloudWatch-related requests, follow this workflow:

1. **Check configured log groups first.** Before calling `DescribeLogGroups`, check if the user has pre-configured log groups via the `log_group_names` credential field. If present (comma-separated string), parse it into a list and use those as the default scope for queries. If the user asks to explore beyond those groups or the field is empty, call `DescribeLogGroups` to discover available options.

   Accessing configured log groups at runtime:
   ```
   credentials["log_group_names"]  # e.g., "/aws/lambda/auth, /aws/ecs/api"
   # Parse: [g.strip() for g in value.split(",") if g.strip()]
   ```

2. **Clarify time range.** If the user doesn't specify a time range, ask them — or default to the last 1 hour. Use the current datetime from the system prompt to compute relative times.

3. **Choose the right API:**
   - Use `FilterLogEvents` for simple keyword/pattern searches (e.g., "find ERROR logs", "show me logs containing timeout").
   - Use `StartQuery` (Insights) for aggregations, stats, parsing, or complex multi-field analysis (e.g., "count errors per hour", "average Lambda duration").

4. **Poll Insights queries efficiently.** After calling `StartQuery`, poll `GetQueryResults` with brief pauses between calls. Stop after 10 retries max — if still not complete, use `StopQuery` to cancel the query, inform the user, and suggest narrowing the time range.

5. **Paginate when needed.** If the response contains a `nextToken`, decide whether to fetch more pages based on the user's request. For exploratory queries, the first page is usually sufficient. For exhaustive searches, continue paginating.

6. **Handle errors gracefully.** If an API call fails, read the error message carefully and provide actionable guidance (see Troubleshooting Guide below).

## Time Range Handling

CloudWatch APIs require epoch timestamps. Convert user-friendly inputs as follows:

- **"last 1 hour"** → `endTime = now`, `startTime = now - 3600`
- **"last 24 hours"** → `endTime = now`, `startTime = now - 86400`
- **"last 7 days"** → `endTime = now`, `startTime = now - 604800`
- **"yesterday"** → `startTime = start of yesterday (00:00:00)`, `endTime = end of yesterday (23:59:59)`
- **Specific date/time** → Parse and convert to epoch

**Critical: ms vs seconds distinction.**

| API | Timestamp unit | Multiply Unix epoch by |
|-----|---------------|----------------------|
| `FilterLogEvents` | Milliseconds | × 1000 |
| `GetLogEvents` | Milliseconds | × 1000 |
| `StartQuery` | Seconds | × 1 (use as-is) |

Getting this wrong is the most common cause of empty results.

## Result Presentation

When displaying CloudWatch results to the user:

1. **Lead with a summary.** State the total log count, time range covered, and key patterns found (e.g., "Found 247 log events in the last hour, 38 contain ERROR").

2. **Show representative entries.** Display 5-10 representative log lines, not the full result set. Prioritize entries that illustrate the user's query (errors, warnings, relevant events).

3. **Highlight important details.** Call out timestamps, error levels (ERROR, WARN, FATAL), exception types, and stack traces in your response.

4. **Format aggregations as tables.** For Insights `stats` results, present data in a table. For time-bucketed stats, suggest a time series chart via the dashboard.

5. **Provide next steps.** After presenting results, suggest follow-up actions: narrowing the search, checking related log groups, correlating with other data sources, or saving the query to a dashboard.

## Dashboard Integration

CloudWatch query results can be saved for dashboard use:

1. **Save queries with `save_skill_query`.** After running a successful CloudWatch query, you can persist it for dashboard display. The saved query stores all parameters needed to re-execute via boto3 on each dashboard refresh.

2. **Map results to chart types:**
   - **Time series / line charts** — Best for time-bucketed stats (`stats count(*) by bin(1h)`, latency over time)
   - **Tables** — Best for raw log entries, filtered event lists
   - **Bar charts** — Best for categorical aggregations (`stats count(*) by functionName`, error counts by type)
   - **Single value / metric** — Best for a single aggregated number (total errors, p99 latency)

3. **Saved queries re-execute on refresh.** Each time the dashboard loads, saved CloudWatch queries run again via boto3 with the stored parameters. For relative time ranges (e.g., "last 1 hour"), the time window shifts forward automatically.

## Troubleshooting Guide

| Error | Likely Cause | Fix |
|-------|-------------|-----|
| `AccessDeniedException` | IAM user/role lacks required permissions | Ensure the IAM policy includes `logs:DescribeLogGroups`, `logs:FilterLogEvents`, `logs:StartQuery`, `logs:GetQueryResults`, and other required actions |
| `No AWS credentials found via the default chain` | IAM role mode selected but no role attached, or the container cannot reach instance metadata | Attach a role with the read-only policy to the instance/task. On EC2 with Docker, set the IMDSv2 hop limit to 2 |
| Empty results | Time range doesn't cover log activity, or wrong log group name | Verify `startTime`/`endTime` bracket the expected log period. Use `DescribeLogGroups` to confirm the exact log group name |
| Empty results (Insights) | `startTime`/`endTime` in wrong unit | `StartQuery` uses epoch **seconds**, not milliseconds |
| `ResourceNotFoundException` | Log group doesn't exist in this region | Check the region in skill settings. CloudWatch Logs are regional — logs in `us-east-1` won't appear when querying `eu-west-1` |
| Insights query stuck in `Running` | Query scanning too much data | Use `StopQuery` to cancel, then retry with a narrower time range or more specific filters |
| `InvalidParameterException` | Malformed query syntax or invalid parameter | Double-check Insights query syntax (pipe-delimited) and parameter names (PascalCase) |
| `LimitExceededException` | Too many concurrent queries | Wait a moment and retry. AWS limits concurrent Insights queries (typically 30 per account per region) |

## Combining with Database Data

When users have both database connections and CloudWatch configured, you can correlate data across sources:

1. **Match request IDs.** Query the database for a specific transaction or request ID, then search CloudWatch logs for the same ID to see the full request lifecycle.

2. **Correlate timestamps.** Find anomalies in database metrics (slow queries, error spikes) and check CloudWatch logs for the same time window to identify root causes.

3. **Enrich with context.** Use database records to identify affected users or resources, then search CloudWatch for related log entries to build a complete incident picture.

4. **Workflow example:**
   - Query the database: "Find all failed orders in the last hour"
   - Get the request IDs or timestamps from the results
   - Search CloudWatch: `FilterLogEvents` with those request IDs as the filter pattern
   - Present a unified view: database record + associated log trail
