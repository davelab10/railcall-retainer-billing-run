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


def _load_handler():
    spec = importlib.util.spec_from_file_location("workflow_test_module_handler", MODULE_HANDLER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HANDLER = _load_handler()


def _run_transform(node, value):
    return TRANSFORM.run_transform(node["code"], value)["output"]


class _Signing:
    def sign_block(self, value):
        return "test-signature"


class WorkflowV160Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.engine = cls.workflow["engine_spec"]
        cls.nodes = cls.engine["nodes"]
        cls.by_id = {node["id"]: node for node in cls.nodes}
        cls.module = json.loads(MODULE_MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.module_commands = {command["id"]: command for command in cls.module["commands"]}

    def test_identity_version_dependency_and_counts(self):
        self.assertEqual(self.workflow["id"], "dave/retainer-billing-run")
        self.assertEqual(self.workflow["version"], "1.6.0")
        self.assertEqual(
            self.workflow["module_dependency"],
            {"id": "dave/stripe-invoicing", "minimum_version": "1.3.0"},
        )
        self.assertEqual(len(self.workflow["nodes"]), 5)
        self.assertEqual(len(self.workflow["edges"]), 4)
        self.assertEqual(len(self.nodes), 8)
        self.assertEqual(
            [node["id"] for node in self.workflow["nodes"]],
            ["validate", "dedup", "plan_summary", "charge", "reconcile"],
        )

    def test_incremental_source_uses_real_registered_action_without_state_args(self):
        source = self.by_id["invoice_history"]
        command = self.module_commands["stripe.billing.invoice_list"]
        provider, verb = MODULE_ROUTES._module_provider_verb(
            command["id"], command, "dave-stripe-invoicing"
        )
        self.assertEqual(provider + "_" + verb, "stripe_billing_invoice_list")
        self.assertEqual(source["action_id"], provider + "_" + verb)
        self.assertEqual(source["type"], "effect")
        self.assertEqual(source["parent"], "validate")
        self.assertEqual(source["args"], {"limit": 100})
        self.assertNotIn("since", source["args"])
        self.assertNotIn("exclude_invoice_ids", source["args"])
        node_json = json.dumps(self.nodes)
        self.assertNotIn("watermark_store", node_json)
        self.assertNotIn("cursor_file", node_json)
        self.assertEqual(self.by_id["dedup"]["parent"], "invoice_history")
        self.assertEqual(
            self.by_id["dedup"]["input_from"]["invoice_history"],
            "{{nodes.invoice_history._}}",
        )

    def test_station_injects_schedule_owned_since_without_mutating_source(self):
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
            prepared_source = next(node for node in prepared["nodes"] if node["id"] == "invoice_history")
            self.assertEqual(prepared_source["args"]["since"], "2026-08-08T23:55:00Z")
            self.assertEqual(prepared_source["args"]["exclude_invoice_ids"], [])
            self.assertEqual(plan["schedule_id"], "schedule-retainer-billing")
            self.assertEqual(len(plan["nodes"]), 1)
            self.assertEqual(self.by_id["invoice_history"]["args"], {"limit": 100})
        finally:
            if previous is None:
                incremental_runtime.INCREMENTAL_ACTIONS.pop(action_id, None)
            else:
                incremental_runtime.INCREMENTAL_ACTIONS[action_id] = previous

    def test_validate_regression(self):
        output = _run_transform(
            self.by_id["validate"],
            [
                {"email": "valid@example.test", "amount_cents": 100},
                {"email": "", "amount_cents": 100},
                {"email": "bool@example.test", "amount_cents": True},
                {"email": "float@example.test", "amount_cents": 1.5},
                {"email": "zero@example.test", "amount_cents": 0},
                "not-an-object",
            ],
        )
        self.assertEqual(output["billable"], [{"email": "valid@example.test", "amount_cents": 100}])
        self.assertEqual(len(output["rejected"]), 5)

    def test_dedup_combines_operator_input_and_relevant_stripe_history(self):
        validated = {
            "billable": [
                {"email": "same@example.test", "amount_cents": 100},
                {"email": "SAME@example.test", "amount_cents": 100},
                {"email": "operator@example.test", "amount_cents": 200},
                {"email": "history@example.test", "amount_cents": 300},
                {"email": "unrelated@example.test", "amount_cents": 400},
            ],
            "rejected": [],
        }
        history = {
            "truncated": False,
            "invoices": [
                {
                    "invoice_id": "in_match",
                    "customer_email": "history@example.test",
                    "amount_due_cents": 300,
                    "description": "Retainer billing for 2026-08",
                    "created_at": "2026-08-02T01:00:00Z",
                    "status": "open",
                },
                {
                    "invoice_id": "in_wrong_amount",
                    "customer_email": "unrelated@example.test",
                    "amount_due_cents": 999,
                    "description": "Retainer billing for 2026-08",
                    "created_at": "2026-08-02T01:00:00Z",
                    "status": "open",
                },
                {
                    "invoice_id": "in_wrong_period",
                    "customer_email": "unrelated@example.test",
                    "amount_due_cents": 400,
                    "description": "Retainer billing for 2026-08",
                    "created_at": "2026-07-31T23:59:59Z",
                    "status": "paid",
                },
                {
                    "invoice_id": "in_void",
                    "customer_email": "unrelated@example.test",
                    "amount_due_cents": 400,
                    "description": "Retainer billing for 2026-08",
                    "created_at": "2026-08-02T01:00:00Z",
                    "status": "void",
                },
                {
                    "invoice_id": "in_one_off_same_value",
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
                "already_billed": ["OPERATOR@example.test"],
                "period": "2026-08",
            },
        )
        self.assertEqual(
            output["clean"],
            [
                {
                    "email": "same@example.test",
                    "amount_cents": 100,
                    "description": "Retainer billing for 2026-08",
                    "period": "2026-08",
                },
                {
                    "email": "unrelated@example.test",
                    "amount_cents": 400,
                    "description": "Retainer billing for 2026-08",
                    "period": "2026-08",
                },
            ],
        )
        reasons = [row["reason"] for row in output["skipped"]]
        self.assertIn("duplicate_in_export", reasons)
        self.assertIn("already_billed_this_period", reasons)
        self.assertIn("stripe_history_same_period", reasons)
        self.assertEqual(output["stripe_history_skipped_count"], 1)

    def test_truncated_history_holds_all_remaining_candidates(self):
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
        self.assertTrue(output["history_truncated"])

    def test_plan_summary_advisory_and_reconcile_regressions(self):
        summary = _run_transform(
            self.by_id["plan_summary"],
            {
                "dedup_output": {
                    "clean": [{"email": "a@example.test", "amount_cents": 700}],
                    "skipped": [{"reason": "stripe_history_same_period"}],
                    "stripe_history_skipped_count": 1,
                    "history_invoices_considered": 2,
                    "history_truncated": False,
                },
                "validate_output": {"rejected": [{"reason": "invalid_amount"}]},
                "spend_ceiling_cents": 1000,
            },
        )
        self.assertEqual(summary["billable_count"], 1)
        self.assertEqual(summary["skipped_count"], 1)
        self.assertEqual(summary["rejected_count"], 1)
        self.assertEqual(summary["total_cents"], 700)
        self.assertTrue(summary["within_cap"])
        self.assertTrue(summary["proceed"])
        self.assertEqual(summary["stripe_history_skipped_count"], 1)

        advisory_disabled = _run_transform(
            self.by_id["advisory_switch"],
            {"requested": "false", "operator_declared_available": "false"},
        )
        advisory_skipped = _run_transform(
            self.by_id["advisory_switch"],
            {"requested": "true", "operator_declared_available": "false"},
        )
        advisory_prepared = _run_transform(
            self.by_id["advisory_switch"],
            {"requested": "true", "operator_declared_available": "true"},
        )
        self.assertEqual(advisory_disabled["status"], "disabled")
        self.assertEqual(advisory_skipped["status"], "skipped")
        self.assertEqual(advisory_prepared["status"], "prepared")
        self.assertTrue(advisory_prepared["decision_support_only"])
        self.assertTrue(advisory_prepared["charge_independent"])

        anomaly = _run_transform(
            self.by_id["anomaly_payload"],
            {
                "clean": [{"email": "private@example.test", "amount_cents": 700}],
                "billing_period": "2026-08",
                "portfolio_baseline_cents": 600,
            },
        )
        payload_text = json.dumps(anomaly)
        self.assertNotIn("private@example.test", payload_text)
        self.assertEqual(anomaly["portfolio"][0]["record_ref"], "batch-1")

        reconciled = _run_transform(
            self.by_id["reconcile"],
            {
                "charge_output": {"items": [{"invoice_id": "in_a"}, {"invoice_id": "in_b"}]},
                "plan_summary_output": {"billable_count": 3, "total_cents": 2100},
            },
        )
        self.assertEqual(reconciled, {"intended": 3, "landed": 2, "difference": 1, "total_planned_cents": 2100})

    def test_charge_governance_and_dunning_absence(self):
        charge = self.by_id["charge"]
        self.assertEqual(charge["action_id"], "stripe_billing_bill_client")
        self.assertEqual(charge["for_each"], "{{nodes.dedup.clean}}")
        self.assertEqual(charge["args"], "{{ctx.item}}")
        self.assertEqual(charge["retry"], {"max": 3, "backoff_s": 2})
        self.assertNotIn("stripe_billing_dunning_message_draft", [node.get("action_id") for node in self.nodes])
        self.assertEqual(self.engine["capabilities"], {
            "providers": ["stripe"],
            "max_spend_cents": 50000,
            "allow_irreversible": True,
        })
        self.assertEqual(self.workflow["capabilities"]["max_spend_cents"], 50000)
        self.assertTrue(self.workflow["capabilities"]["allow_irreversible"])

    def _registered_actions(self, invoice_handler, charge_handler):
        source_command = self.module_commands["stripe.billing.invoice_list"]
        charge_command = self.module_commands["stripe.billing.bill_client"]
        source = MODULE_ROUTES._synth_module_integration(
            source_command["id"], source_command, invoice_handler, "dave-stripe-invoicing"
        )
        charge = MODULE_ROUTES._synth_module_integration(
            charge_command["id"], charge_command, charge_handler, "dave-stripe-invoicing"
        )
        return {
            "stripe_billing_invoice_list": source,
            "stripe_billing_bill_client": charge,
        }

    def test_v065_plan_resolves_actions_and_is_stable(self):
        actions = self._registered_actions(
            HANDLER.stripe_billing_invoice_list,
            HANDLER.stripe_billing_bill_client,
        )
        old = {key: ENGINE.R.ACTIONS.get(key) for key in actions}
        ENGINE.R.ACTIONS.update(actions)
        try:
            first = ENGINE.plan_workflow(self.engine, signing=_Signing())
            second = ENGINE.plan_workflow(self.engine, signing=_Signing())
        finally:
            for key, value in old.items():
                if value is None:
                    ENGINE.R.ACTIONS.pop(key, None)
                else:
                    ENGINE.R.ACTIONS[key] = value
        self.assertEqual(first["workflow_root"], second["workflow_root"])
        self.assertEqual(first["blast_radius"]["node_count"], 8)
        self.assertIn("stripe", first["blast_radius"]["systems_touched"])
        self.assertIn("charge:stripe_billing_bill_client", first["blast_radius"]["irreversible"])
        self.assertNotIn("invoice_history:stripe_billing_invoice_list", first["blast_radius"]["irreversible"])
        self.assertEqual(first["blast_radius"]["requires"], "require_human")
        self.assertTrue(all(node["policy"]["decision"] != "block" for node in first["nodes"]))

    def test_invoice_history_failure_stops_before_charge(self):
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
                result = ENGINE.run_workflow(
                    self.engine,
                    ws=workspace,
                    signing=_Signing(),
                    allow_live_effects=False,
                )
        finally:
            for key, value in old.items():
                if value is None:
                    ENGINE.R.ACTIONS.pop(key, None)
                else:
                    ENGINE.R.ACTIONS[key] = value
        self.assertEqual(result["outcome"], "ROLLED_BACK")
        self.assertEqual(calls["history"], 2)
        self.assertEqual(calls["charge"], 0)
        self.assertNotIn("charge", result["taken"])

    def test_v065_effect_wrapper_drives_history_dedup_before_charge(self):
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
            return {
                "invoice_id": "in_charge_" + str(len(calls["charge"])),
                "amount_due_cents": inputs["amount_cents"],
            }, None

        actions = self._registered_actions(invoice_history, charge)
        old = {key: ENGINE.R.ACTIONS.get(key) for key in actions}
        ENGINE.R.ACTIONS.update(actions)
        engine = json.loads(json.dumps(self.engine))
        engine["context"].update({
            "clients": [
                {"email": "history@example.test", "amount_cents": 300},
                {"email": "unrelated@example.test", "amount_cents": 400},
                {"email": "operator@example.test", "amount_cents": 200},
                {"email": "same@example.test", "amount_cents": 100},
                {"email": "SAME@example.test", "amount_cents": 100},
            ],
            "already_billed": ["OPERATOR@example.test"],
            "billing_period": "2026-08",
        })
        no_client = mock.patch.object(ENGINE, "_client", return_value=(None, "mock"))
        try:
            with tempfile.TemporaryDirectory() as workspace, no_client:
                result = ENGINE.run_workflow(
                    engine,
                    ws=workspace,
                    signing=_Signing(),
                    allow_live_effects=False,
                )
        finally:
            for key, value in old.items():
                if value is None:
                    ENGINE.R.ACTIONS.pop(key, None)
                else:
                    ENGINE.R.ACTIONS[key] = value

        self.assertEqual(result["outcome"], "COMPLETED")
        history_output = result["outputs"]["invoice_history"]
        self.assertNotIn("invoices", history_output)
        self.assertEqual(
            [row["invoice_id"] for row in history_output["_"]["invoices"]],
            ["in_relevant", "in_one_off"],
        )
        dedup = result["outputs"]["dedup"]["output"]
        reasons = [row["reason"] for row in dedup["skipped"]]
        self.assertIn("stripe_history_same_period", reasons)
        self.assertIn("already_billed_this_period", reasons)
        self.assertIn("duplicate_in_export", reasons)
        self.assertEqual(
            [row["email"] for row in calls["charge"]],
            ["unrelated@example.test", "same@example.test"],
        )
        self.assertNotIn("history@example.test", [row["email"] for row in calls["charge"]])

    def test_successful_fixture_run_preserves_charge_and_reconcile_flow(self):
        calls = {"history": [], "charge": []}

        def invoice_history(inputs, stamp):
            calls["history"].append(inputs)
            return {
                "invoices": [
                    {
                        "invoice_id": "in_unrelated",
                        "customer_email": "someone-else@example.test",
                        "amount_due_cents": 1234,
                        "description": "Retainer billing for 2026-07",
                        "created_at": "2026-07-02T01:00:00Z",
                        "status": "open",
                    }
                ],
                "truncated": False,
            }, None

        def charge(inputs, stamp):
            calls["charge"].append(inputs)
            return {"invoice_id": "in_fixture", "amount_due_cents": inputs["amount_cents"]}, None

        actions = self._registered_actions(invoice_history, charge)
        old = {key: ENGINE.R.ACTIONS.get(key) for key in actions}
        ENGINE.R.ACTIONS.update(actions)
        no_client = mock.patch.object(ENGINE, "_client", return_value=(None, "mock"))
        try:
            with tempfile.TemporaryDirectory() as workspace, no_client:
                result = ENGINE.run_workflow(
                    self.engine,
                    ws=workspace,
                    signing=_Signing(),
                    allow_live_effects=False,
                )
        finally:
            for key, value in old.items():
                if value is None:
                    ENGINE.R.ACTIONS.pop(key, None)
                else:
                    ENGINE.R.ACTIONS[key] = value
        self.assertEqual(result["outcome"], "COMPLETED")
        self.assertEqual(calls["history"], [{"limit": 100}])
        self.assertEqual(len(calls["charge"]), 1)
        self.assertEqual(calls["charge"][0]["email"], "client001@example.com")
        self.assertEqual(calls["charge"][0]["description"], "Retainer billing for 2026-07")
        self.assertEqual(result["outputs"]["reconcile"]["landed"], 1)
        self.assertEqual(result["outputs"]["reconcile"]["difference"], 0)

    def test_no_new_workflow_json_was_created(self):
        self.assertEqual([path.name for path in WORKFLOW_DIR.glob("*.json")], ["spec.json"])


if __name__ == "__main__":
    unittest.main()
