# Retainer Billing Run

`dave/retainer-billing-run` v1.9.1 is a governed workflow for recurring
retainer billing. It addresses a practical failure mode for small teams,
consultants, and agencies: the same client can appear twice in an export, a
previous invoice may already exist, provider history may be incomplete, and a
timeout can leave a billing effect unresolved. The workflow prepares and
reviews a plan before money movement, then reconciles the result against what
the provider actually reports.

## What it solves

The workflow separates validation, history-aware deduplication, account
readiness, planning, approval, execution, reconciliation, recovery, and
settlement. Duplicate input rows and already-billed identities are skipped.
Incomplete history is not treated as proof that billing is safe. A plan is
bound to its billing period, project `billing_run_id`, candidates, amounts,
descriptions, and plan hash. Execution must receive the matching approved plan
identity. If the provider outcome is unknown, the workflow preserves that
uncertainty and does not blindly retry it.

## Prerequisites

The exact dependency is:

```text
dave/stripe-invoicing >= 1.6.0
```

Install and configure the Module through Station. The Workflow does not own or
store a Stripe secret. A buyer configures the Module's canonical structured
credential field `STRIPE_SECRET_KEY` through Station Configure. Existing
pre-v1.6.0 buyers may need the Module's one-time explicit Configure migration;
plain legacy credentials are not transparently migrated. Do not put secrets in
Workflow context. The Workflow declares Stripe and Groq capabilities, a
`max_spend_cents` of `50000`, and `allow_irreversible: true`; the actual money
effect remains the Module action behind Station approval.

## Input contract

The authoritative runtime context is `engine_spec.context` in `spec.json`.
Important fields are:

| Field | Shape and purpose |
|---|---|
| `clients` | Array of objects with `email`, optional `customer_id`, and positive whole-integer `amount_cents`; invalid rows are rejected. |
| `already_billed` | Array of identities already billed for the run; matching identities are skipped. |
| `billing_period` | String such as `2026-08`; used for deterministic description/history matching. |
| `billing_run_id` | Project correlation string; it is not Station's `run_id`. |
| `approved_plan_hash` | String supplied by the approval/execution path; a non-matching value blocks execution. |
| `spend_ceiling_cents` | Integer ceiling for the run; current context defaults to `50000`. |
| `approval_tiers` | Object containing `small_batch_max_cents`, `small_batch_quorum`, and `large_batch_quorum`. |
| `anomaly_config` | Object containing the configured batch/amount thresholds used by review analysis. |
| `known_customer_emails`, `prior_amount_by_email`, `prior_customer_ids` | Optional review context used by `review_summary`; these do not authorize money movement. |
| `enable_anomaly_advisory` | Advisory request flag; advisory remains isolated from execution authority. |
| `operator_declared_groq_credential_available` | Operator declaration used by the advisory switch; it does not create a credential. |
| `portfolio_baseline_cents` | Numeric baseline passed to the minimized advisory payload. |
| `preflight_max_age_seconds` | Optional integer freshness bound, 60–86,400 seconds; current default is 900. |
| `preflight_reference_time` | Optional UTC timestamp used to evaluate preflight age; empty means same-run evidence. |

Example input shape, not execution output:

```json
{
  "clients": [{"email": "billing@example.test", "customer_id": "cus_test", "amount_cents": 700}],
  "already_billed": [],
  "billing_period": "2026-08",
  "billing_run_id": "retainer-2026-08-001",
  "approved_plan_hash": "",
  "spend_ceiling_cents": 1000
}
```

## 14-node lifecycle

The following is an explanatory grouping of the authoritative
`engine_spec.nodes` order. It is not a second runtime graph. The top-level
five-node `nodes` array is a secondary, high-level canvas representation; it
is not authoritative runtime behavior. The 14-node `engine_spec.nodes` array
is the authoritative runtime graph verified by the tests. Its declared order
is intentional, and `engine_spec.edges` is intentionally empty: Station uses
the node order plus each node's `parent`, `cond`, `input_from`, and `for_each`
bindings rather than a separate edge-list DAG.

### PREPARE

1. **`validate` — `transform`**: reads `ctx.clients`, normalizes email and
   customer identity, accepts positive integer cents, and records rejected
   rows. It cannot move money.
2. **`invoice_history` — `effect`**: resolves
   `stripe_billing_invoice_list` through Stripe with `limit: 100`. Station
   supplies incremental state; this is a read and cannot move money.
3. **`dedup` — `transform`**: combines validated rows, Station's flattened
   history output, `already_billed`, and the period. It removes duplicate
   export rows and relevant same-period retainer invoices, and emits history
   provenance. It cannot move money.
4. **`account_preflight` — `effect`**: fans out
   `stripe_billing_account_preflight` for each clean candidate. It is a
   read-only readiness check and cannot move money.
5. **`plan_summary` — `transform`**: validates preflight records, checks
   freshness/completeness, builds candidates and totals, and computes the
   deterministic plan identity. It cannot move money.

### REVIEW / APPROVE / EXECUTE

6. **`review_summary` — `transform`**: applies the configured review/anomaly
   rules and prior-customer context to the plan. Findings are advisory review
   information, not financial authority.
7. **`approval_tier` — `transform`**: calculates the required tier/quorum from
   the plan and `approval_tiers`. It reports a requirement; Station remains the
   approval authority.
8. **`execution_guard` — `transform`**: checks the review/approval conditions
   and supplied `approved_plan_hash`. A failed or mismatched guard blocks the
   effect and is not a completed run.
