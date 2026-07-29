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
  anything is charged. It is a reporting node now, not a gate, see below.
- charge sends one Stripe test mode PaymentIntent per clean row, with retry
  on transient failures. As of v1.0.2 it carries no manual approval gate:
  the platform itself enforces the declared spend ceiling natively.
- reconcile compares what was intended against what actually landed.

Demo video: https://youtu.be/YUN5aaUmDoc

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
- `capabilities.max_spend_cents`: the total you are willing to charge in one run. As of station v0.39 this is the field that actually stops the run, enforced by the platform itself, not by this spec.

Without this step the workflow only knows about the example rows shipped in
the spec, which are not useful to anyone but the person who wrote them.

`context.spend_ceiling_cents` is still present in the spec and still totaled
by plan_summary for the reconcile report, but it is informational only as of
v1.0.2. It does not gate anything. Edit `capabilities.max_spend_cents` if you
want to change what actually stops a run.

## Real test results

Both runs below are actual output from staging and applying the v1.0.2 spec
against Stripe test mode, not a description of intended behavior.

**Success path.** Five clean rows in, two rejected by validate, two removed
by dedup, one charge sent, no manual gate anywhere in the spec:

```
outcome: COMPLETED
taken: [validate, dedup, plan_summary, charge, reconcile]
charge.status: succeeded
charge.id: pi_3TyVZ6IiIXjQdCon0luTy1Bz
charge.amount: 7900 (usd)
reconcile: { intended: 1, landed: 1, difference: 0, blocked_by_cap: false }
workflow_receipt: outcome COMPLETED, signed true
```

**Platform spend cap blocks the run natively, zero calls to Stripe.** Same
spec, `capabilities.max_spend_cents` set to 100 against a real planned total
of 7900. There is no cond node on charge to trip. The block is the platform's
own runtime guard:

```
outcome: ROLLED_BACK
error: SpendCapExceeded('node charge[0] would spend 7900 cents; cumulative 7900 > max_spend_cents=100')
taken: [validate, dedup, plan_summary]
workflow_receipt: signed true, compensated true
```

The receipt is signed either way. A refused run still produces a legitimate,
offline-verifiable audit record, it is just shaped as a rollback with an
error instead of a clean skip. See TESTING.md for the full transcripts.

## Three things worth knowing before you edit this spec

**The $100 per charge cap comes from RailCall's own Stripe primitive, not
from Stripe.** `stripe_charge_create` refuses any `amount_cents` above 10000
before it makes a request. A client row above $100 fails locally, not mid
batch on Stripe's side.

**As of station v0.39, `capabilities.max_spend_cents` is enforced natively at
run time for a node inside a `for_each`.** The engine tracks cumulative spend
across iterations, reading each iteration's actual spend from the node's
receipt first, then its resolved output, then a re-estimate of the args,
whichever is highest, and raises `SpendCapExceeded` to roll back the whole
run once an iteration would push spend past the declared ceiling. This
workflow's own `cond` gate on the charge node, present through v1.0.1, is
gone as of v1.0.2. It was a real workaround for a real platform gap, not a
defensive habit kept out of caution, and it stopped being necessary once the
platform closed that gap. Staging still does not fan out a `for_each`, so
the staged plan still shows `for_each_unbounded` and a plan-time spend
estimate of 0 for this node, that part of the platform has not changed. The
run-time guard is what matters, and it is verified above against this exact
spec, not assumed from a changelog entry.

**`for_each` is only evaluated when the workflow runs, not during staging.**
A binding typo in that field passes staging with no warning and only breaks,
or silently produces nothing, once the workflow runs for real. If you edit
the `charge` node, test the change with a live run against Stripe test mode,
not just a stage.

**There is no bridge between RailCall's module system and its workflow
system, checked again against station v0.40.** A workflow effect node
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
output: the success path with a real Stripe test mode PaymentIntent, the
platform's native spend cap blocking a run with zero calls to Stripe, a row
with no customer id being rejected, and a duplicate row being caught.

## License

MIT
