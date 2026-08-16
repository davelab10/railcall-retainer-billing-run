# Retainer Billing Run

A billing export is not proof that a customer should be billed. Recurring data can contain the same customer twice, a customer may already have been billed in another run, provider history may be incomplete, and a timeout can leave the result uncertain after an effect lands.

`dave/retainer-billing-run` v1.8.0 turns that input into a stateful billing run that decides whether billing is safe to continue. It combines validation, incremental provider history, duplicate suppression, account readiness, deterministic planning, approval and execution guards, spend controls, reconciliation/recovery, and settlement while keeping advisory output isolated from financial authority.

Demo: https://youtu.be/x79XvHJK-_M

Dependency declared by the current `workflow/spec.json`: `dave/stripe-invoicing >= 1.5.0`. The current module implementation is `dave/stripe-invoicing` v1.5.0 with 34 commands; the earlier cycle baseline was v1.4.0.

## How the workflow works

```text
INPUT → HISTORY → PREFLIGHT → PLAN / GUARD → MONEY → RECONCILE / RECOVERY → SETTLE
```

- **INPUT** rejects malformed rows and unsafe amounts.
- **HISTORY** reads incremental invoice history and checks whether a candidate was already billed.
- **PREFLIGHT** checks bounded, read-only Stripe account readiness before planning can continue.
- **PLAN / GUARD** builds a deterministic plan, explains review findings, selects an approval tier, and binds execution to the approved plan identity.
- **MONEY** sends only clean candidates to the module's approval-controlled billing action.
- **RECONCILE / RECOVERY** separates landed, failed, skipped, and unknown outcomes and keeps uncertain work from being retried blindly.
- **SETTLE** advances the run only when unresolved provider truth does not require a hold.
- Optional **ADVISORY** nodes add minimized anomaly context but never become financial authority.

## The 14-node runtime architecture

The source retains a five-node legacy canvas for compatibility. The authoritative runtime graph is the following **14-node `engine_spec`**:

| Node | Purpose | Important behavior |
|---|---|---|
| `validate` | Normalize `ctx.clients` and reject malformed rows. | Requires a non-empty identity and positive whole-integer `amount_cents`; invalid rows do not reach `charge`. |
| `invoice_history` | Read provider invoice history. | Resolves through the module's `stripe_billing_invoice_list`; Station supplies incremental state. |
| `dedup` | Remove duplicate and already-billed candidates. | Reads the flattened history list from `{{nodes.invoice_history._}}`; matching retainer history is labeled `stripe_history_same_period`. |
| `account_preflight` | Check each candidate's bounded Stripe billing readiness. | Resolves the read-only `stripe_billing_account_preflight` action; it is evidence for planning, not approval or authority. |
| `plan_summary` | Create the deterministic billing plan. | Produces candidates, total, count, period, `billing_run_id`, and plan hash. |
| `review_summary` | Explain review and anomaly findings. | Adds reasons for review without authorizing a charge. |
| `approval_tier` | Calculate the required approval tier. | Reports the required quorum/tier; Station remains the approval authority. |
| `execution_guard` | Check whether execution is ready. | Fails closed when readiness, review, or approved plan identity is missing or invalid. |
| `advisory_switch` | Keep optional advisory availability explicit. | Disabled/unavailable advisory cannot authorize billing. |
| `anomaly_payload` | Build minimized advisory input. | Contains only the facts needed for anomaly analysis. |
| `charge` | Fan out clean candidates. | Resolves through `stripe_billing_bill_client`, an approval-controlled module action. |
| `reconcile` | Compare intended work with provider results. | Preserves landed and unresolved outcomes instead of inventing success. |
| `recovery_plan` | Build a conservative recovery set. | Uses provider truth and does not retry an effect known to have landed. |
| `settle` | Decide whether the run is settled. | Unresolved outcomes keep settlement and watermark advancement on hold. |

The critical gate is deliberately ordered as:

```text
dedup → account_preflight → plan_summary
```

`account_preflight` may only pass a candidate into planning when its provider evidence is `ready`, complete, has a valid `as_of`, and is not stale, refused, malformed, truncated, or otherwise unknown. A preflight pass is not an approval, an Airlock decision, a plan pin, or an execution guard; those controls remain separate.

The final history binding is deliberate:

```text
dedup.input_from.invoice_history = {{nodes.invoice_history._}}
```

## Install and prepare a safe first plan

Install the dependency and workflow through the current marketplace command:

```sh
railcall market install dave/stripe-invoicing
railcall market install dave/retainer-billing-run
```

Then open the workflow in Studio and prepare a plan. Configure a Stripe **TEST** credential in the Station vault through Studio's Stripe integration; do not put a secret in workflow context.

Use a minimal context like this as a starting point:

```json
{
  "clients": [
    {
      "email": "billing-contact@example.test",
      "customer_id": "cus_test_only",
      "amount_cents": 700
    }
  ],
  "billing_period": "2026-08",
  "already_billed": [],
  "billing_run_id": "retainer-2026-08-001",
  "spend_ceiling_cents": 1000
}
```

Before the first run, change:

- `clients`: the rows to consider; each billable row needs an identity and a positive whole-integer amount in cents;
- `billing_period`: the period used in the deterministic retainer description and history match;
- `already_billed`: operator-known billed identities for this run;
- `billing_run_id`: your project correlation ID, not Station's `run_id`;
- `spend_ceiling_cents`: the permitted cumulative amount for the run.
- `preflight_max_age_seconds`: optional freshness bound for preflight evidence (the current default is 900 seconds; the workflow accepts 60–86,400 seconds).
- `preflight_reference_time`: optional UTC reference used for a bounded freshness check; leave it empty for same-run evidence.

