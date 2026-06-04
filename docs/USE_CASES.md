# Use Cases — Migration Agent Platform

Now that the system is **fully generic** (not hardcoded to GDS), here are real enterprise migration scenarios it can handle:

---

## 1. Legacy System Retirement → Cloud Migration

**Scenario:** Company runs on-premises "HR Management" system with 2M employee records. Moving to Salesforce.

**Challenges:**
- HR system has tables: `employees`, `departments`, `roles`
- Salesforce has: `Account`, `Contact`, `User`, `CustomField__c`
- Column names completely different (e.g., `emp_id` → `sf_account_id`)
- Some fields don't map (legacy `notes` → drop, `salary` → new field)
- 500K employee records; manual mapping would take weeks

**How Migration Agent Solves It:**
1. Point agent at HR database (source) and Salesforce (target)
2. Agent **discovers both schemas** at runtime (no pre-built mappings)
3. Agent **reasons** about semantic equivalence:
   - `emp_id` + `emp_name` → `Account.Id` + `Account.Name`
   - `dept_code` → `Account.Department__c`
4. Agent **transforms** 500K records in-memory with pandas
5. **Screens for anomalies** (e.g., missing required fields, invalid email formats)
6. **Auto-promotes** clean records; **HITL reviews** suspicious ones
7. **Verification report** confirms all 500K in production with audit trail

**Result:** Weeks of manual mapping → hours of AI-driven migration + HITL review.

---

## 2. Data Warehouse Consolidation

**Scenario:** 3 regional warehouses (North, South, West) each with identical-schema databases. Consolidating into single enterprise warehouse.

**Challenges:**
- 3 source databases → 1 target database
- Regional tables have different naming: `north_sales`, `south_sales`, `west_sales`
- Target consolidates: `consolidated_sales` table
- Row counts: 50M per region = 150M total
- Need audit trail showing which region each record came from

**How Migration Agent Solves It:**
1. Run migration 3 times (one per region) with same target database
2. Agent **discovers source schema** (identical across regions)
3. Agent **discovers target schema** once (consolidation schema)
4. Agent **includes regional identifier** as `source_region` for traceability
5. Each run's `promotion_config` includes the regional source field
6. **Auto-promotes** clean records, **flags anomalies** (data quality issues)
7. **Verification agent** confirms: 150M records consolidated, zero data loss

**Result:** 3 parallel migrations running independently, all converging to single warehouse with full lineage.

---

## 3. Compliance & Data Governance — Sensitive Field Exclusion

**Scenario:** Healthcare provider moving patient records from legacy EHR to HIPAA-compliant cloud EHR.

**Challenges:**
- Source EHR has: `patient_id`, `ssn`, `medical_record`, `address`
- Target EHR has: `patient_id`, `medical_record` (SSN/address must NOT migrate)
- If agent accidentally maps `ssn` field, compliance audit fails
- Need proof that excluded columns were intentional, not forgotten

**How Migration Agent Solves It:**
1. Agent discovers target schema (no SSN field)
2. Agent **cannot map** SSN because target has no column for it
3. Agent's `promotion_config` explicitly shows:
   ```json
   {
     "entity_table": {
       "column_map": {
         "patient_id": "patient_id",
         "medical_record": "medical_record"
       }
     }
   }
   ```
4. Absent from map: SSN (no target column)
5. **Audit log shows:** Which columns were NOT migrated (compliance proof)
6. **Verification report:** "Confirmed: 2 of 4 source columns mapped (SSN, address excluded)"

**Result:** Data governance team gets automated proof that sensitive fields were properly excluded, not accidentally migrated.

---

## 4. Acquiring a Company — Database Integration

**Scenario:** Company acquires a smaller competitor. Their CRM (`Zendesk` instance) has 50K contacts; need to merge into acquirer's CRM (`HubSpot`).

**Challenges:**
- Zendesk schema: `zendesk_contact`, `zendesk_company`, `zendesk_interaction`
- HubSpot schema: `hubspot_contact`, `hubspot_company`, `hubspot_deal`
- Different primary keys, different relationship models
- Some Zendesk contacts are stale (last update >2 years ago) — should flag
- Duplicate contacts possible (same email, different records)

**How Migration Agent Solves It:**
1. Agent discovers Zendesk schema (source)
2. Agent discovers HubSpot schema (target)
3. Agent **reasons about mapping:**
   - `zendesk_contact` → `hubspot_contact`
   - `zendesk_company` → `hubspot_company`
   - Relationship mapping: Zendesk has explicit FK; HubSpot uses `company_id`
4. Agent **detects anomalies:**
   - Stale contacts (last_updated > 2 years) → `risk_level = 'review'`
   - Duplicate emails → `risk_level = 'review'`
