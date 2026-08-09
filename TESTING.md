# Testing report — Retainer Billing Run v1.6.0

Workflow ID: `dave/retainer-billing-run`  
Target runtime: RailCall Station v0.66  
Module dependency: `dave/stripe-invoicing >= 1.3.0`

Demo video: https://youtu.be/He6ZvGOjjt8

This report summarizes the completed workflow verification. Fixture evidence, policy refusal, and provider evidence remain distinct; no missing provider effect is presented as success.

## Final status

| Check | Result |
|---|---|
| Workflow identity | `dave/retainer-billing-run` v1.6.0 |
| Legacy canvas | 5 nodes |
| Engine DAG | Exactly 8 nodes |
| Workflow regression | 14/14 PASS |
| Runtime copy | Matches final source |
| Module dependency | PASS |
| Action resolution | PASS |
| Incremental integration | PASS |
| Scheduling compatibility | PASS |
| Approval-controlled billing path | PASS |
| Reconcile behavior | PASS |
| Station v0.66 compatibility | PASS; no workflow migration required |

Final engine nodes:

1. `validate`
2. `invoice_history`
3. `dedup`
4. `plan_summary`
5. `advisory_switch`
6. `anomaly_payload`
7. `charge`
8. `reconcile`

`invoice_history` is the only added node. `dedup` and `plan_summary` are the changed nodes.

## Validation matrix

The completed runtime matrix covers:

- valid input;
- duplicate input;
- operator-provided `already_billed`;
- blank email;
- zero amount;
- float amount;
- relevant same-period provider history;
- unrelated one-off history;
- truncated history;
- history-source failure;
- over-cap billing;
- no-billable input.

Invalid rows are rejected before effects are prepared. Clean rows retain `email`, whole-integer `amount_cents`, period, and the deterministic description `Retainer billing for <billing_period>`.

## Incremental history and dedup

`invoice_history` resolves to the existing incremental `stripe_billing_invoice_list` action. Station v0.66 returns the effect through a flattened wrapper, and the final binding reads the actual invoice list from:

```text
{{nodes.invoice_history._}}
```

Verified behavior:

- matching same-period retainer history produces `stripe_history_same_period`;
- the matching candidate is removed before `charge`;
- unrelated one-off invoices do not suppress a valid candidate;
- operator `already_billed` protection remains active;
- duplicate input remains suppressed;
- truncated history blocks candidates safely;
- history-source failure prevents charge;
- no hardcoded `since` exists in the workflow;
- incremental state and watermark remain Station-owned.

## Scheduling

Scheduling compatibility passed with a 15-minute interval and concurrency `skip`.

- A successful scheduled run advanced the watermark from `2026-08-09T00:00:00Z` to `2026-08-09T00:01:00Z`.
- Failed, truncated, and manual runs did not advance schedule-owned state.
- Overlap produced `SKIPPED_OVERLAP`.
- Missed ticks did not burst-replay.
- The tested schedule was disabled after verification.
- Live execution remains constrained by Station policy and approval.

## Approval, plan pin, and spend cap

`charge` resolves to `stripe_billing_bill_client`, an approval-controlled external effect from module v1.3.0. The workflow does not call Stripe directly.

Verified governance behavior:

- the reviewed plan is pinned to its approval;
- mutation requires a new approved plan;
- clean candidates fan out only after validation and deduplication;
- the native cumulative spend cap applies before provider execution;
- a 50,001-cent fixture produced `SpendCapExceeded` before charge;
- the blocked-cap receipt recorded `external_api_touched: false` and held the watermark.

Idempotency remains the module's safe-retry boundary and is not described as rollback.

## Advisory isolation

The optional advisory path is minimized, read-only, and separate from the billing critical path. It cannot approve, deny, alter, or execute a charge. Missing credentials or invalid advisory output do not create financial authority.

Live Groq completion was not demonstrated and is not claimed.

## Reconcile

Reconcile reports intended and landed effects without manufacturing provider success. When charge output lacks invoice evidence, the verified result is:

```text
intended = 1
landed = 0
difference = 1
```

This remains truthful for incomplete, policy-blocked, or provider-missing outcomes.

## Studio and planning surfaces

The workflow is listed in Studio v0.66 with its Run and Visual surfaces. Context, DAG structure, action resolution, scheduling compatibility, plan blast radius, approval boundary, spend ceiling, and reconcile output are inspectable without bypassing governance.

## Evidence boundaries

- Provider success is claimed only when an actual provider receipt or effect supports it.
- Live Groq success is not claimed.
- Fixture output is labeled as fixture/runtime-policy evidence rather than live-provider completion.
- No secret, credential, approval token, customer production data, private path, or signing material belongs in this report.
- Station v0.66 receipt-persistence failure may still return `executed: true` alongside `ok: false`; this workflow documentation does not claim otherwise.

## Conclusion

Workflow v1.6.0 is final for Station v0.66: five legacy nodes, exactly eight engine nodes, history-aware incremental deduplication, fail-closed source and truncation handling, schedule-owned watermark settlement, isolated advisory behavior, approval-controlled billing, native spend-cap enforcement, plan pinning, and truthful reconciliation. No workflow source migration was required for Station v0.66.
