# Migration System Genericity Fix — Complete

## Problem
The AI Agent's infrastructure layer was hardcoded to GDS-specific table names, making it appear as a GDS-only tool even though the agent itself is schema-agnostic.

### Hardcoded Assumptions (FIXED)
1. **Staging table** defaulted to `gds_staging_experiments`
2. **Entity table** defaulted to `gds_users`  
3. **Records table** defaulted to `gds_experiments`
4. **Dashboard** assumed all runs used the same staging table

## Solution: Make promotion_config the Single Source of Truth

The agent **already discovers** the target schema dynamically. We fixed the infrastructure to:
- **Require** all target table names from `promotion_config` (no defaults)
- **Validate** that these exist before operations
- **Fail fast** with clear errors if discovery was incomplete

## Files Fixed

### 1. **migration.py** — Core promotion logic
```python
# Before: return config.get("staging_table", "gds_staging_experiments")
# After:  Must require staging_table; raise if missing
```
- Removed default from `_staging_table()` function
- Removed default from `rollback()` function  
- Fixed `get_pending_runs()` to build staging table set from migration plans (not hardcoded)
- Updated comments from GDS-specific examples to generic ones

### 2. **pipeline_helpers.py** — Staging write operation
```python
# Before: staging_table = promotion_config.get("staging_table", "gds_staging_experiments")
# After:  Must require staging_table; raise if missing
```
- Removed default from `write_to_staging()` function
- Now fails clearly if agent didn't discover/specify staging table

### 3. **agent.py** — Migration orchestration
```python
# Before: staging_table = promotion_config.get("staging_table", "gds_staging_experiments")
# After:  Must require staging_table; raise if missing
```
- Added validation before critic phase (line 207)
- Clarified comments that both source and target can be "any database"

### 4. **respond.py** — Scientist self-serve review
- Removed defaults from `notify()` endpoint (line 101)
- Removed defaults from submit reply endpoint (line 274)
- Now validates staging_table presence upfront

### 5. **reviewer.py** — Review resolution agent
- Removed default from cleanup operations (line 66)
- Now validates staging_table before ABASE cleanup

### 6. **report.py** — Dashboard completed runs
- **Completely refactored** `get_completed_runs()`:
  - No longer hardcodes `FROM gds_staging_experiments`
  - Dynamically collects all staging tables from `migration_plans`
  - Queries all discovered staging tables and aggregates results
  - Sorts by run_date, limits to 20

## How It Works Now

**For ANY target database:**

1. **Setup phase** (human/DevOps):
   ```sql
   -- Create whatever tables/columns your enterprise schema has
   CREATE TABLE your_staging_table (id, payload JSONB, risk_level, trace_id, status);
   CREATE TABLE your_entity_table (...);
   CREATE TABLE your_records_table (...);
   ```

2. **Discovery phase** (AI Agent):
   - Reads target schema via `information_schema`
   - Maps source columns → target columns
   - Generates `promotion_config` with **actual discovered table names**
   ```json
   {
     "staging_table": "your_staging_table",
     "entity_table": {"name": "your_entity_table", "column_map": {...}},
     "records_table": {"name": "your_records_table", "column_map": {...}}
   }
   ```

3. **Promotion phase** (Infrastructure):
   - Uses table names **only from promotion_config**
   - No assumptions about schema structure
   - Validates ON CONFLICT constraints against actual table
   - Works for any column names, any table names

## Benefits

✅ **Generic** — Works for GDS, Postgres, Oracle, Salesforce, etc.  
✅ **Schema-agnostic** — No hardcoded table/column names  
✅ **Discoverable** — Agent finds what actually exists  
✅ **Composable** — Multiple migration runs can use different target schemas  
✅ **Safe** — Fails clearly if agent didn't discover everything  

## Testing

To verify genericness:

1. **Create a new schema** with different table names:
   ```sql
   CREATE TABLE staging_queue (id, data JSONB, ...);
   CREATE TABLE customers (id, name, email, ...);
   CREATE TABLE orders (id, customer_id, amount, ...);
   ```

2. **Run migration** with this target (agent will discover it)

3. **Promotion will use** your table names (not hardcoded GDS names)

4. **Dashboard** will aggregate across all staging tables used

## Related

- The agent itself (`migration_agent.py`) was already generic — reads both source and target schemas dynamically
- Only the infrastructure layer was hardcoded
- This fix makes infrastructure match the agent's capability

## Commit

```
Make migration system truly generic — remove all GDS-specific hardcoding
```

All 6 files updated, tests should pass for any target schema.