5. **Auto-promotes** fresh, unique contacts
6. **HITL reviews** stale/duplicate ones:
   - Keep or exclude stale contacts?
   - Which duplicate wins?
7. **Verification:** "49,500 migrated, 500 flagged, 0 duplicates in target"

**Result:** Acquisition integration happens in days with full traceability; no silent duplicates.

---

## 5. Multi-Tenant SaaS Platform Expansion

**Scenario:** SaaS company launches in new region; needs to replicate customer tenant database from `US Region` to `EU Region`.

**Challenges:**
- Schema is identical (same SaaS product)
- But target region has new columns for GDPR compliance: `consent_timestamp`, `data_processing_agreement`
- Need to populate defaults for new GDPR columns while migrating existing data
- Scale: 10M records, must validate no data corruption during replication

**How Migration Agent Solves It:**
1. Agent discovers US source schema (existing columns)
2. Agent discovers EU target schema (includes GDPR columns)
3. Agent **maps existing columns** 1:1
4. Agent **handles new columns** with defaults:
   ```json
   {
     "column_map": {
       "customer_id": "customer_id",
       "email": "email",
       ...
     },
     "new_target_columns_with_defaults": {
       "consent_timestamp": "NOW()",
       "data_processing_agreement": "pending_review"
     }
   }
   ```
5. Agent **validates** all records in EU schema after migration
6. **Auto-promotes** clean records
7. **Verification:** "10M records replicated, GDPR fields initialized, zero nulls in required columns"

**Result:** Regional expansion happens safely; compliance columns initialized correctly; no data loss.

---

## 6. Database Engine Migration (Same Data, Different DB)

**Scenario:** Company running on Oracle; migrating to PostgreSQL (same data, different database system).

**Challenges:**
- Oracle schema: tables, columns, types are similar but SQL syntax differs
- PostgreSQL target: same logical schema, different data types
- Oracle `NUMBER(10,2)` → PostgreSQL `NUMERIC(10,2)`
- Oracle `VARCHAR2(255)` → PostgreSQL `VARCHAR(255)`
- Need to detect type mismatches, truncated strings, precision loss

**How Migration Agent Solves It:**
1. Agent discovers Oracle schema
2. Agent discovers PostgreSQL schema
3. Agent **checks type compatibility:**
   - `NUMBER(10,2)` → `NUMERIC(10,2)`: ✓ compatible
   - `VARCHAR2(4000)` → `VARCHAR(255)`: ⚠️ truncation risk
4. Agent **flags records** where source values exceed target column width
5. Agent **transforms** data types (Oracle formatting → PostgreSQL)
6. **Auto-promotes** records within safe bounds
7. **HITL reviews** records at risk of truncation:
   - Edit to fit? Or reject record?
8. **Verification:** "2.5M records migrated, 50K reviewed (>255 char names), zero truncation"

**Result:** Database engine migration completed safely; no silent data loss from type mismatches.

---

## Common Pattern Across All Use Cases

Regardless of the specific scenario, the system provides:

1. **Schema Discovery** — No hardcoding, no pre-built templates
2. **Semantic Mapping** — AI reasons about column equivalence
3. **Anomaly Detection** — Statistical screening (not hardcoded rules)
4. **Partial Automation** — Auto-promote safe data, HITL review uncertain
5. **Full Audit Trail** — Every decision attributed, timestamped, reversible
6. **Verification Report** — Independent audit of what actually moved

---

## Deployment Checklist per Use Case

For any new migration, ask:

- [ ] What is the source schema? (database URL + credentials)
- [ ] What is the target schema? (database URL + credentials)
- [ ] Are any columns intentionally excluded? (e.g., SSN, internal fields)
- [ ] What constitutes an "anomaly" for your domain? (agent learns from data sampling)
- [ ] Who reviews flagged rows? (HITL team, compliance, SME)
- [ ] What's the rollback plan if verification fails?
- [ ] How long can the source DB be read-locked during migration?

---

## When NOT to Use This System

- **Real-time data sync:** This is batch migration, not replication
- **Schema-less data:** Document DBs without clear schemas need different tools
- **Single table, 100M+ rows:** May need incremental batching (future enhancement)
- **Encrypted source fields:** Agent cannot reason about encrypted data

---

## Scaling Across Enterprise

The system is **per-migration instance**. For enterprise-wide:

1. Deploy one backend service (`app.main:app`)
2. Run multiple migrations (one `POST /api/agent/run` per source→target pair)
3. Each migration gets its own `trace_id` and audit trail
4. Shared HITL queue shows all pending reviews across all migrations
5. Central verification dashboard shows all migration reports

Example: Company with 20 legacy systems → 20 migrations, all running in parallel, all feeding into one HITL console.
