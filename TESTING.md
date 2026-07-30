# Testing

Every result below is real output from running the published v1.0.2 spec.json
through RailCall's own workflow engine (the same plan_workflow / run_workflow
functions `railcall market stage` and `railcall market apply` call into),
against Stripe test mode. Nothing here is a description of intended
behavior. Last run against station v0.42, with the platform's native
`capabilities.max_spend_cents` guard (landed v0.39) doing the enforcement.
The workflow's own `cond` gate on the charge node, used through v1.0.1, has
been removed. As of v1.2.0 the charge node uses `stripe_billing_bill_client`
from the `dave/stripe-invoicing` module instead of the built-in
`stripe_charge_create` primitive.

## 1. Success path, a real Stripe test mode PaymentIntent

Five rows in context.clients: two valid and distinct, one duplicate of the
first, one with no email, one with a zero amount. One charge should
survive validate and dedup.

Command run:

```
stage = workflow_mcp.stage_workflow(wf, ws=ws, signing=signing)
res = workflow_mcp.apply_workflow(stage["consent_token"], ws=ws, signing=signing, allow_live=True, live=True)
```

Output:

```
outcome: COMPLETED
taken: [validate, dedup, plan_summary, charge, reconcile]

charge:
  id: pi_3TyVZ6IiIXjQdCon0luTy1Bz
  status: succeeded
  amount: 7900 (usd)

reconcile:
  { intended: 1, landed: 1, difference: 0, blocked_by_cap: false }

workflow_receipt: outcome COMPLETED, signed true
```

## 2. Platform spend cap blocks the run natively, zero calls to Stripe

Same batch, `capabilities.max_spend_cents` set to 100 against a real planned
total of 7900. `context.spend_ceiling_cents` was deliberately left wide open
(999999) in this run to isolate the platform's own guard from anything this
spec might otherwise do. There is no cond node on the charge node in v1.0.2,
so this is entirely the platform's own runtime enforcement.

Output:

```
outcome: ROLLED_BACK
error: SpendCapExceeded('node charge[0] would spend 7900 cents; cumulative 7900 > max_spend_cents=100')
taken: [validate, dedup, plan_summary]
skipped: []

workflow_receipt: signed true, compensated true
```

`charge` never entered the loop, so zero requests reached Stripe. The engine
computed the real spend for the pending iteration and refused before firing,
not after. The workflow receipt is still signed: a refused run produces a
legitimate, offline-verifiable audit record shaped as a rollback with an
error, rather than a clean skip. That shape difference is the one thing worth
knowing if you are comparing this against the v1.0.1 behavior described in
older copies of this README: the old workflow-owned gate produced a clean
`COMPLETED` outcome with `charge` in `skipped` and `reconcile` explaining why.
The v1.0.2 platform-only guard produces `ROLLED_BACK` with the reason in the
receipt's `error` field instead. Both stop the batch before it overspends;
only the audit trail's shape changed.

## 3. A row with no email is rejected

Row `{ "email": "", "amount_cents": 8400 }` from the same batch above.

Output, from the validate node:

```
{ "row": { "email": "", "amount_cents": 8400 }, "reason": "missing_email" }
```

The row never reaches dedup or charge.

## 4. A duplicate row is detected

Row `{ "email": "client001@example.com", "amount_cents": 7900 }` appears
twice in the same batch. validate passes both through since both are
individually well formed. dedup catches the second occurrence.

Output, from the dedup node:

```
{ "email": "client001@example.com", "amount_cents": 7900, "reason": "duplicate_in_export" }
```

The same batch also carries a client already present in
context.already_billed for the period, which dedup catches with a second,
distinct reason:

```
{ "email": "client002@example.com", "amount_cents": 9500, "reason": "already_billed_this_period" }
```