9. **`advisory_switch` — `transform`**: evaluates whether optional Groq
   advisory processing was requested and declared available. It cannot approve
   or authorize billing.
10. **`anomaly_payload` — `transform`**: builds minimized advisory input from
    planned candidates, period, and portfolio baseline. It cannot move money.

### MONEY / RECONCILE / RECOVER / SETTLE

11. **`charge` — `effect`**: fans out planned candidates to the actual action
    `stripe_billing_bill_client`. It is the only Workflow node with a direct
    financial effect and is still protected by Module/Station approval. The
    node declares retry metadata (`max: 3`, `backoff_s: 2`); an unknown
    provider result is not thereby safe to retry.
12. **`reconcile` — `transform`**: compares planned intent with charge results
    and preserves landed, failed, and unknown/unresolved classifications where
    present.
13. **`recovery_plan` — `transform`**: builds a conservative recovery set from
    reconciliation. It does not claim compensation and does not blindly retry
    an effect known to have landed.
14. **`settle` — `transform`**: determines settlement and watermark readiness
    from reconciliation and recovery. Unresolved outcomes hold settlement and
    watermark advancement.

## Incremental history and provenance

Station owns the cursor, `since`, seen state, and watermark. The Workflow does
not create a local cursor or hardcode a `since` value. `dedup` reads the
flattened history result from `{{nodes.invoice_history._}}`, applies the
relevant period/description/status/amount match, and records provenance such
as source action, source command, Station ownership, `since`, returned count,
skipped delivered count, matches considered, suppression count, truncation,
and completeness. A truncated or incomplete history result fails closed for
safe billing decisions; it is not evidence that an invoice does not exist.
Runtime provenance describes the run and does not alter static plan identity.

## Account preflight

`account_preflight` is a read-only provider check before planning. The plan
gate requires one valid record per candidate, `ok: true`, `billing_state:
"ready"`, complete evidence, a valid UTC `as_of`, and acceptable freshness.
Attention-required, not-ready, unknown, stale, refused, malformed, truncated,
ambiguous, or mismatched records block planning before `charge`. A preflight
pass is readiness evidence only; it is not approval, an Airlock decision, a
plan pin, or financial authority.

## Deterministic planning and plan binding

`plan_summary` constructs candidate identity, amount, currency, description,
period, project billing run, candidate count, and total planned cents. It
computes the plan hash from the canonical plan representation. The exact
approved plan hash is supplied to `execution_guard`; a changed plan or wrong
hash cannot silently reuse approval. Station's signed plan/receipt remains the
runtime authority for approval evidence. `billing_run_id` is project
correlation only and never replaces Station's `run_id`.

## Approval, spend cap, and execution

Station controls approval, quorum, Airlock state, receipts, and execution
authorization. The Workflow declares `max_spend_cents: 50000` and
`allow_irreversible: true`; the latter permits the declared effect to exist but
does not bypass approval. A cap or guard failure blocks the next effect before
the cap is exceeded. A previously landed effect is not automatically refunded
or rolled back by this Workflow. Planned, approved, attempted, and landed are
distinct states where the runtime/provider result supports that distinction.

## Reconciliation, recovery, and settlement

Reconciliation uses provider/action results rather than local optimism. A
landed result is preserved as landed; a provider refusal can be failed; and an
ambiguous timeout remains unknown/unresolved. Recovery keeps known-landed work
out of an unsafe retry set and may require operator/provider confirmation. The
Workflow does not promise automatic Stripe compensation or automatic retry of
unknown effects. Settlement and watermark advancement remain held until the
unresolved state is resolved according to Station's state contract.

## Advisory isolation

Groq advisory processing is optional and minimized. `advisory_switch` and
`anomaly_payload` do not approve, charge, change Stripe state, bypass the
execution guard, change unknown to landed, or raise the spend cap. Missing
advisory credentials or invalid advisory output cannot authorize billing.

## Failure behavior

| Failure | Workflow behavior |
|---|---|
| Invalid row or amount | Rejects the row before planning/effect. |
| Duplicate export row | Skips it as `duplicate_in_export`. |
| Already billed identity | Skips it as `already_billed_this_period`. |
| Relevant provider history | Skips it as `stripe_history_same_period`. |
| History failure/truncation | Fails closed; no charge is allowed from incomplete history. |
| Preflight not ready/stale/malformed | Planning gate refuses; no charge. |
| Cap exceeded | Blocks the next effect before exceeding the declared ceiling. |
| Approval/hash missing or mismatched | Execution guard blocks; not completed. |
| Provider refusal | Reconciliation can classify the result as failed. |
| Ambiguous provider outcome | Preserved as unknown/unresolved; unsafe retry is not implied. |
| Recovery unresolved | Settlement and watermark advancement remain held. |

## Known limitations

The dependency must resolve to Module v1.6.0 or later. Stripe credentials belong
to Station/Module Configure, not Workflow context. TEST/fixture and harness
evidence is not production-money proof. Provider state, permissions, latency,
and usage/history completeness remain environmental. Unknown effects require
conservative recovery and may require operator confirmation. The declared
retry metadata does not make unknown effects safe to retry. Native multi-member
Team quorum was not exercised locally because a second independent Station
identity was unavailable. The Workflow has no automatic compensation claim.

## Evidence

- Homepage: https://davelab10.github.io/portofolio/
- Tests: https://github.com/davelab10/railcall-retainer-billing-run/blob/main/TESTING.md
- Current public video: https://youtu.be/ugNt-j2-B8w
- Any v1.8.0 public video is historical evidence only and is not the current
  Workflow release.
