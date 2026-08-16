# Testing report — `dave/retainer-billing-run` v1.8.0

Demo video: https://youtu.be/x79XvHJK-_M

This report summarizes the final workflow verification. Automated tests, controlled effect harnesses, provider evidence, and environment limitations are kept separate.

## Final status

| Check | Result |
|---|---|
| Workflow identity | `dave/retainer-billing-run` v1.8.0 |
| Module dependency | `dave/stripe-invoicing >= 1.5.0` (current module v1.5.0) |
| Legacy canvas | 5 nodes |
| Engine DAG | Exactly 14 nodes |
| Workflow regression | 19/19 passed |
| Module regression | 50/50 passed |
| Combined module + workflow regression | 69/69 passed |
| Action resolution | PASS |
| Incremental integration | PASS |
| Scheduling compatibility | PASS |
| Approval-controlled billing path | PASS |
| Spend ceiling | PASS |
| Reconcile / recovery / settle | PASS |

## Automated regression

The final workflow suite passed **19/19 tests**. The module suite passed **50/50**, for a combined **69/69** result. These are regression/synthetic checks, not a claim that the latest verification executed a live financial write.

Coverage includes:

- workflow identity, v1.8.0 dependency, legacy canvas, and exact 14-node order;
- action resolution for `stripe_billing_invoice_list`, `stripe_billing_account_preflight`, and `stripe_billing_bill_client`;
- Station-owned incremental registration and schedule injection;
- malformed input, duplicate input, and whole-integer validation;
- `already_billed` and relevant provider-history matching;
- unrelated one-off history remaining non-blocking;
- truncated history and source failure preventing charge;
- deterministic plan hash and project `billing_run_id` binding;
- review findings, configurable approval tiers, and execution guard refusal;
- controlled charge fan-out and truthful reconcile output;
- execution refusal before charge when the guard blocks;
- the project-owned approval/recovery reference contract.

## Validation and deduplication

Verified cases:

- malformed input is rejected before an effect is prepared;
- `duplicate_in_export` does not create a second candidate;
- `already_billed_this_period` is skipped;
- relevant same-period retainer history produces `stripe_history_same_period`;
- an unrelated one-off invoice does not suppress a valid candidate;
- history-source failure produces zero charge calls;
- no unintended charge is produced by invalid or already-billed rows.

## Account preflight gate

The live Module→Workflow binding for `account_preflight` passed with Stripe TEST provider reads. The `account_preflight` node resolves `stripe_billing_account_preflight` after `dedup` and before `plan_summary`; its output is normalized from the effect wrapper and matched one-to-one with candidate customer IDs.

The gate passes only `ok: true`, `billing_state: "ready"`, complete evidence, valid UTC `as_of`, and evidence within the configured freshness bound. Missing, stale, incomplete, truncated, refused, malformed, or ambiguous evidence fails closed before planning and therefore before `charge`. This is read-only readiness evidence, not approval, Airlock authority, plan pinning, or an execution guard.

## Incremental history

`invoice_history` resolves through the module's `stripe_billing_invoice_list` action. Station owns `since`, `exclude_invoice_ids`, cursor, seen-window, and watermark state. The workflow contains no local cursor or hardcoded `since`.

The v1.8 runtime binding reads the flattened effect wrapper's list from:

```text
{{nodes.invoice_history._}}
```

Verified behavior:

- complete history can be matched deterministically;
- `truncated: true` fails closed rather than assuming the customer was not billed;
- history failure prevents `charge`;
- failed, truncated, manual, and unresolved runs hold schedule-owned watermark advancement;
- schedule overlap uses `skip` rather than concurrent duplicate work.

The workflow's `history_provenance` is limited to source action/command, Station incremental ownership, `since`, returned count, `skipped_already_delivered`, history matches considered, suppression count, `truncated`, and `completeness`. It does not fabricate a snapshot hash, receipt reference, watermark before/after, or observation-window end. Runtime provenance is not static plan identity and does not cause static plan-root drift.

## Plan, execution guard, and spend

`plan_summary`, `review_summary`, and `approval_tier` produce a reviewable plan and required approval tier. `execution_guard` requires a valid readiness/approval condition and matching approved plan identity. A wrong hash or blocking review condition stops the workflow before `charge` and is not reported as a completed run.

The static plan binds billing period, project `billing_run_id`, candidate identity, amounts, descriptions, totals, and plan identity. Project `billing_run_id` does not replace Station's `run_id`; a changed financial plan cannot reuse an approval for a different plan.

Controlled spend-cap verification covered the three important boundaries:

- 700 cents with a 699-cent cap: blocked before charge;
- 700 cents with a 700-cent cap: allowed;
- 1,100 cents with a 1,000-cent cap: the first under-cap item may land and the next effect is blocked before exceeding the cap.

The cap result is not described as automatic Stripe rollback or compensation for an effect that already landed.

## Reconcile, recovery, and settlement

The controlled mixed-result case preserved:

- a landed result;
- an unknown/unresolved result;
- the unresolved amount;
- `safe_to_retry: false`;
- a held watermark action;
- unresolved settlement;
- a recovery requirement before retry.

Unknown is not treated as a clean failure. Recovery uses provider truth to avoid retrying an effect already known to have landed. The workflow does not claim automatic external compensation.

## Advisory boundary

The advisory path uses `advisory_switch` and `anomaly_payload` with minimized facts. It is read-only and separate from the deterministic billing path. Advisory failure, unavailable credentials, or invalid advisory output cannot authorize, deny, or execute a charge.

No native provider success is inferred from advisory fixtures or controlled harness output.

Advisory output cannot approve, bypass the execution guard, change an `unknown` outcome to `landed`, or raise the spend ceiling. No current live AI success is claimed.

## Receipts and provenance

Workflow receipts and integrity evidence were checked where produced by the verified paths. Provider success is claimed only when a provider receipt or effect supports it. Controlled effect fan-out and recovery tests use harmless harness/provider stubs; they are not production financial writes.

The latest Step 4 verification performed no financial write and generated no live signed workflow receipt. That limitation is explicit; controlled receipts are not presented as live-money evidence.

No secret, credential, approval token, production customer data, private path, or signing material belongs in this report.

## Test-environment limitation

Native multi-member Team quorum was **not exercised** because the local environment requires a second independent Station identity. This is an environment limitation, not a workflow defect. No native Team-quorum E2E verification is claimed.

## Conclusion

Workflow v1.8.0 is verified with five legacy canvas nodes and exactly 14 engine nodes. Validation, history-aware deduplication, account-preflight gating, fail-closed truncation handling, deterministic plan/guard binding, spend-cap boundaries, approval-controlled charge fan-out, reconcile/recovery/settle semantics, and advisory isolation passed. The only stated verification limitation is the unavailable second identity for native multi-member Team quorum.
