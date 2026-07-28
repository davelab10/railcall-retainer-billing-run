# Retainer Billing Run

A RailCall workflow for monthly retainer billing. An agency or small SaaS with
a fixed client list runs this once a period. Five nodes take the run from a
raw client list to sent charges: validate, dedup, plan_summary, charge,
reconcile.

- validate rejects any row with no customer id, no amount, or a non-positive
  amount, with a reason per rejected row.
- dedup removes a client that appears twice in one export and a client
  already present in already_billed for this period, since billing someone
  twice is the failure that actually hurts a retainer run.
- plan_summary totals the real planned spend across the whole batch before
  anything is charged.
- charge sends one Stripe test mode PaymentIntent per clean row, with retry
  on transient failures, and only runs at all if the batch is within the
  declared spend ceiling.
- reconcile compares what was intended against what actually landed.

## Install

```
railcall market install dave/retainer-billing-run
```

## Configure before running

The published spec ships with anonymous placeholder data so it can stage and
run out of the box. Replace it with your own before using it for real:

- `context.clients`: your client list, each row `{ "customer_id": "...", "amount_cents": ... }`.
- `context.already_billed`: customer ids already billed for the current period, so a rerun does not double charge.
- `context.billing_period`: a label for the current period, for example `"2026-08"`.
- `context.spend_ceiling_cents`: the total you are willing to charge in one run. This is the field that actually stops the run, see the note below on why it is not `capabilities.max_spend_cents`.

Without this step the workflow only knows about the example rows shipped in
the spec, which are not useful to anyone but the person who wrote them.

There are two spend related fields in this spec, in two different blocks,
and only one of them does anything at run time. Edit `context.spend_ceiling_cents`.
Leave `capabilities.max_spend_cents` alone unless you have read the note below
and understand why it does not gate anything for this workflow.

## Real test results

Both runs below are actual output from staging and applying this workflow
against Stripe test mode, not a description of intended behavior.

**Success path.** Five clean rows in, two rejected by validate, two removed
by dedup, one charge sent:

```
outcome: COMPLETED
taken: [validate, dedup, plan_summary, charge, reconcile]
charge.status: succeeded
charge.id: pi_3TxrJEIiIXjQdCon0R3onw7m
charge.amount: 7900 (usd)
reconcile: { intended: 1, landed: 1, difference: 0, blocked_by_cap: false }
workflow receipt: signed, offline verification passed
```

**Spend ceiling blocks the run.** Same batch, `context.spend_ceiling_cents` set
to 100 against a planned total of 7900. The charge node did not run and no
request reached Stripe:

```
outcome: COMPLETED
taken: [validate, dedup, plan_summary, reconcile]
skipped: [charge]
plan_summary: { total_cents: 7900, spend_ceiling_cents: 100, within_cap: false, proceed: false }
reconcile: { intended: 1, landed: 0, difference: 1, blocked_by_cap: true }
```

The second result matters more than the first. It is the proof that the
spend ceiling is an enforced gate and not just a number in the spec.

## Three things worth knowing before you edit this spec

**The $100 per charge cap comes from RailCall's own Stripe primitive, not
from Stripe.** `stripe_charge_create` refuses any `amount_cents` above 10000
before it makes a request. A client row above $100 fails locally, not mid
batch on Stripe's side.

**`capabilities.max_spend_cents` still tells a staging reviewer nothing
useful for a node inside a `for_each`, as of station v0.28.** Staging plans a
looped node once, with no item in scope, so the plan output shows this
workflow's `charge` node as `for_each_unbounded` and its declared spend still
resolves to 0 cents at staging time. Station v0.28 did add a real platform
side guard at run time: if `capabilities.max_spend_cents` is declared, the
engine now tracks cumulative spend across a `for_each` and raises
`SpendCapExceeded`, rolling back the whole run, once an iteration would push
spend past that ceiling. This workflow keeps its own gate anyway, belt and
suspenders: it enforces its own cap via `context.spend_ceiling_cents`
regardless of the platform's `capabilities.max_spend_cents` check, so a
v0.27-or-older station running this workflow is still safe, and even on
v0.28 or later the result reads as a clean, whole run refusal (`charge`
skipped, `reconcile` explains why) rather than a rollback with an exception
partway through the batch. `context.spend_ceiling_cents` has a different name
from `capabilities.max_spend_cents` on purpose: two spend fields with the
same name, one platform enforced and one workflow enforced, is exactly the
kind of trap that costs a buyer real money the first time they edit the
wrong one. See TESTING.md for both results side by side.

**`for_each` and `cond` are only evaluated when the workflow runs, not during
staging.** A binding typo in either field passes staging with no warning and
only breaks, or silently does nothing, once the workflow actually runs. If
you edit the `charge` node, test the change with a live run against Stripe
test mode, not just a stage.

**There is no bridge between RailCall's module system and its workflow
system, checked again against station v0.28.** A workflow effect node
resolves only through `integration_registry.py`, a table separate from the
module runtime's `LOCAL_HANDLERS` and command registry, so a RailCall module
command such as `stripe.billing.invoice_create` is not reachable from a
workflow node. If a spec sets a module command id as `action_id` anyway,
`resolve_node` does not fail cleanly: it falls back silently to the
provider's first registered action and only errors downstream with a
confusing `TypeError`, so do not assume an unrecognized `action_id` will
fail loudly.

## Testing

See [TESTING.md](TESTING.md) for the actual commands run and their real
output: the success path with a real Stripe test mode PaymentIntent, this
workflow's own spend ceiling blocking a run with zero calls to Stripe, a row
with no customer id being rejected, and a duplicate row being caught.

## License

MIT
