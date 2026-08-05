# Testing report — Retainer Billing Run

Workflow ID: `dave/retainer-billing-run`

Target runtime: RailCall Station v0.55
Source: `workflow/spec.json`

Workflow version: `1.4.0`. The tested source has `updated: 2026-08-05T00:00:00Z`.

Status legend:

- ✅ **Verified** — directly verified through Station, DAG/MCP planning, Studio inspection, static validation, or a provider-independent runtime policy path.
- ⚠ **Blocked by Station** — execution reached the official provider boundary but Station v0.55 blocked the allowed hostname after DNS resolution.
- 🧪 **Fixture Only** — transform, advisory, effect output, or reconciliation behavior was exercised with controlled fixture data; it is not a live-provider pass.

No live Stripe success, live Groq success, or successful provider receipt is claimed.

## Source and runtime consistency

| Check | Status | Result |
|---|---|---|
| Workflow JSON | ✅ Verified | `workflow/spec.json` parses successfully. |
| Identity | ✅ Verified | ID `dave/retainer-billing-run`; title `Retainer Billing Run`. |
| Legacy compatibility | ✅ Verified | Five top-level canvas nodes and four valid edges remain present. |
| `engine_spec` | ✅ Verified | Eight unique DAG nodes with valid parents and bindings. |
| Runtime copies | ✅ Verified | Source and both Station workflow copies were re-synchronized after the charge-contract correction; all three have the same SHA-256. |
| Action resolution | ✅ Verified | `stripe_billing_bill_client` and `groq_billing_billing_anomaly_detect` resolved in Station. |
| Capabilities | ✅ Verified | Providers Stripe/Groq, `max_spend_cents: 50000`, `allow_irreversible: true`. |

The eight `engine_spec` nodes verified against the source are:

| Node | Type | Branch |
|---|---|---|
| `validate` | transform | prepare |
| `dedup` | transform | prepare |
| `plan_summary` | transform | prepare |
| `advisory_switch` | transform | advisory |
| `anomaly_payload` | transform | advisory |
| `anomaly_preflight` | effect | advisory |
| `charge` | effect | billing |
| `reconcile` | transform | billing |

## Default fixture result

The shipped anonymous fixture produces these provider-independent transform results:

| Stage | Result | Status |
|---|---|---|
| Input | Five rows | 🧪 Fixture Only |
| `validate` | Three billable; two rejected | 🧪 Fixture Only |
| `dedup` | One clean; two skipped | 🧪 Fixture Only |
| `plan_summary` | One billable, two skipped, two rejected, total 7,900 cents, within cap | 🧪 Fixture Only |
| Advisory | Disabled by default | ✅ Verified |
| Charge preparation | One clean row resolves to the approval-controlled effect | ✅ Verified |
| Charge input contract | Clean row supplies `email`, `amount_cents`, and deterministic non-empty `description`; real handler validation passes with fixture transport | 🧪 Fixture Only |
| Provider completion | No Stripe success claimed | ⚠ Blocked by Station |

## Validation

`validate` was exercised with valid rows and the following negative fixtures:

- Missing email.
- Missing amount.
- Zero amount.
- Negative amount.
- Float amount.
- Boolean amount.
- Input row that is not an object.
- Empty client list.

The transform rejects invalid rows before an effect is prepared. These checks are 🧪 fixture results because they require no provider.

## Dedup and `already_billed`

| Scenario | Status | Result |
|---|---|---|
| Duplicate email within one batch | 🧪 Fixture Only | Second occurrence is skipped as `duplicate_in_export`. |
| Email listed in `already_billed` | 🧪 Fixture Only | Row is skipped as `already_billed_this_period`. |
| Identifier consistency | ✅ Verified | Client rows, `seen`, and `already_billed` all compare email values. |
| Previously billed client reaches charge | ✅ Verified | No; the row is absent from `nodes.dedup.clean`. |

This verifies the earlier customer-ID-versus-email mismatch is no longer present in the current workflow structure.

## Plan Summary

| Check | Status | Result |
|---|---|---|
| Counts use validate/dedup outputs | 🧪 Fixture Only | Billable, skipped, and rejected counts match the fixture. |
| Planned total | 🧪 Fixture Only | Only clean rows contribute to `total_cents`. |
| `within_cap` and `proceed` | 🧪 Fixture Only | Computed deterministically from clean rows and `spend_ceiling_cents`. |
| Charge gate | ✅ Verified | `proceed` is not a `cond` on `charge`; native Station spend policy is the financial gate. |

## Optional anomaly advisory

| Check | Status | Result |
|---|---|---|
| Disabled by default | ✅ Verified | `enable_anomaly_advisory` defaults to the string `false`. |
| Enable switch | 🧪 Fixture Only | Accepted true-like values enable the advisory branch. |
| Action ID and provider | ✅ Verified | `groq_billing_billing_anomaly_detect`, provider `groq`. |
| Read-only decision support | ✅ Verified | The resolved module command is `read`, `side_effects: none`, and returns `decision_support_only`. |
| Charge dependency | ✅ Verified | `charge` is parented by `plan_summary`, not by an advisory node, and has no advisory condition. |
| Payload anonymization | 🧪 Fixture Only | Payload contains opaque `batch-N` references and aggregate amounts/counts only. |
| Email, name, customer ID, invoice ID | ✅ Verified | None are copied by `anomaly_payload`. |
| Invalid AI output | 🧪 Fixture Only | Module validation rejects invalid structured output fail-closed. |
| Live Groq response | ⚠ Blocked by Station | No provider success or egress-provider receipt is claimed. |

