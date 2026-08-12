import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / "workflow"
WORKFLOW_PATH = WORKFLOW_DIR / "spec.json"
MODULE_DIR = ROOT / "module"
MODULE_MANIFEST_PATH = MODULE_DIR / "module.json"
MODULE_HANDLER_PATH = MODULE_DIR / "handlers" / "handler.py"
STATION_ROOT = pathlib.Path.home() / ".railcall" / "station"
STATION_WORKBENCH = STATION_ROOT / "workbench"

sys.path.insert(0, str(STATION_ROOT))
sys.path.insert(0, str(STATION_WORKBENCH))
from workbench import workflow_engine as ENGINE
from workbench import workflow_transform as TRANSFORM
from primitives import incremental_contract
from primitives import incremental_runtime
from workbench.routes import modules as MODULE_ROUTES

from reference_harness import BillingContract, canonical_plan


def _load_handler():
    spec = importlib.util.spec_from_file_location("workflow_test_module_handler_v140", MODULE_HANDLER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HANDLER = _load_handler()


def _run_transform(node, value):
    return TRANSFORM.run_transform(node["code"], value)["output"]


class _Signing:
    def sign_block(self, value):
        return "test-signature"


class WorkflowV170Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.engine = cls.workflow["engine_spec"]
        cls.nodes = cls.engine["nodes"]
        cls.by_id = {node["id"]: node for node in cls.nodes}
        cls.module = json.loads(MODULE_MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.module_commands = {command["id"]: command for command in cls.module["commands"]}

    def test_identity_version_dependency_and_phase_counts(self):
        self.assertEqual(self.workflow["id"], "dave/retainer-billing-run")
        self.assertEqual(self.workflow["version"], "1.7.0")
        self.assertEqual(
            self.workflow["module_dependency"],
            {"id": "dave/stripe-invoicing", "minimum_version": "1.4.0"},
        )
        self.assertEqual(len(self.workflow["nodes"]), 5)
        self.assertEqual(len(self.workflow["edges"]), 4)
        self.assertEqual(len(self.nodes), 13)
        self.assertEqual(
            [node["id"] for node in self.nodes],
            [
                "validate", "invoice_history", "dedup", "plan_summary",
                "review_summary", "approval_tier", "execution_guard",
                "advisory_switch", "anomaly_payload", "charge", "reconcile",
                "recovery_plan", "settle",
            ],
        )

    def test_incremental_source_uses_station_owned_state(self):
        source = self.by_id["invoice_history"]
        command = self.module_commands["stripe.billing.invoice_list"]
        provider, verb = MODULE_ROUTES._module_provider_verb(
            command["id"], command, "dave-stripe-invoicing"
        )
        self.assertEqual(provider + "_" + verb, "stripe_billing_invoice_list")
        self.assertEqual(source["action_id"], provider + "_" + verb)
        self.assertEqual(source["type"], "effect")
        self.assertEqual(source["args"], {"limit": 100})
        self.assertNotIn("since", source["args"])
        self.assertNotIn("exclude_invoice_ids", source["args"])
        self.assertEqual(
            self.by_id["dedup"]["input_from"]["invoice_history"],
            "{{nodes.invoice_history._}}",
        )
        self.assertNotIn("watermark_store", json.dumps(self.nodes))
        self.assertNotIn("cursor_file", json.dumps(self.nodes))

    def test_schedule_injects_since_without_mutating_source(self):
        command = self.module_commands["stripe.billing.invoice_list"]
        contract = incremental_contract.parse(command)
        action_id = self.by_id["invoice_history"]["action_id"]
        previous = incremental_runtime.INCREMENTAL_ACTIONS.get(action_id)
        incremental_runtime.register(
            action_id,
            module_slug="dave-stripe-invoicing",
            module_id=self.module["id"],
            command_id=command["id"],
            contract=contract,
        )
        try:
            with tempfile.TemporaryDirectory() as workspace:
                prepared, plan = incremental_runtime.prepare(
                    self.engine,
                    ws=workspace,
                    schedule_id="schedule-retainer-billing",
                    now="2026-08-09T00:00:00Z",
                )
            source = next(node for node in prepared["nodes"] if node["id"] == "invoice_history")
            self.assertEqual(source["args"]["since"], "2026-08-08T23:55:00Z")
            self.assertEqual(source["args"]["exclude_invoice_ids"], [])
            self.assertEqual(plan["schedule_id"], "schedule-retainer-billing")
            self.assertEqual(self.by_id["invoice_history"]["args"], {"limit": 100})
        finally:
            if previous is None:
                incremental_runtime.INCREMENTAL_ACTIONS.pop(action_id, None)
            else:
                incremental_runtime.INCREMENTAL_ACTIONS[action_id] = previous

    def test_validation_normalizes_identity_and_rejects_invalid_amounts(self):
        output = _run_transform(
            self.by_id["validate"],
            [
                {"email": " Person@Example.TEST ", "customer_id": "cus_1", "amount_cents": 100},
                {"email": "", "amount_cents": 100},
                {"email": "bool@example.test", "amount_cents": True},
                {"email": "float@example.test", "amount_cents": 1.5},
                {"email": "zero@example.test", "amount_cents": 0},
                "not-an-object",
            ],
        )
        self.assertEqual(
            output["billable"],
            [{"email": "person@example.test", "customer_id": "cus_1", "amount_cents": 100}],
        )
        self.assertEqual(len(output["rejected"]), 5)

    def test_dedup_uses_relevant_history_and_leaves_unrelated_history(self):
        validated = {
            "billable": [
                {"email": "history@example.test", "customer_id": "cus_h", "amount_cents": 300},
                {"email": "unrelated@example.test", "customer_id": "cus_u", "amount_cents": 400},
            ],
            "rejected": [],
        }
        history = {
            "truncated": False,
            "invoices": [
                {
                    "invoice_id": "in_match",
                    "customer_email": "HISTORY@example.test",
                    "amount_due_cents": 300,
                    "description": "Retainer billing for 2026-08",
                    "created_at": "2026-08-02T01:00:00Z",
                    "status": "open",
                },
                {
                    "invoice_id": "in_one_off",
                    "customer_email": "unrelated@example.test",
                    "amount_due_cents": 400,
                    "description": "One-off consulting",
                    "created_at": "2026-08-02T01:00:00Z",
                    "status": "open",
                },
            ],
        }
        output = _run_transform(
            self.by_id["dedup"],
            {
                "validate_output": validated,
                "invoice_history": history,
                "already_billed": [],
                "period": "2026-08",
            },
        )
        self.assertEqual([row["email"] for row in output["clean"]], ["unrelated@example.test"])
        self.assertIn("stripe_history_same_period", [row["reason"] for row in output["skipped"]])
        self.assertEqual(output["history_invoices_considered"], 1)

    def test_truncated_history_is_fail_closed(self):
        output = _run_transform(
            self.by_id["dedup"],
            {
                "validate_output": {
                    "billable": [{"email": "candidate@example.test", "amount_cents": 500}],
                    "rejected": [],
                },
                "invoice_history": {"invoices": [], "truncated": True},
                "already_billed": [],
                "period": "2026-08",
            },
        )
        self.assertEqual(output["clean"], [])
        self.assertEqual(output["skipped"][0]["reason"], "invoice_history_truncated_fail_closed")

    def test_plan_hash_and_billing_run_id_are_deterministic(self):
        value = {
            "dedup_output": {
                "clean": [{"email": "a@example.test", "customer_id": "cus_a", "amount_cents": 700, "description": "Retainer billing for 2026-08", "period": "2026-08"}],
                "skipped": [],
            },
            "validate_output": {"rejected": []},
            "billing_period": "2026-08",
            "billing_run_id": "run-001",
        }
        first = _run_transform(self.by_id["plan_summary"], value)
        second = _run_transform(self.by_id["plan_summary"], value)
        changed = _run_transform(self.by_id["plan_summary"], {**value, "billing_period": "2026-09"})
        self.assertEqual(first["plan_hash"], second["plan_hash"])
        self.assertNotEqual(first["plan_hash"], changed["plan_hash"])
        self.assertEqual(first["billing_run_id"], "run-001")
        self.assertEqual(first["canonical_plan"]["candidate_count"], 1)
        _, reference_hash = canonical_plan(
            "2026-08",
            [{"email": "a@example.test", "customer_id": "cus_a", "amount_cents": 700, "description": "Retainer billing for 2026-08", "period": "2026-08"}],
            "run-001",
        )
        self.assertEqual(first["plan_hash"], reference_hash)

    def test_review_and_configurable_approval_tier(self):
        plan = {
            "billing_run_id": "run-001",
            "plan_hash": "fnv1a64:test",
            "billing_period": "2026-08",
            "candidate_count": 1,
            "total_planned_cents": 12000,
            "candidates": [{"email": "new@example.test", "customer_id": "cus_new", "amount_cents": 12000}],
            "skipped": [],
            "rejected": [],
        }
        review = _run_transform(
            self.by_id["review_summary"],
            {
                "plan_summary_output": plan,
                "anomaly_config": {"large_batch_total_cents": 10000, "high_value_cents": 25000},
                "known_customer_emails": [],
                "prior_amount_by_email": {},
                "prior_customer_ids": {"new@example.test": "cus_old"},
            },
        )
        self.assertTrue(review["review_required"])
        self.assertIn("large_batch_total", [x["kind"] for x in review["anomalies"]])
        self.assertIn("changed_customer_mapping", [x["kind"] for x in review["anomalies"]])
        tier = _run_transform(
            self.by_id["approval_tier"],
            {
                "plan_summary_output": plan,
                "review_output": review,
                "approval_tiers": {"small_batch_max_cents": 10000, "small_batch_quorum": 1, "large_batch_quorum": 3},
            },
        )
        self.assertEqual(tier["required_quorum"], 3)
        self.assertTrue(tier["station_authority"])
        self.assertEqual(tier["approval_status"], "not_asserted_by_workflow")
        self.assertNotIn("approver", tier)

    def test_execution_guard_requires_matching_supplied_hash(self):
        plan = {"plan_hash": "fnv1a64:expected", "billing_run_id": "run-001"}
        approval = {"required_quorum": 2}
        good = _run_transform(
            self.by_id["execution_guard"],
            {"plan_summary_output": plan, "approval_output": approval, "review_output": {"blocking_anomaly_count": 0}, "approved_plan_hash": "fnv1a64:expected"},
        )
        self.assertTrue(good["execution_ready"])
        self.assertTrue(good["station_approval_required"])
        with self.assertRaisesRegex(RuntimeError, "execution_guard blocked"):
            _run_transform(
                self.by_id["execution_guard"],
                {"plan_summary_output": plan, "approval_output": approval, "review_output": {"blocking_anomaly_count": 0}, "approved_plan_hash": "fnv1a64:changed"},
            )
        with self.assertRaisesRegex(RuntimeError, "execution_guard blocked"):
            _run_transform(
                self.by_id["execution_guard"],
                {"plan_summary_output": plan, "approval_output": approval, "review_output": {"blocking_anomaly_count": 1}, "approved_plan_hash": "fnv1a64:expected"},
            )

    def _registered_actions(self, invoice_handler, charge_handler):
        source_command = self.module_commands["stripe.billing.invoice_list"]
        charge_command = self.module_commands["stripe.billing.bill_client"]
        source = MODULE_ROUTES._synth_module_integration(
            source_command["id"], source_command, invoice_handler, "dave-stripe-invoicing"
        )
        charge = MODULE_ROUTES._synth_module_integration(
            charge_command["id"], charge_command, charge_handler, "dave-stripe-invoicing"
        )
        return {"stripe_billing_invoice_list": source, "stripe_billing_bill_client": charge}

    def test_action_resolution_and_station_plan_are_stable(self):
        actions = self._registered_actions(
            HANDLER.stripe_billing_invoice_list,
            HANDLER.stripe_billing_bill_client,
        )
        old = {key: ENGINE.R.ACTIONS.get(key) for key in actions}
        ENGINE.R.ACTIONS.update(actions)
        engine = json.loads(json.dumps(self.engine))
        engine["context"].update({
            "clients": [{"email": "plan@example.test", "customer_id": "cus_plan", "amount_cents": 700}],
            "already_billed": [],
            "billing_period": "2026-08",
            "billing_run_id": "run-plan-001",
            "known_customer_emails": ["plan@example.test"],
            "prior_amount_by_email": {"plan@example.test": 700},
            "prior_customer_ids": {"plan@example.test": "cus_plan"},
        })
        try:
            first = ENGINE.plan_workflow(engine, signing=_Signing())
            second = ENGINE.plan_workflow(engine, signing=_Signing())
        finally:
            for key, value in old.items():
                if value is None:
                    ENGINE.R.ACTIONS.pop(key, None)
                else:
                    ENGINE.R.ACTIONS[key] = value
        self.assertEqual(first["workflow_root"], second["workflow_root"])
        self.assertEqual(first["blast_radius"]["node_count"], 13)
        self.assertIn("stripe", first["blast_radius"]["systems_touched"])
        self.assertIn("charge:stripe_billing_bill_client", first["blast_radius"]["irreversible"])
        self.assertNotIn("invoice_history:stripe_billing_invoice_list", first["blast_radius"]["irreversible"])
        self.assertEqual(first["blast_radius"]["requires"], "require_human")

    def test_history_failure_prevents_charge(self):
        calls = {"history": 0, "charge": 0}

        def fail_history(inputs, stamp):
            calls["history"] += 1
            raise RuntimeError("invoice history unavailable")

        def charge_should_not_run(inputs, stamp):
            calls["charge"] += 1
            return {"invoice_id": "in_should_not_exist"}, None

        actions = self._registered_actions(fail_history, charge_should_not_run)
        old = {key: ENGINE.R.ACTIONS.get(key) for key in actions}
        ENGINE.R.ACTIONS.update(actions)
        no_sleep = mock.patch.object(ENGINE.time, "sleep", return_value=None)
        no_client = mock.patch.object(ENGINE, "_client", return_value=(None, "mock"))
        try:
            with tempfile.TemporaryDirectory() as workspace, no_sleep, no_client:
                result = ENGINE.run_workflow(self.engine, ws=workspace, signing=_Signing(), allow_live_effects=False)
        finally:
            for key, value in old.items():
                if value is None:
                    ENGINE.R.ACTIONS.pop(key, None)
                else:
                    ENGINE.R.ACTIONS[key] = value
        self.assertEqual(result["outcome"], "ROLLED_BACK")
        self.assertEqual(calls["charge"], 0)

    def test_runtime_history_dedup_and_exact_plan_reach_charge(self):
        calls = {"charge": []}

        def invoice_history(inputs, stamp):
            return {
                "invoices": [
                    {
                        "invoice_id": "in_relevant",
                        "customer_email": "history@example.test",
                        "amount_due_cents": 300,
                        "description": "Retainer billing for 2026-08",
                        "created_at": "2026-08-02T01:00:00Z",
                        "status": "open",
                    },
                    {
                        "invoice_id": "in_one_off",
                        "customer_email": "unrelated@example.test",
                        "amount_due_cents": 400,
                        "description": "One-off consulting",
                        "created_at": "2026-08-02T01:00:00Z",
                        "status": "open",
                    },
                ],
                "truncated": False,
            }, None

        def charge(inputs, stamp):
            calls["charge"].append(inputs)
            return {"invoice_id": "in_charge_" + str(len(calls["charge"])), "amount_due_cents": inputs["amount_cents"]}, None

        actions = self._registered_actions(invoice_history, charge)
        old = {key: ENGINE.R.ACTIONS.get(key) for key in actions}
        ENGINE.R.ACTIONS.update(actions)
        engine = json.loads(json.dumps(self.engine))
        engine["context"].update({
            "clients": [
                {"email": "history@example.test", "amount_cents": 300},
                {"email": "unrelated@example.test", "amount_cents": 400},
                {"email": "operator@example.test", "amount_cents": 200},
            ],
            "already_billed": [],
            "billing_period": "2026-08",
            "billing_run_id": "run-runtime-001",
        })
        no_client = mock.patch.object(ENGINE, "_client", return_value=(None, "mock"))
        try:
            with tempfile.TemporaryDirectory() as workspace, no_client:
                result = ENGINE.run_workflow(engine, ws=workspace, signing=_Signing(), allow_live_effects=False)
        finally:
            for key, value in old.items():
                if value is None:
                    ENGINE.R.ACTIONS.pop(key, None)
                else:
                    ENGINE.R.ACTIONS[key] = value
        self.assertEqual(result["outcome"], "COMPLETED")
        self.assertEqual([row["email"] for row in calls["charge"]], ["unrelated@example.test", "operator@example.test"])
        self.assertNotIn("history@example.test", [row["email"] for row in calls["charge"]])
        self.assertEqual(result["outputs"]["reconcile"]["output"]["landed_count"], 2)
        self.assertEqual(result["outputs"]["settle"]["output"]["settlement_status"], "settled")
        for row in calls["charge"]:
            self.assertEqual(row["billing_run_id"], "run-runtime-001")
            self.assertTrue(row["plan_hash"].startswith("sha256:"))

    def test_single_candidate_charge_reconciles_direct_fanout_output(self):
        calls = {"charge": 0}

        def invoice_history(inputs, stamp):
            return {"invoices": [], "truncated": False}, None

        def charge(inputs, stamp):
            calls["charge"] += 1
            return {"invoice_id": "in_single_candidate", "amount_due_cents": inputs["amount_cents"]}, None

        actions = self._registered_actions(invoice_history, charge)
        old = {key: ENGINE.R.ACTIONS.get(key) for key in actions}
        ENGINE.R.ACTIONS.update(actions)
        engine = json.loads(json.dumps(self.engine))
        engine["context"].update({
            "clients": [{"email": "single@example.test", "amount_cents": 700}],
            "already_billed": [],
            "billing_period": "2026-08",
            "billing_run_id": "run-single-001",
        })
        no_client = mock.patch.object(ENGINE, "_client", return_value=(None, "mock"))
        try:
            with tempfile.TemporaryDirectory() as workspace, no_client:
                result = ENGINE.run_workflow(engine, ws=workspace, signing=_Signing(), allow_live_effects=False)
        finally:
            for key, value in old.items():
                if value is None:
                    ENGINE.R.ACTIONS.pop(key, None)
                else:
                    ENGINE.R.ACTIONS[key] = value
        self.assertEqual(result["outcome"], "COMPLETED")
        self.assertEqual(calls["charge"], 1)
        reconcile = result["outputs"]["reconcile"]["output"]
        self.assertEqual(reconcile["planned_count"], 1)
        self.assertEqual(reconcile["landed_count"], 1)
        self.assertEqual(reconcile["unknown_count"], 0)
        self.assertEqual(reconcile["settlement_status"], "settled")
        self.assertEqual(result["outputs"]["settle"]["output"]["settlement_status"], "settled")

    def test_execution_guard_block_is_not_reported_completed(self):
        calls = {"charge": 0}

        def invoice_history(inputs, stamp):
            return {"invoices": [], "truncated": False}, None

        def charge(inputs, stamp):
            calls["charge"] += 1
            return {"invoice_id": "in_should_not_exist"}, None

        actions = self._registered_actions(invoice_history, charge)
        old = {key: ENGINE.R.ACTIONS.get(key) for key in actions}
        ENGINE.R.ACTIONS.update(actions)
        engine = json.loads(json.dumps(self.engine))
        engine["context"].update({
            "clients": [{"email": "blocked@example.test", "amount_cents": 700}],
            "already_billed": [],
            "billing_period": "2026-08",
            "billing_run_id": "run-blocked-001",
            "approved_plan_hash": "sha256:wrong",
        })
        no_client = mock.patch.object(ENGINE, "_client", return_value=(None, "mock"))
        try:
            with tempfile.TemporaryDirectory() as workspace, no_client:
                result = ENGINE.run_workflow(engine, ws=workspace, signing=_Signing(), allow_live_effects=False)
        finally:
            for key, value in old.items():
                if value is None:
                    ENGINE.R.ACTIONS.pop(key, None)
                else:
                    ENGINE.R.ACTIONS[key] = value
        self.assertEqual(result["outcome"], "ROLLED_BACK")
        self.assertEqual(calls["charge"], 0)
        self.assertIn("execution_guard blocked", result.get("error", ""))

    def test_reference_harness_contract_covers_approval_recovery(self):
        contract = BillingContract()
        candidates = [
            {"email": "a@example.test", "customer_id": "cus_a", "amount_cents": 1000, "description": "Retainer billing for 2026-08", "period": "2026-08"},
            {"email": "b@example.test", "customer_id": "cus_b", "amount_cents": 2000, "description": "Retainer billing for 2026-08", "period": "2026-08"},
            {"email": "c@example.test", "customer_id": "cus_c", "amount_cents": 3000, "description": "Retainer billing for 2026-08", "period": "2026-08"},
        ]
        prepared = contract.prepare("2026-08", candidates, "run-001")
        with self.assertRaisesRegex(ValueError, "approval_required"):
            contract.approve(prepared, plan_hash=prepared["plan_hash"], quorum=False)
        with self.assertRaisesRegex(ValueError, "plan_hash_mismatch"):
            contract.approve(prepared, plan_hash="sha256:changed")
        approval = contract.approve(prepared, plan_hash=prepared["plan_hash"])
        results = contract.execute(
            prepared,
            approval,
            [
                {"state": "landed", "provider_id": "in_a"},
                {"state": "failed", "reason": "provider refused"},
                {"state": "unknown", "reason": "timeout"},
            ],
        )
        summary = contract.reconcile(prepared, results)
        self.assertEqual(summary["landed_count"], 1)
        self.assertEqual(summary["failed_count"], 1)
        self.assertEqual(summary["unknown_count"], 1)
        self.assertEqual(summary["settlement_status"], "unresolved")
        retry = contract.recover(prepared, results, {"in_a"})
        self.assertEqual([row["email"] for row in retry], ["b@example.test", "c@example.test"])
        changed_candidate = contract.prepare("2026-08", [candidates[0], {**candidates[1], "amount_cents": 2500}, candidates[2]], "run-001")
        self.assertNotEqual(prepared["plan_hash"], changed_candidate["plan_hash"])
        changed_period = contract.prepare("2026-09", candidates, "run-001")
        self.assertNotEqual(prepared["plan_hash"], changed_period["plan_hash"])
        changed_run = contract.prepare("2026-08", candidates, "run-002")
        with self.assertRaisesRegex(ValueError, "plan_hash_mismatch"):
            contract.approve(changed_run, plan_hash=prepared["plan_hash"])
        with self.assertRaisesRegex(ValueError, "billing_run_id_mismatch"):
            contract.execute(prepared, {**approval, "billing_run_id": "run-old"}, results)
        self.assertEqual(contract.reconcile(prepared, [{**results[0], "state": "skipped"}])["skipped_count"], 1)

    def test_no_new_workflow_json_was_created(self):
        self.assertEqual([path.name for path in WORKFLOW_DIR.glob("*.json")], ["spec.json"])


if __name__ == "__main__":
    unittest.main()
