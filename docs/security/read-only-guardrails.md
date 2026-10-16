# Read-Only Guardrails

Byaan is designed for analytical workflows. The application includes prompt instructions and validation layers that block known write operations before query execution.

These guardrails are defense in depth. For production databases, always use database credentials with read-only permissions.

## Summary

Current guardrail layers include:

- Prompt-level rules that instruct the agent to generate read-only database queries.
- SQL AST validation with `sqlglot` before SQL execution.
- MongoDB query parsing that allows known read operations and blocks known writes.
- DynamoDB PartiQL and native API checks that allow read operations only.
- DuckDB SQL validation for uploaded/local file analysis.
- Result limits and execution timeouts on tool paths.
- Optional redaction rules for restricted tables, collections, and columns.

## SQL Databases

SQL validation lives in [`server/tools/sql.py`](../../server/tools/sql.py).

Supported SQL execution paths validate generated SQL with `sqlglot` before execution. Byaan blocks AST nodes for:

- `DELETE`
- `INSERT`
- `UPDATE`
- `CREATE`
- `ALTER`
- `DROP`

The SQL tool also applies a maximum result limit and can block access to redacted tables or columns when redaction rules are present.

Relevant tests:

- [`server/tests/test_sql_validation.py`](../../server/tests/test_sql_validation.py)

## MongoDB

MongoDB tool validation lives in [`server/tools/mongo.py`](../../server/tools/mongo.py), with parsing support in [`server/services/database_operations.py`](../../server/services/database_operations.py).

Byaan allows read-style operations such as:

- `find`
- `findOne`
- `count`
- `countDocuments`
- `estimatedDocumentCount`
- `distinct`
- `aggregate`

Known write operations and write-capable aggregation stages are blocked, including `$out` and `$merge`. The MongoDB execution path also rejects parsed operations that are outside the allowed read-operation set.

Relevant tests:

- [`server/tests/test_mongo_connector.py`](../../server/tests/test_mongo_connector.py)
- [`server/tests/test_mongo_write_detection.py`](../../server/tests/test_mongo_write_detection.py)

## DynamoDB

DynamoDB validation lives in [`server/tools/dynamodb.py`](../../server/tools/dynamodb.py).

For PartiQL mode, Byaan blocks statements that begin with:

- `INSERT`
- `UPDATE`
- `DELETE`

For native API mode, Byaan only allows:

- `get_item`
- `query`
- `scan`
- `batch_get_item`
- `describe_table`

Relevant tests:

- [`server/tests/test_dynamodb_write_detection.py`](../../server/tests/test_dynamodb_write_detection.py)

## DuckDB And File Analysis

DuckDB validation lives in [`server/services/duckdb_service.py`](../../server/services/duckdb_service.py).

Byaan parses DuckDB SQL with `sqlglot`, requires a single statement, blocks disallowed write/schema AST nodes, and rejects disallowed DuckDB commands such as file export, extension loading, and external URL access.

The DuckDB path is used for analytical queries over uploaded or local file-backed datasets. It is intended for local analysis, with performance bounded by local machine resources.

Byaan currently inlines named parameters for file-backed and DynamoDB PartiQL queries in [`server/services/raw_query.py`](../../server/services/raw_query.py), rather than binding them at the driver level. String values are quoted with embedded single quotes doubled; nulls, booleans and numbers use their respective SQL literals. Literal backslashes in string values are preserved. SQL database connectors continue to bind parameters through the driver.

Relevant tests:

- [`server/tests/test_duckdb_service.py`](../../server/tests/test_duckdb_service.py)

## MCP

MCP clients call into the same Byaan application/tooling paths instead of receiving raw database credentials from this repository's MCP setup. Read-only behavior therefore depends on the same connector-specific validation and the permissions of the configured database credentials.

For production MCP workflows:

- Use read-only database users.
- Scope MCP keys to the least access needed.
- Avoid exposing production credentials to broad local developer environments.
- Review generated queries before relying on them for business-critical decisions.

## Known Limits

Application-level query validation cannot replace database permissions. Use least-privilege credentials.

Validation is strongest for supported query forms that the parser understands. Unusual database-specific syntax should be treated carefully and covered by tests before broad production use.

LLM prompts are not a security boundary. Byaan's parser and connector checks are the enforcement layer, and database permissions are the final boundary.

File analysis runs locally but may send relevant schema, prompts, query text, or result excerpts to the configured model provider depending on the workflow.
