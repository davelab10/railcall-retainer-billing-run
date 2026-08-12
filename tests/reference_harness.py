"""Project-owned reference contract for the v1.7 billing lifecycle.

This file is intentionally test-only.  It models the contract we expect from a
future native Station Team execution path; it is not a second governance or
approval implementation and is never imported by the production workflow.
"""

import hashlib
import json


def canonical_plan(period, candidates, billing_run_id):
    rows = []
    for row in candidates:
        rows.append({
            "email": str(row["email"]).strip().lower(),
            "customer_id": str(row.get("customer_id") or ""),
            "amount_cents": int(row["amount_cents"]),
            "currency": str(row.get("currency") or "usd").lower(),
            "description": str(row.get("description") or ""),
            "period": str(row.get("period") or period),
            "billing_run_id": str(billing_run_id),
        })
    payload = {
        "billing_period": str(period),
        "billing_run_id": str(billing_run_id),
        "candidates": rows,
        "candidate_count": len(rows),
        "total_planned_cents": sum(row["amount_cents"] for row in rows),
    }
    encoded = json.dumps(payload)
    return payload, "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class BillingContract:
    """Small deterministic model used only to test expected lifecycle rules."""

    def prepare(self, period, candidates, billing_run_id):
        payload, plan_hash = canonical_plan(period, candidates, billing_run_id)
        return {"plan": payload, "plan_hash": plan_hash, "billing_run_id": billing_run_id}

    def approve(self, prepared, *, plan_hash, quorum=True):
        if not quorum:
            raise ValueError("approval_required")
        if plan_hash != prepared["plan_hash"]:
            raise ValueError("plan_hash_mismatch")
        return {"approved_plan_hash": plan_hash, "billing_run_id": prepared["billing_run_id"]}

    def execute(self, prepared, approval, outcomes):
        if approval.get("approved_plan_hash") != prepared["plan_hash"]:
            raise ValueError("approved_plan_hash_mismatch")
        if approval.get("billing_run_id") != prepared["billing_run_id"]:
            raise ValueError("billing_run_id_mismatch")
        expected = prepared["plan"]["candidates"]
        if len(outcomes) != len(expected):
            raise ValueError("outcome_count_mismatch")
        return [
            {**candidate, "state": outcome["state"], "provider_id": outcome.get("provider_id"),
             "reason": outcome.get("reason", "")}
            for candidate, outcome in zip(expected, outcomes)
        ]

    def reconcile(self, prepared, results):
        counts = {state: 0 for state in ("landed", "skipped", "failed", "unknown")}
        landed_total = 0
        unresolved = 0
        for row in results:
            state = row["state"]
            counts[state] += 1
            if state == "landed":
                landed_total += row["amount_cents"]
            if state in ("failed", "unknown"):
                unresolved += row["amount_cents"]
        return {
            "planned_count": len(results),
            "planned_total_cents": prepared["plan"]["total_planned_cents"],
            "landed_count": counts["landed"],
            "skipped_count": counts["skipped"],
            "failed_count": counts["failed"],
            "unknown_count": counts["unknown"],
            "landed_total_cents": landed_total,
            "unresolved_amount_cents": unresolved,
            "billing_run_id": prepared["billing_run_id"],
            "plan_hash": prepared["plan_hash"],
            "settlement_status": "settled" if unresolved == 0 else "unresolved",
        }

    def recover(self, prepared, previous_results, provider_landed_ids):
        """Build a safe retry set from provider truth, never local status alone."""
        retry = []
        for row in previous_results:
            if row.get("provider_id") in provider_landed_ids:
                continue
            if row["state"] in ("failed", "unknown"):
                retry.append(row)
        return retry