An advisory failure does not authorize, deny, or alter a charge. AI remains advisory only and never becomes the financial gate.

## Approval and governance

| Check | Status | Result |
|---|---|---|
| Charge action | ✅ Verified | `stripe_billing_bill_client`, provider `stripe`. |
| Human approval | ✅ Verified | DAG policy resolves charge as an approval-controlled external effect. |
| Fan-out | ✅ Verified | `for_each` binds to `{{nodes.dedup.clean}}`; args bind to `{{ctx.item}}`. |
| Retry policy | ✅ Verified | Maximum three attempts with two-second backoff. |
| Idempotency | ✅ Verified | Resolved module action uses the approved payload hash for Stripe idempotency. |
| Live Execution Policy | ✅ Verified | Execution remains fail-closed; planning alone does not run provider effects. |
| Direct provider bypass | ✅ Verified | No workflow node calls Stripe directly outside the governed module action. |
| Module input contract | 🧪 Fixture Only | Calling the real `stripe.billing.bill_client` handler with a clean row passes required-field validation; fixture request forms retain `Retainer billing for 2026-08`. |

## Spend cap and rollback

| Scenario | Status | Result |
|---|---|---|
| Planned total below native cap | 🧪 Fixture Only | Workflow may proceed to the approval/provider boundary. |
| Native cap below planned effect | ✅ Verified | Station stops before provider execution. |
| Workflow outcome on cap violation | ✅ Verified | Runtime reports a rolled-back outcome. |
| Provider call during blocked-cap path | ✅ Verified | No Stripe provider execution occurs. |
| Advisory bypasses cap | ✅ Verified | No; advisory output is not part of the spend policy or charge condition. |
| Provider receipt for blocked-cap path | Not claimed | Rollback evidence is a workflow governance result, not a successful provider receipt. |

## Reconcile

| Check | Status | Result |
|---|---|---|
| Intended count | 🧪 Fixture Only | Read from `plan_summary.billable_count`. |
| Landed count | 🧪 Fixture Only | Counts only fixture outputs containing invoice/effect IDs. |
| Difference | 🧪 Fixture Only | Calculated as intended minus landed. |
| Live reconciliation | ⚠ Blocked by Station | Cannot be proven against a completed Stripe provider effect in Station v0.55. |

Reconcile is not allowed to infer or manufacture a landed effect when provider output is missing.

## MCP DAG

| Check | Status | Result |
|---|---|---|
| `railcall_workflows_dag_list` | ✅ Verified | Lists `retainer-billing-run`. |
| `railcall_workflow_dag_plan` | ✅ Verified | Accepts the workflow ID and returns the DAG plan. |
| Plan node count | ✅ Verified | Eight `engine_spec` nodes. |
| Plan inspection | ✅ Verified | Exposes branches, context, effect nodes, provider requirements, and approval blast radius without executing providers. |
| MCP live run | Not claimed | Not required while the official sandbox blocker remains. |

## Studio

| Check | Status | Result |
|---|---|---|
| Workflow listed | ✅ Verified | Appears as `Retainer Billing Run` in Studio Workflows. |
| Run Button | ✅ Verified | Present for the `engine_spec` workflow. |
| Context Form | ✅ Verified | Exposes clients, already billed, billing period, summary ceiling, advisory switch, and portfolio baseline. |
| Plan/dry inspection | ✅ Verified | Available without claiming a provider run. |
| Action resolution error | ✅ Verified | No unresolved action in the current DAG plan. |

## Charge input-contract regression

The Step 8 consistency audit found that `dedup` originally omitted `description`, even though `charge` passes each clean row unchanged to `stripe.billing.bill_client` and the module requires a non-empty description. The corrective regression now verifies:

1. Legacy and `engine_spec` dedup both emit `email`, `amount_cents`, `description`, and `period`.
2. Both rails produce the same `Retainer billing for <billing_period>` value.
3. An empty period uses the deterministic, non-empty fallback `Retainer billing`.
4. Multiple clean rows each receive the same period-derived description.
5. The real module handler accepts the resulting row and carries the description into invoice and line-item preparation under fixture transport.

This is 🧪 **Fixture Only** evidence: it proves the workflow-to-module contract without claiming live Stripe execution.

## Station blocker

Live Stripe and Groq calls are currently ⚠ **Blocked by Station**:

1. The module manifest allows the provider hostname.
2. Station v0.55 resolves that hostname to an IP address.
3. The runtime compares the resolved IP against the hostname allowlist.
4. The provider call is rejected before Stripe or Groq can complete it.

The workflow, DAG, MCP plan, Studio surfaces, validation, dedup, advisory payload, approval boundary, spend policy, and action resolution are still testable. The platform blocker is not recorded as a module defect and is not converted into a fixture-based claim of live success.

## Step 4 conclusion

- ✅ Verified: workflow identity, legacy compatibility, eight-node DAG, action resolution, Studio Run Button, Context Form, MCP list/plan, governance, approval boundary, native spend-cap stop, and rollback behavior.
- 🧪 Fixture Only: transform outputs, anonymized advisory results, the corrected charge-input contract, and reconciliation outputs.
- ⚠ Blocked by Station: live Stripe completion, live Groq completion, live provider validation, and provider receipt success.
- No secret, credential, approval token, real customer data, or provider success identifier is included in this report.
