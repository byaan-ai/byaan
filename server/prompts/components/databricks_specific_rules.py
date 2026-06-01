DATABRICKS_SPECIFIC_RULES = """
For query execution use tool execute_databricks_query.

<read_only>
This connector is STRICTLY READ-ONLY. Only SELECT statements are allowed.
FORBIDDEN: INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, MERGE, TRUNCATE, GRANT, REVOKE,
COPY INTO, REFRESH, VACUUM, OPTIMIZE, REPLACE. The tool will reject these before execution.
</read_only>

<query_rules>
1. Databricks SQL is ANSI-compatible. Standard SELECT/WHERE/GROUP BY/JOIN/CTE/window functions all supported.
2. ALWAYS use fully-qualified three-part names: catalog.schema.table (e.g. samples.tpch.customer).
   The connection may have a default catalog/schema, but explicit three-part names are safest in multi-catalog setups.
3. Backtick identifiers that contain reserved words or special chars: `my-table`, `order`.
4. Use the tool's `limit` parameter for testing (max 50). Do not write LIMIT in the SQL during exploration.
   For final saved queries that need aggregation, write LIMIT inline.
5. Date functions: use `current_date()`, `current_timestamp()`, `date_add()`, `date_diff()`, `date_format()`.
6. String functions: `concat()`, `lower()`, `upper()`, `substring()`, `regexp_replace()`, `split()`.
7. NULL-safe comparison: use `<=>` or `IS NOT DISTINCT FROM`.
8. Use ANSI joins (JOIN ... ON), not comma-separated tables.
</query_rules>

<warehouse_behavior>
- Serverless SQL warehouses may cold-start (2-6 seconds for the first query after idle).
  The tool default timeout is 60 seconds to accommodate this. Subsequent queries are fast.
- Auto-stop kicks in after ~10 min idle. The next query will cold-start again. This is normal.
- Cost: warehouses bill per second while running. Prefer aggregations over fetching raw rows.
</warehouse_behavior>

<schema_discovery>
Unity Catalog three-level namespace: catalog → schema (database) → table.
Pre-loaded schema is in <database_schemas>. Table keys may be plain names (e.g. `customer`),
schema-qualified (`tpch.customer`), or fully-qualified (`samples.tpch.customer`) depending on how
the connection was configured. Always use the FULLY-QUALIFIED form in queries to avoid ambiguity.

If the pre-loaded schema is empty/incomplete, discover via execute_databricks_query:
- SHOW CATALOGS                               — list visible catalogs
- SHOW SCHEMAS IN <catalog>                   — list schemas (a.k.a. databases) in a catalog
- SHOW TABLES IN <catalog>.<schema>           — list tables
- DESCRIBE TABLE <catalog>.<schema>.<table>   — column names + types
- SHOW COLUMNS IN <catalog>.<schema>.<table>  — alternative column listing
- DESCRIBE EXTENDED <catalog>.<schema>.<table> — partition + storage info (optional)

The `samples` catalog (when available) contains public sample data: samples.tpch (customer, orders,
lineitem, nation, region, part, supplier, partsupp), samples.nyctaxi (trips), samples.tpcds_sf1, etc.
</schema_discovery>

<critical>
- The query limit parameter supports up to 50 rows. During exploration, use 3-5 rows.
- For dashboards, aggregate server-side — Databricks excels at this. Don't fetch raw rows then aggregate client-side.
- Use save_query the same way as with PostgreSQL/MySQL connectors.
</critical>

<timeout_handling>
- If "timeout": true, the query exceeded the configured seconds. Optimize:
  add WHERE filters, push aggregations down, avoid wide SELECT *, use LIMIT in inner CTEs.
- Cold-start timeouts (first query after idle) — increase `timeout` to 90+ seconds for the first call.
</timeout_handling>
"""
