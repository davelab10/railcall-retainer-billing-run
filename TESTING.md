# Testing

Every result below is real output from running the published spec.json
through RailCall's own workflow engine (the same plan_workflow / run_workflow
functions `railcall market stage` and `railcall market apply` call into),
against Stripe test mode. Nothing here is a description of intended
behavior. Last run against station v0.28.

## 1. Success path, a real Stripe test mode PaymentIntent

Five rows in context.clients: two valid and distinct, one duplicate of the
first, one with no customer id, one with a zero amount. One charge should
survive validate and dedup.

Command run:

```
res = workflow_engine.run_workflow(wf, ws=ws, signing=signing, allow_live_effects=True)
```

Output:

```
outcome: COMPLETED
taken: [validate, dedup, plan_summary, charge, reconcile]

charge:
  id: pi_3TxrJEIiIXjQdCon0R3onw7m
  status: succeeded
  amount: 7900 (usd)
  description: Retainer cus_example001 2026-07

reconcile:
  { intended: 1, landed: 1, difference: 0, blocked_by_cap: false }

workflow_receipt: signed
offline verify: merkle_root_match true, integrity_match true, signature true
```

## 2. Spend ceiling blocks the run, zero calls to Stripe

Same batch, context.spend_ceiling_cents set to 100 against a real planned
total of 7900. This is the workflow's own gate, context.spend_ceiling_cents
read by plan_summary and enforced through the charge node's cond, and it is
the more important test: it proves the ceiling is a gate, not a number that
only looks like one.

Output:

```
outcome: COMPLETED
taken: [validate, dedup, plan_summary, reconcile]
skipped: [charge]

plan_summary:
  { total_cents: 7900, spend_ceiling_cents: 100, within_cap: false, proceed: false }

reconcile:
  { intended: 1, landed: 0, difference: 1, blocked_by_cap: true }
```

No request reached Stripe. `charge` never ran.

For comparison, station v0.28 also added its own runtime guard on
capabilities.max_spend_cents. Isolating that check alone (capabilities.max_spend_cents
set to 100, this workflow's own context.spend_ceiling_cents left wide open)
produces a different shaped result on v0.28 and later:

```
outcome: ROLLED_BACK
error: SpendCapExceeded('node charge[0] would spend 7900 cents; cumulative 7900 > max_spend_cents=100')
taken: [validate, dedup, plan_summary]
```

Both stop the batch before it overspends. This workflow's own gate produces a
clean COMPLETED run with charge simply skipped and reconcile explaining why.
Relying only on the platform's v0.28 guard produces a ROLLED_BACK run with an
exception, and on any station older than v0.28 that guard does not exist at
all. That is why this workflow keeps its own gate rather than deleting it now
that the platform has one too.

## 3. A row with no customer id is rejected

Row `{ "customer_id": "", "amount_cents": 8400 }` from the same batch above.

Output, from the validate node:

```
{ "row": { "customer_id": "", "amount_cents": 8400 }, "reason": "missing_customer_id" }
```

The row never reaches dedup or charge.

## 4. A duplicate row is detected

Row `{ "customer_id": "cus_example001", "amount_cents": 7900 }` appears
twice in the same batch. validate passes both through since both are
individually well formed. dedup catches the second occurrence.

Output, from the dedup node:

```
{ "customer_id": "cus_example001", "amount_cents": 7900, "reason": "duplicate_in_export" }
```

The same batch also carries a client already present in
context.already_billed for the period, which dedup catches with a second,
distinct reason:

```
{ "customer_id": "cus_example002", "amount_cents": 9500, "reason": "already_billed_this_period" }
```
