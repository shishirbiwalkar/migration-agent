# Scientist Self-Serve Review (notify-and-resolve loop)

Status: **backbone landed** (token model + notify + public respond endpoints). Frontend
scientist page and propose-then-confirm are the next slices.

---

## 1. Why this exists

When the Migration Agent flags wells as anomalous (`risk_level='review'`), the question
"*should these migrate or not?*" is best answered by the **scientist who owns the data** — they
are the domain expert on their own readings. The old model required an admin to guess, or to relay
a reply by hand. This feature closes the loop:

> **Flag → notify the owning scientist → scientist replies in their own words → the Review
> Resolution Agent interprets the reply → wells approved/excluded, attributed to the scientist.**

The LLM earns its place precisely here: a scientist's reply is unstructured natural language
("*the B4 spike was real, instrument was fine, but drop the last reading*"). No deterministic parser
maps that to per-well actions reliably; the agent does — and records the reasoning as audit trail.

---

## 2. The loop

```
Reviewer flags wells (risk_level='review')              ← already happens
        │
        ▼
Reviewer triggers "Notify"  →  mint one magic-link token per flagged scientist
        │   channel: console / link / smtp / sendgrid   (console+link need no infra)
        ▼
Scientist opens link  →  small page showing ONLY their flagged wells
        │   writes a free-text reply
        ▼
[next slice] Agent PROPOSES actions  →  scientist CONFIRMS    ← trust + safety gate
        │
        ▼
review_agent applies  →  wells approved/excluded, approved_by = the scientist
        │   reply text stored as the audit justification
        ▼
Reviewer queue flips:  Notified → Replied → Resolved
```

The agent (`app/agents/review_agent.py`) is reused unchanged — the only new code is the
front of the funnel (mint/notify) and a public, token-scoped surface that calls it.

---

## 3. Design decisions

### 3.1 Notification channel is pluggable and zero-infra by default

`NOTIFY_PROVIDER` selects how the link reaches the scientist. **Real email is optional** — it adds
an account, API keys, a verified sender domain, deliverability config, and per-email cost, none of
which is needed to prove the workflow.

| Provider | Setup | What it does |
|---|---|---|
| `console` *(default)* | none | Logs the magic link to the server log |
| `link` | none | Notify endpoint **returns** the link for the reviewer to copy/send manually |
| `smtp` | SMTP host/creds | Sends a real email (opt-in) |
| `sendgrid` | API key + sender | Sends via SendGrid (opt-in) |

Start on `console`/`link`. Swap to `smtp`/`sendgrid` only when real delivery is wanted — it is a
channel swap, not a redesign.

### 3.2 The token is the security boundary

A magic link is a public URL that ultimately writes to production, so the token is scoped hard:

- **Opaque + random** — `secrets.token_urlsafe(32)` (256-bit), stored server-side in
  `review_invitations`. No `trace_id` or PII is exposed in the URL.
- **Single scientist** — the agent's existing name filter (`_NAME_FILTER` in `review_agent.py`)
  guarantees a token can only see/act on *that scientist's* pending wells.
- **Approve/exclude only** — the token cannot reach any other endpoint or scientist.
- **Expiring** — `expires_at` (default 7 days). An expired token returns `410 Gone`; the well falls
  back to the reviewer's manual queue (no dead ends).
- **One active invite per (trace, scientist)** — re-notifying reuses the same row/token.

### 3.3 Attribution improves for free

Because the scientist acts directly, `approved_by` becomes the **scientist's own name** (not
"HITL Reviewer"), and their reply text is persisted on the invitation. The audit trail now reads:
*"Well B04 migrated — Chen_L replied 'spike was real' on 2026-06-03."*

### 3.4 Propose-then-confirm (next slice, strongly recommended)

Since a non-technical user's words drive production writes with no admin in the loop, the scientist
page should show the agent's intended actions ("*I'll keep B04 and drop A02 — confirm?*") before
applying. This is the safety gate, the trust-builder, and the best demo moment in one. The backbone
applies on submit; the confirm step is layered on top in the frontend slice.

---

## 4. Data model

`review_invitations` (auto-created on first use, like `backup_store`; also in `schema_gds.sql`):

| Column | Notes |
|---|---|
| `token` | unique, opaque, `secrets.token_urlsafe(32)` |
| `trace_id` | the migration run |
| `scientist_name` | owner of the flagged wells |
| `status` | `notified → replied → resolved` (or `expired`) |
| `channel` | provider used to notify |
| `reply_text` | the scientist's actual words (audit evidence) |
| `sent_at` / `responded_at` / `expires_at` | lifecycle timestamps |
| UNIQUE `(trace_id, scientist_name)` | one active invite per scientist per run |

---

## 5. API surface (backbone)

Router: `app/api/respond.py`, prefix `/api/respond`.

| Method | Route | Who | Purpose |
|---|---|---|---|
| `POST` | `/api/respond/notify/{trace_id}` | reviewer | Mint/refresh invitations for all flagged scientists (or one via `scientist_name`); returns links |
| `GET`  | `/api/respond/status/{trace_id}` | reviewer | Per-scientist invitation status for the queue UI |
| `GET`  | `/api/respond/{token}` | scientist | Their flagged wells (token-scoped) |
| `POST` | `/api/respond/{token}` | scientist | Submit reply → runs the Review Resolution Agent |

Notification plumbing lives in `app/core/notify.py` (token mint, link build, channel send).

---

## 6. Build sequence

1. **Backbone** *(this slice)* — `review_invitations`, `notify.py`, `respond.py`, wired into `main.py`.
2. Scientist page — `frontend/app/respond/[token]/page.tsx` (mobile-first, scientist-branded).
3. Propose-then-confirm on that page (+ a `dry_run` path on submit).
4. Reviewer queue — invitation status badges + reminders on `/review`.
5. Real email provider (`smtp`/`sendgrid`) — optional channel swap, last.

---

## 7. Relationship to existing docs

- The agent that interprets replies is the **Review Resolution Agent** — see ARCHITECTURE.md §3 and
  WORKFLOW.md Stage 2.
- This feature adds the **outreach front-end** to that agent; it does not change how the agent
  reasons or writes.
