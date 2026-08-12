# Retainer Billing Run

A billing export is not proof that a customer should be billed. Recurring data can contain the same customer twice, a customer may already have been billed in another run, provider history may be incomplete, and a timeout can leave the result uncertain after an effect lands.

`dave/retainer-billing-run` v1.7.0 turns that input into a stateful billing run that decides whether billing is safe to continue. It keeps history, planning, approval, money movement, and recovery as separate decisions.

Demo: https://youtu.be/ezVvjoQb3lU

Dependency: `dave/stripe-invoicing >= 1.4.0`.

## How the workflow works

```text
INPUT → HISTORY → PLAN / GUARD → ADVISORY → MONEY → RECONCILE / RECOVERY
```

- **INPUT** rejects malformed rows and unsafe amounts.
- **HISTORY** reads incremental invoice history and checks whether a candidate was already billed.
- **PLAN / GUARD** builds a deterministic plan, explains review findings, selects an approval tier, and binds execution to the approved plan hash.
- **ADVISORY** can add minimized anomaly context without becoming financial authority.
- **MONEY** sends only clean candidates to the module's approval-controlled billing action.
- **RECONCILE / RECOVERY** separates landed, failed, skipped, and unknown outcomes and keeps uncertain work from being retried blindly.

## The 13-node runtime architecture

The source retains a five-node legacy canvas for compatibility. The authoritative runtime graph is the following **13-node `engine_spec`**:

| Node | Purpose | Important behavior |
|---|---|---|
| `validate` | Normalize `ctx.clients` and reject malformed rows. | Requires a non-empty identity and positive whole-integer `amount_cents`; invalid rows do not reach `charge`. |
| `invoice_history` | Read provider invoice history. | Resolves through the module's `stripe_billing_invoice_list`; Station supplies incremental state. |
| `dedup` | Remove duplicate and already-billed candidates. | Reads the flattened history list from `{{nodes.invoice_history._}}`; matching retainer history is labeled `stripe_history_same_period`. |
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

Leave `approved_plan_hash` empty until the Station approval flow supplies it. Optional review, approval-tier, schedule, and advisory fields can remain at their documented defaults. Start with the plan/dry-run surface, inspect what is billable and skipped, then review the plan hash, approval tier, and spend ceiling before any money-moving approval.

Station owns incremental `since`, `exclude_invoice_ids`, cursor, and watermark state. Do not create a local cursor or manually advance a watermark in workflow context.

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

The guard fails closed for incomplete or invalid execution conditions. During verification, a blocked guard could previously coexist with a completed workflow state; the final behavior stops execution and reports the block truthfully instead.

## Spend ceiling

The ceiling is enforced before the next effect would exceed the permitted amount. Verified boundary examples:

- 700 cents with a 699-cent cap: blocked before charge;
- 700 cents with a 700-cent cap: allowed;
- a 1,100-cent batch with a 1,000-cent cap: an under-cap item may land, then the next effect is blocked before the ceiling is exceeded.

If an effect already landed, a spend-cap rollback state does **not** mean Stripe automatically refunded or reversed it. Reconcile and recovery must use provider truth.

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

## Team approval limitation

Native multi-member Team quorum was **not exercised** in the local verification environment because it requires a second independent Station identity. This is a test-environment limitation, not a workflow defect, and this documentation makes no native quorum E2E claim.

## Known limitations

### Project limitations

- Provider success is claimed only when an actual receipt or effect supports it.
- Unknown provider outcomes require conservative recovery and may need operator/provider confirmation before retry.
- Station owns incremental state, overlap behavior, and watermark settlement; the workflow does not replace those controls.

### Test-environment limitation

- Native multi-member Team quorum was not exercised locally because a second independent Station identity was unavailable.

For the final verification matrix, see [TESTING.md](TESTING.md).
