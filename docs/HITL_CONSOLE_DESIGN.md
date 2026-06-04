# Enterprise HITL Console — Design Specification

An enterprise-grade migration management and human-in-the-loop review system.

---

## Core Principle

**One console, one purpose: Safely migrate data from any source to any target.**

The console is NOT a data viewer. It's a **migration orchestration & approval system**.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    HITL CONSOLE (:3000)                     │
├──────────────┬──────────────────────────────────────────────┤
│              │                                              │
│  SIDEBAR     │          MAIN CONTENT AREA                  │
│  (Nav)       │          (Context-aware)                    │
│              │                                              │
│  • Dashboard │  ┌─ Dashboard ────────────────────────────┐ │
│  • Migrations│  │ • All migrations (status, rows, date)   │ │
│  • Reviews   │  │ • Active migration detail               │ │
│  • Reports   │  │ • Mapping Critic verdict                │ │
│  • Audit Log │  └────────────────────────────────────────┘ │
│  • Settings  │                                              │
│              │  ┌─ Migration Control ────────────────────┐ │
│              │  │ [RUN NEW MIGRATION] button             │ │
│              │  │ Source: _________ Target: _________    │ │
│              │  │ [Advanced Options ▼]                   │ │
│              │  └────────────────────────────────────────┘ │
│              │                                              │
│              │  ┌─ Pending Reviews (in active migration)─┐ │
│              │  │ [Entity name] | Status | Signal | ...  │ │
│              │  │ • Chen_L      | review | 63.00 | [Appr]│ │
│              │  │ • Singh_A     | review | 68.40 | [Appr]│ │
│              │  └────────────────────────────────────────┘ │
│              │                                              │
└──────────────┴──────────────────────────────────────────────┘
```

---

## Navigation Sidebar

### Left Sidebar Structure

```
┌─ HITL CONSOLE ─────────────────────┐
│                                    │
│  [Search migrations...            ]│
│                                    │
│ 📊 DASHBOARD                       │
│    Overview of all migrations      │
│                                    │
│ ▶ ACTIVE MIGRATIONS (2)            │
│    ├─ Trace: 171008d7... ⚠ 10 flag│
│    └─ Trace: a42f8c9b...  ✓ clean │
│                                    │
│ ⏳ PENDING REVIEWS (12 total)       │
│    Show rows across all migrations │
│    needing human approval          │
│                                    │
│ ✓ COMPLETED (47)                   │
│    Past migrations (last 30 days)  │
│                                    │
│ 📋 REPORTS                         │
│    Verification reports            │
│                                    │
│ 🔍 AUDIT LOG                       │
│    Every action: who, when, what   │
│                                    │
│ ⚙ SETTINGS                         │
│    Team, permissions, webhooks     │
│                                    │
└────────────────────────────────────┘
```

### Sidebar Collapse

- Responsive: collapse to icons on small screens
- Search box at top: find by trace_id, scientist, date range

---

## Main Content Sections

### 1. DASHBOARD (Default View)

```
╔════════════════════════════════════════════════════════════════╗
║                    MIGRATION DASHBOARD                         ║
╠════════════════════════════════════════════════════════════════╣
║                                                                ║
║  ACTIVE MIGRATIONS (2)                  PENDING REVIEWS (12)   ║
║  ┌────────────────────────┐             ┌─────────────────┐   ║
║  │ trace: 171008d7...     │             │ Chen_L (2 wells)│   ║
║  │ Status: REVIEW_PENDING │             │ Singh_A (2)     │   ║
║  │ Started: 2 hours ago   │             │ Williams_K (1)  │   ║
║  │ Rows: 480 total        │             │ [6 more...]     │   ║
║  │ ✓ 470 auto-approved    │             │ [REVIEW QUEUE]  │   ║
║  │ ⚠ 10 flagged           │             └─────────────────┘   ║
║  │                        │                                    ║
║  │ [Details] [Approve] [Reject] [Rollback]                   ║
║  └────────────────────────┘                                    ║
║                                                                ║
║  trace: a42f8c9b...                                            ║
║  Status: AUTO_APPROVED                                         ║
║  Started: 12 hours ago                                         ║
║  Rows: 320 total (all clean)                                   ║
║  [View Report] [Rollback]                                      ║
║                                                                ║
╠════════════════════════════════════════════════════════════════╣
║  COMPLETED THIS MONTH (47)                                     ║
║  ├─ 2026-06-03: 480 rows (12 reviewed, auto-approved)         ║
║  ├─ 2026-06-02: 320 rows (all clean)                          ║
║  └─ [Show more...]                                             ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝
```

**Key metrics:**
- Active migration count
- Total pending reviews
- Success rate (% auto-approved)
- Time since last completed migration

---

### 2. MIGRATION CONTROL

```
╔════════════════════════════════════════════════════════════════╗
║                   RUN NEW MIGRATION                           ║
╠════════════════════════════════════════════════════════════════╣
║                                                                ║
║  SOURCE DATABASE                                               ║
║  ┌──────────────────────────────────────────────────────────┐ ║
║  │ Preset: [ABASE (us-west-2) ▼]                           │ ║
║  │ or Custom: [_____________________________]              │ ║
║  │ ℹ Reads: users, experiments tables                      │ ║
║  └──────────────────────────────────────────────────────────┘ ║
║                                                                ║
║  TARGET DATABASE                                               ║
║  ┌──────────────────────────────────────────────────────────┐ ║
║  │ Preset: [GDS (us-east-2) ▼]                             │ ║
║  │ or Custom: [_____________________________]              │ ║
║  │ ℹ Writes to: gds_users, gds_experiments                │ ║
║  └──────────────────────────────────────────────────────────┘ ║
║                                                                ║
║  OPTIONS                                                       ║
║  ┌──────────────────────────────────────────────────────────┐ ║
║  │ ☑ Auto-approve clean rows                              │ ║
║  │ ☑ Create source backup before migration                │ ║
║  │ ☐ Dry-run mode (no writes)                             │ ║
║  │ ☐ Pause before critic verdict (manual approval)        │ ║
║  └──────────────────────────────────────────────────────────┘ ║
║                                                                ║
║  INITIATED BY: [Your Name ▼]                                  ║
║  REASON: [_________________________________]                 ║
║                                                                ║
║                  [RUN MIGRATION]      [Reset]                 ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝
```

**Fields:**
- Source preset (default ABASE) or custom URL
- Target preset (default GDS) or custom URL
- Auto-approve checkbox (default checked)
- Backup checkbox (default checked)
- Initiated by (current user)
- Reason (for audit trail)

---

### 3. MIGRATION DETAIL + PROGRESS

```
╔════════════════════════════════════════════════════════════════╗
║ MIGRATION: 171008d7-368d-4236-ae8c-85fc6bf21b81 (Chen_L)     ║
╠════════════════════════════════════════════════════════════════╣
║                                                                ║
║  STATUS: ⚠ PENDING REVIEW                                     ║
║  Started: 2026-06-03 14:30:00 UTC                             ║
║  Initiated by: Alice Chen                                      ║
║                                                                ║
║  PROGRESS                                                      ║
║  ┌──────────────────────────────────────────────────────────┐ ║
║  │ ✓ Source backup                 Completed 14:30         │ ║
║  │ ✓ Schema discovery              Completed 14:31         │ ║
║  │ ✓ Semantic mapping               Completed 14:32         │ ║
║  │ ✓ Data transformation            Completed 14:33         │ ║
║  │ ✓ Anomaly detection              Completed 14:34         │ ║
║  │ ✓ Write to staging               Completed 14:35         │ ║
║  │ ⚠ Mapping Critic review          IN PROGRESS 14:36       │ ║
║  │                                                          │ ║
║  │ CRITIC VERDICT: ⚠ FLAG                                   │ ║
║  │ Severity: [⚠ WARNING]                                    │ ║
║  │                                                          │ ║
║  │ Finding: Type mismatch in 'signal' column                │ ║
║  │  - Source: raw integer 0-100                            │ ║
║  │  - Target: NUMERIC(5,2) expects decimal                 │ ║
║  │  - Impact: 10 rows may lose precision                   │ ║
║  │  - Recommendation: Review flagged rows                  │ ║
║  │                                                          │ ║
║  │ [View Full Critic Report]                               │ ║
║  └──────────────────────────────────────────────────────────┘ ║
║                                                                ║
║  SUMMARY                                                       ║
║  ├─ Total rows: 480                                            ║
║  ├─ Auto-approved: 470 (97.9%)                               ║
║  ├─ Flagged: 10 (2.1%) ← WAITING FOR YOUR REVIEW            ║
║  ├─ Scientists: 2                                             ║
║  └─ Time elapsed: 6 min 15 sec                                ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝
```

**Shows:**
- Migration metadata (trace_id, who started, when)
- Real-time progress steps (source backup → critic verdict)
- Critic verdict with findings and severity
- Row counts (total, auto-approved, flagged)

---

### 4. PENDING REVIEWS (HITL Queue)

```
╔════════════════════════════════════════════════════════════════╗
║                    PENDING REVIEWS (12)                        ║
╠════════════════════════════════════════════════════════════════╣
║                                                                ║
║  FILTER BY                                                     ║
║  Migration: [All ▼] | Risk: [All ▼] | Scientist: [All ▼]     ║
║  Sort: [Date ▼]                                               ║
║                                                                ║
║  MIGRATION: 171008d7... | TRACE: Chen_L | ROWS: 10           ║
║  ┌──────────────────────────────────────────────────────────┐ ║
║  │ ☐ Scientist: Chen_L        │ Well: B03 | Signal: 63.00  │ ║
║  │   ⚠ Flagged (anomaly)      │ Expected: 5–15              │ ║
║  │   Reason: Mean + 2σ above  │ Compound: ABC-001           │ ║
║  │   [Details ▼]              │ Plate: P-2026-0401          │ ║
║  │                            │                             │ ║
║  │                            │ [Approve] [Exclude] [Info]  │ ║
║  └──────────────────────────────────────────────────────────┘ ║
║                                                                ║
║  ┌──────────────────────────────────────────────────────────┐ ║
║  │ ☐ Scientist: Chen_L        │ Well: B04 | Signal: 62.00  │ ║
║  │   ⚠ Flagged (anomaly)      │ Expected: 5–15              │ ║
║  │   Reason: Same plate, both anomalous                    │ ║
║  │   [Details ▼]              │                             │ ║
║  │                            │ [Approve] [Exclude] [Info]  │ ║
║  └──────────────────────────────────────────────────────────┘ ║
║                                                                ║
║  [Select All] [Approve Selected] [Exclude Selected]           ║
║                                                                ║
║  ─── MIGRATION: a42f8c9b... (6 more rows) ───                 ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝
```

**Features:**
- Filter by migration, risk level, scientist
- Bulk selection and actions
- Approve / Exclude buttons per row
- Expand details for deeper inspection

---

### 5. ROW DETAIL INSPECTOR

```
╔════════════════════════════════════════════════════════════════╗
║  INSPECT ROW: Chen_L, Well B03                                ║
╠════════════════════════════════════════════════════════════════╣
║                                                                ║
║  SOURCE DATA (ABASE)                                           ║
║  ┌────────────────────────────────────────────────────────┐   ║
║  │ user_id: 5                                             │   ║
║  │ plate_barcode: P-2026-0401                             │   ║
║  │ well_position: B03                                     │   ║
║  │ raw_value: 63                           ← FLAGGED     │   ║
║  │ recorded_at: 2026-05-15 09:23:00                       │   ║
║  │ compound_id: ABC-001                                   │   ║
║  │ concentration_um: 50.0                                 │   ║
║  │ assay_type: fluorescence                               │   ║
║  └────────────────────────────────────────────────────────┘   ║
║                                                                ║
║  MAPPING (what will be written to GDS)                        ║
║  ┌────────────────────────────────────────────────────────┐   ║
║  │ raw_value (63) → signal                                │   ║
║  │ user_id (5) → gds_user_id (via FK lookup)             │   ║
║  │ well_position (B03) → well_position                    │   ║
║  │ compound_id (ABC-001) → compound_id                    │   ║
║  │ concentration_um (50.0) → concentration                │   ║
║  │ assay_type (fluorescence) → assay_type                 │   ║
║  └────────────────────────────────────────────────────────┘   ║
║                                                                ║
║  ANOMALY DETAILS                                               ║
║  ┌────────────────────────────────────────────────────────┐   ║
║  │ Signal value: 63.00                                    │   ║
║  │ Population mean: 10.2 (±0.8σ)                         │   ║
║  │ Threshold (mean + 2σ): 11.8                            │   ║
║  │ Deviation: 51.2 points above threshold                │   ║
║  │                                                        │   ║
║  │ Context:                                               │   ║
║  │ • Same scientist (Chen_L) has another flagged well   │   ║
║  │ • Same plate (P-2026-0401) has sister well B04       │   ║
║  │ • B04 also 62.0 (similar anomaly)                     │   ║
║  │ • Possible batch contamination or instrument issue    │   ║
║  └────────────────────────────────────────────────────────┘   ║
║                                                                ║
║  YOUR DECISION                                                 ║
║  Reason: [________________________________ ]                 ║
║  [ ✓ APPROVE ]  [ ✗ EXCLUDE ]  [ ? MORE INFO ]               ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝
```

**Shows:**
- Source values as they exist
- Target mapping (what gets written)
- Anomaly explanation (mean, σ, threshold)
- Context (related rows, batch info)
- Decision buttons with reason field

---

### 6. AUDIT LOG

```
╔════════════════════════════════════════════════════════════════╗
║                      AUDIT LOG                                ║
╠════════════════════════════════════════════════════════════════╣
║                                                                ║
║ DATE       | ACTION              | ACTOR       | TRACE_ID     ║
║ ───────────┼─────────────────────┼─────────────┼──────────── ║
║ 14:40 UTC  | approved row (B03)  | Alice Chen  | 171008d7... ║
║ 14:40 UTC  | excluded row (A04)  | Alice Chen  | 171008d7... ║
║ 14:35 UTC  | wrote 470 to stg    | AI Agent    | 171008d7... ║
║ 14:34 UTC  | critic_flagged      | AI Critic   | 171008d7... ║
║ 14:30 UTC  | migration_started   | Alice Chen  | 171008d7... ║
║                                                                ║
║ 13:20 UTC  | auto_approved 320   | AI Agent    | a42f8c9b... ║
║ 13:15 UTC  | migration_started   | Bob Smith   | a42f8c9b... ║
║                                                                ║
║ [Expand] [Export] [Filter]                                    ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝
```

**Contains:**
- Every action: who did it, when, what migration
- Searchable and exportable
- Full accountability trail

---

## Design Principles

| Principle | Implementation |
|-----------|---|
| **Enterprise Focus** | Migration control only, no data browsing |
| **Clear Accountability** | Every action: actor, timestamp, reason |
| **Reduce Cognitive Load** | One screen ≈ one decision (approve/exclude/reject) |
| **Visual Hierarchy** | Status color-coded: green ✓, yellow ⚠, red ✗ |
| **Reversibility** | Rollback option always visible |
| **Auditability** | All decisions in audit log, reason field required |

---

## Color Scheme (Enterprise)

- **Blue** (#0052CC): Primary actions, navigation
- **Green** (#28A745): Success, auto-approved
- **Yellow** (#FFC107): Warning, flagged, pending review
- **Red** (#DC3545): Error, critical issues, rollback
- **Gray** (#6C757D): Completed, historical
- **White**: Background, clean UI

---

## Data Separation

**HITL Console is for migration control. NOT for:**
- ❌ Browsing source data (use ABASE frontend)
- ❌ Browsing target data (use GDS frontend)
- ❌ System logs or infrastructure alerts
- ❌ Data analysis or reporting (use GDS analytics)

**HITL is ONLY for:**
- ✅ Run migration
- ✅ Review flagged rows
- ✅ Approve/exclude/reject decisions
- ✅ Rollback operations
- ✅ Audit trail
- ✅ Mapping Critic verdict

---

## Responsive Design

**Desktop (1920×1080+):**
- Full sidebar + content side-by-side
- All details visible

**Tablet (768-1024px):**
- Collapsible sidebar (hamburger menu)
- Simplified details view

**Mobile (< 768px):**
- Full-screen navigation
- One action per screen
- Large tap targets

---

## Next Steps

1. Implement dashboard view (show all migrations)
2. Implement migration control (run new migration)
3. Implement pending reviews (HITL queue)
4. Add row detail inspector
5. Add audit log view
6. Add rollback controls
7. Integrate with Mapping Critic verdict display
8. Add keyboard shortcuts for power users

---

## Success Metrics

- User can run migration in < 2 clicks
- User can review & approve/exclude a row in < 10 seconds
- All actions searchable in audit log within 1 second
- No data browsing in HITL (users go to ABASE/GDS for that)
