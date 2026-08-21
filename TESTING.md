# Workflow testing and reviewer evidence — v1.9.0

This is an evidence report for `dave/retainer-billing-run` v1.9.0. It
separates automated/fixture proof from live financial claims.

## Tested baseline

- Workflow: `dave/retainer-billing-run` v1.9.0
- Engine graph: 14 authoritative nodes
- Dependency: `dave/stripe-invoicing >= 1.6.0`
- Station baseline: v1.5.0
- Capabilities: Stripe and Groq
- Declared spend cap: 50,000 cents
- `allow_irreversible`: true
- Regression: 22 tests passed with no failures or errors in the verified run.
- The top-level five-node canvas is a secondary, high-level representation;
  it is not authoritative runtime behavior. The tested runtime graph is the
  14-node `engine_spec.nodes` array.

## Regression

Run from the repository root:

```bash
python3 -m pytest -q workflow/tests/test_workflow_v170.py
```

Verified result:

```text
22 passed
```

The suite covers identity and graph counts, dependency resolution, action
resolution, capability declarations, Station-owned incremental state, input
validation, deduplication, truncation/failure gates, deterministic planning,
plan binding, approval tiers, execution guard refusal, preflight, provenance,
runtime fan-out, reconciliation, recovery/settlement harness behavior, and
negative execution cases.

## Engine graph validation

The authoritative `engine_spec` contains 14 unique typed nodes in this order:

```text
validate → invoice_history → dedup → account_preflight → plan_summary
→ review_summary → approval_tier → execution_guard
→ advisory_switch → anomaly_payload → charge → reconcile
→ recovery_plan → settle
```

This declared node order is intentional. `engine_spec.edges` is intentionally
empty and is not a separate runtime DAG; Station derives dependencies and
branches from node `parent`, `cond`, `input_from`, and `for_each` bindings.

The graph includes transform and effect nodes, explicit parent/input
bindings, `for_each` on preflight and charge, action IDs
`stripe_billing_invoice_list`, `stripe_billing_account_preflight`, and
`stripe_billing_bill_client`, and effect retry metadata. The test suite checks
that action resolution and the Station plan remain stable and that a graph
change changes the planned root.

## Dependency resolution

The spec declares:

```text
dave/stripe-invoicing >= 1.6.0
```

Tests resolve the current Module command surface for invoice history,
account preflight, and billing execution. The Workflow does not carry the
Stripe secret; Station/Module Configure owns credential resolution, with the
Module v1.6.0 canonical `STRIPE_SECRET_KEY` contract.

## Incremental history and provenance

`invoice_history` resolves `stripe_billing_invoice_list`. Station owns
`since`, `exclude_invoice_ids`, cursor/seen state, and watermark state. The
Workflow contains no hardcoded `since` and does not fabricate cursor or
watermark values. Tests cover schedule injection, Station ownership,
deduplication, source failure, truncation, and provenance fields. Truncated or
incomplete history is not interpreted as “not billed”; it holds the safe path.
Static plan identity is separate from runtime provenance.

## Preflight

`account_preflight` resolves the read-only Module action for each clean
candidate before planning. Tests cover a complete ready result and refusal for
attention-required/not-ready/unknown state, incomplete or truncated evidence,
stale `as_of`, count mismatch, malformed data, and ambiguous customer mapping.
The gate is readiness evidence only; it is not approval or financial authority.

## Deterministic planning and binding

`plan_summary` creates the candidate list, totals, billing period, project
`billing_run_id`, and plan hash from canonical plan content. Tests show equal
inputs produce the same workflow root/plan identity and that a changed plan
changes it. `execution_guard` requires a matching supplied approved plan hash;
wrong or missing identity blocks charge.

## Approval and execution guard

`approval_tier` calculates the required tier/quorum from configured thresholds;
it does not fabricate Station approval. Tests cover approval/hash refusal and
verify that execution-guard blocks are not reported as completed runs. Station
remains the owner of native approval, quorum, receipts, and Airlock state.

## Spend cap

The graph declares `max_spend_cents: 50000` and `allow_irreversible: true`.
Controlled boundary tests cover a 700-cent candidate against 699 and 700-cent
caps, plus a 1,100-cent batch against a 1,000-cent cap. The first permitted
effect may land; the next effect is blocked before exceeding the ceiling. This
is not proof of live financial execution and is not described as automatic
Stripe rollback or compensation.

## Financial effect

- `stripe_billing_invoice_list`: read-only Stripe history action.
- `stripe_billing_account_preflight`: read-only Stripe readiness action.
- `stripe_billing_bill_client`: the approval-controlled financial effect used
  by `charge` for each planned candidate.

The tests use controlled handlers/harnesses for effects. No live production
charge, invoice send, refund, or other production-money write was performed.

## Reconciliation, recovery, and settlement

The reference harness covers landed, failed, skipped, and unknown results.
Tests verify that unknown is distinct from failed, `safe_to_retry` remains
false for unresolved effects, known-landed work is excluded from recovery, and
settlement remains unresolved when provider truth is incomplete. Watermark
advancement is held for failed, truncated, manual, and unresolved states.
The Workflow does not claim automatic external compensation or unsafe
automatic retry.

## Public video

Current public Workflow video: https://youtu.be/wXJn9yWTTn8

The video demonstrates the documented workflow structure and governance
boundaries; it is not proof of production-money execution or live provider
availability.

## Advisory isolation

`advisory_switch` and `anomaly_payload` carry minimized optional Groq context.
Advisory output is read-only decision support. It cannot approve, execute,
change Stripe state, bypass the execution guard, change an unknown result to
landed, or increase the spend cap. No live Groq success is claimed.

## Negative/failure cases

Verified negative cases include malformed rows and amounts, duplicate input,
already-billed identities, relevant history matches, unrelated one-off
history, history failure, truncated history, stale/refused/malformed preflight,
wrong plan hash, approval refusal, execution guard block, and mixed
landed/failed/unknown recovery outcomes.

## Runtime path

The canonical active Station workflow was synchronized to:

```text
dave_retainer-billing-run.json
```

It is v1.9.0 with 14 engine nodes and dependency `dave/stripe-invoicing >=
1.6.0`. Any older workflow artifact found outside the active loader path is
NON-ACTIVE and must not be interpreted as current runtime state.

## Proof boundaries

Unit and fixture tests prove graph contracts, action binding, transforms,
guards, controlled fan-out, reconciliation, and recovery semantics. Controlled
handlers are not live Stripe. No live financial writes were performed in this
documentation step. Tests do not prove account-specific permissions, provider
availability, production money movement, native multi-member Team quorum with
a second Station identity, or public video publication.
