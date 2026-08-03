# Retainer Billing Run

A RailCall workflow for monthly retainer billing. An agency or small SaaS with
a fixed client list runs this once a period. Five nodes take the run from a
raw client list to sent invoices: validate, dedup, plan_summary, charge,
reconcile.

- validate rejects any row with no email, no amount, or a non-positive
  amount, with a reason per rejected row.
- dedup removes a client that appears twice in one export and a client
  already present in already_billed for this period, since billing someone
  twice is the failure that actually hurts a retainer run.
- plan_summary totals the real planned spend across the whole batch before
  anything is charged. It is a reporting node, not a gate.
- charge calls `stripe_billing_bill_client` from the `dave/stripe-invoicing`
  module — find-or-create customer, draft invoice, finalize, and send, four
  Stripe calls behind one airlock approval per row. As of station v0.42,
  installed module commands are first-class workflow effect nodes.
- reconcile compares what was intended against what actually landed.

Demo video: https://youtu.be/jHipCIVVhqI

## Install

```
railcall market install dave/retainer-billing-run
```

Also install the module this workflow depends on:

```
railcall market install dave/stripe-invoicing
```

## AI companion commands (v1.3.0, station v0.45)

`dave/stripe-invoicing` v1.2.3 ships three AI commands that pair well with
this workflow. Run them before or after the billing run as needed.

`stripe.billing.invoice_description_generate` — generates a professional
line-item description from service name, period, and client name.

`stripe.billing.dunning_message_draft` — drafts a follow-up email for any
invoice that surfaces as overdue in `aging_report`. Inputs: invoice_id,
customer_email, days_overdue, tone (polite / firm / urgent).

`stripe.billing.client_summary_insight` — synthesizes a plain-English
account insight from `customer_summary` output. Useful for account reviews
before a billing run.

All three LLM calls are governed via `station.llm.complete()` with a signed
egress receipt per call. Requires a Groq API key in the vault:

```
keys.local.json: {"groq": {"GROQ_API_KEY": "gsk_..."}}
```

## Configure before running

The published spec ships with anonymous placeholder data so it can stage and
run out of the box. Replace it with your own before using it for real:

- `context.clients`: your client list, each row `{ "email": "...", "amount_cents": ... }`.
- `context.already_billed`: emails already billed for the current period, so a rerun does not double charge.
- `context.billing_period`: a label for the current period, for example `"2026-08"`.
- `capabilities.max_spend_cents`: the total you are willing to charge in one run. As of station v0.39 this is the field that actually stops the run, enforced by the platform itself, not by this spec.

Without this step the workflow only knows about the example rows shipped in
the spec, which are not useful to anyone but the person who wrote them.

`context.spend_ceiling_cents` is still present in the spec and still totaled
by plan_summary for the reconcile report, but it is informational only. It
does not gate anything. Edit `capabilities.max_spend_cents` if you want to
change what actually stops a run.

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

**As of station v0.42, module commands are first-class workflow effect nodes.**
The charge node uses `stripe_billing_bill_client` (action_id format:
`provider_verb`) from the `dave/stripe-invoicing` module. This means billing
is governed: find-or-create customer, draft invoice, finalize, and send — four
Stripe calls behind one airlock approval, with a Stripe Idempotency-Key
derived from the payload hash on every write.

**As of station v0.39, `capabilities.max_spend_cents` is enforced natively at
run time for a node inside a `for_each`.** The engine tracks cumulative spend
across iterations, reading each iteration's actual spend from the node's
receipt first, then its resolved output, then a re-estimate of the args,
whichever is highest, and raises `SpendCapExceeded` to roll back the whole
run once an iteration would push spend past the declared ceiling. This
workflow's own `cond` gate on the charge node, present through v1.0.1, is
gone as of v1.0.2. The run-time guard is what matters, and it is verified
above against this exact spec, not assumed from a changelog entry.

**`for_each` is only evaluated when the workflow runs, not during staging.**
A binding typo in that field passes staging with no warning and only breaks,
or silently produces nothing, once the workflow runs for real. If you edit
the `charge` node, test the change with a live run against Stripe test mode,
not just a stage.

## Testing

See [TESTING.md](TESTING.md) for the actual commands run and their real
output: the success path with a real Stripe test mode PaymentIntent, the
platform's native spend cap blocking a run with zero calls to Stripe, a row
with no email being rejected, and a duplicate row being caught.

## License

MIT