Leave `approved_plan_hash` empty until the Station approval flow supplies it. Optional review, approval-tier, schedule, and advisory fields can remain at their documented defaults. Start with the plan/dry-run surface, inspect what is billable and skipped, then review the plan hash, approval tier, and spend ceiling before any money-moving approval.

Station owns incremental `since`, `exclude_invoice_ids`, cursor, and watermark state. Do not create a local cursor or manually advance a watermark in workflow context.

### History provenance

The workflow exposes only provenance fields actually supplied by the incremental source and transform:

- `source_action_id` and `source_command`;
- `station_incremental` and `cursor_owner`;
- Station-injected `since`;
- `returned_count`;
- `skipped_already_delivered`;
- `history_matches_considered`;
- `stripe_history_suppressed_count`;
- `truncated` and `completeness`.

Station remains the owner of `since` injection, watermark, seen state, and incremental settlement. The workflow does not create a `snapshot_hash`, receipt reference, watermark before/after, or observation-window end. Runtime provenance is evidence about this run; it is not the static plan identity or `plan_hash`.

The `account_preflight` gate applies the same fail-closed principle. A missing, stale, incomplete, truncated, refused, malformed, or ambiguous result stops planning before a financial effect. `as_of` is provider evidence time in UTC; a supplied `preflight_reference_time` bounds its age, and otherwise the evidence is treated as same-run.

## History-aware deduplication

This is more than removing duplicate CSV rows. The workflow protects against both `duplicate_in_export` and `already_billed_this_period` by combining input checks with provider invoice history.

The candidate description is deterministic:

```text
Retainer billing for <billing_period>
```

A provider invoice is relevant only when the client, billing period, amount, status, and this description match. A relevant match is skipped with `stripe_history_same_period`. An unrelated one-off invoice does not suppress the candidate.

Station injects incremental history state and may provide `exclude_invoice_ids`. Complete history is usable; `truncated: true` means the history is incomplete and the workflow fails closed. It never interprets incomplete history as evidence that a customer has not been billed. A history-source failure also stops the path before `charge`.

## Plan and execution guard

Clean candidates do not immediately reach `charge`:

1. `plan_summary` creates the deterministic plan and hash.
2. `review_summary` records explainable review/anomaly findings.
3. `approval_tier` calculates the required tier without fabricating an approval.
4. `execution_guard` checks readiness and that the supplied approved plan identity still matches the plan being executed.

The plan binds `billing_period`, project `billing_run_id`, candidate identity, amounts, descriptions, totals, and the plan identity. Project `billing_run_id` is a correlation field; it does not replace Station's `run_id`. A changed financial plan cannot silently reuse an approval for a different plan.

The guard fails closed for incomplete or invalid execution conditions. During verification, a blocked guard could previously coexist with a completed workflow state; the final behavior stops execution and reports the block truthfully instead.

## Spend ceiling

The ceiling is enforced before the next effect would exceed the permitted amount. Verified boundary examples:

- 700 cents with a 699-cent cap: blocked before charge;
- 700 cents with a 700-cent cap: allowed;
- a 1,100-cent batch with a 1,000-cent cap: an under-cap item may land, then the next effect is blocked before the ceiling is exceeded.

If an effect already landed, a spend-cap rollback state does **not** mean Stripe automatically refunded or reversed it. Reconcile and recovery must use provider truth.

`charge` still resolves through `stripe_billing_bill_client`. Writes remain approval-controlled by the Module and Station, and the spend ceiling is enforced before the next effect would exceed it. A passing account preflight is readiness evidence only; it is not an approval or financial authority.

## Reconcile, recovery, and settlement

`reconcile` distinguishes intended billing from what the provider actually reported. An attempted effect can be `landed` or `unknown/unresolved`; `unknown` is not the same as `failed`.

For the verified mixed-result case:

- the landed result is preserved;
- the unknown result and unresolved amount are preserved;
- `safe_to_retry` is `false`;
- the watermark action is held;
- settlement remains unresolved;
- `recovery_plan` is required before a safe retry decision.

If RailCall cannot prove whether an effect landed, the workflow does not blindly retry it. This prevents uncertainty from becoming duplicate billing. The workflow does not claim automatic Stripe compensation.

## Advisory boundary

`advisory_switch` and `anomaly_payload` are optional, minimized, and separate from deterministic financial authority. Advisory failure or unavailability cannot silently authorize a charge, and advisory output is not treated as provider success.

Advisory output cannot approve, bypass the execution guard, change `unknown` to `landed`, or raise the spend ceiling. No live AI/provider-success claim is made here unless a current receipt supports it.

## Team approval limitation

Native multi-member Team quorum was **not exercised** in the local verification environment because it requires a second independent Station identity. This is a test-environment limitation, not a workflow defect, and this documentation makes no native quorum E2E claim.

## Known limitations

### Project limitations

- Provider success is claimed only when an actual receipt or effect supports it.
- Strict preflight can hold planning when readiness evidence is incomplete, stale, truncated, refused, or unknown; bounded provider reads can therefore produce an `unknown` decision.
- Unknown provider outcomes require conservative recovery and may need operator/provider confirmation before retry.
- An unknown outcome is not automatically safe to retry; `safe_to_retry` remains false until provider truth is sufficient.
- Station owns incremental state, overlap behavior, and watermark settlement; the workflow does not replace those controls.
- The latest verification performed no financial write and generated no live signed workflow receipt.

### Test-environment limitation

- Native multi-member Team quorum was not exercised locally because a second independent Station identity was unavailable.

For the final verification matrix, see [TESTING.md](TESTING.md).
