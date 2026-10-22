"""Pure in-memory revision fixtures: no Git, devices, or real control records."""
import copy
import importlib.util
import itertools
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InvalidatedReviewOrderTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "private_revision_order", ROOT / "tools/stage_acceptance.py")
        self.stage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.stage)
        self.error = self.stage.ctl().ControlError

    def programs(self):
        plan = {"schema_version": 1, "program_id": "unit-order",
                "project_root": str(ROOT / "fixture_project"),
                "authorized_actions": ["verify", "code"],
                "stages": [
                    {"id": "done", "title": "Accepted", "required_actions": ["verify"]},
                    {"id": "zulu", "title": "Pending first", "required_actions": ["verify"]},
                    {"id": "alpha", "title": "Pending second", "required_actions": ["verify"]}]}
        # Insertion order deliberately differs from canonical JSON key order.
        before = {"version": 1, "generation": 7, "plan": plan,
                  "plan_digest": self.stage.digest(plan), "bindings": {},
                  "reviews": {"zulu": [{"review_id": "z-first", "outcome": "fail"},
                                        {"review_id": "z-second", "outcome": "inconclusive"}],
                              "done": [{"review_id": "accepted-review", "outcome": "pass"}],
                              "alpha": [{"review_id": "a-first", "outcome": "fail"}]},
                  "decisions": [{"stage_id": "done", "decision": "confirm"}]}
        after = copy.deepcopy(before)
        after["plan"]["stages"][1]["required_actions"].append("code")
        after["plan_digest"] = self.stage.digest(after["plan"])
        after["generation"] = 8
        after["plan_revisions"] = [{
            "request_id": "unit-append", "generation": 8,
            "previous_plan": copy.deepcopy(before["plan"]),
            "old_plan_digest": before["plan_digest"],
            "new_plan_digest": after["plan_digest"],
            "accepted_prefix": ["done"],
            "invalidated_reviews": ["z-first", "z-second", "a-first"]}]
        return before, after

    def assert_valid(self, before, after):
        try:
            self.stage._validate_revision_append(before, after)
        except self.error as exc:
            self.fail("Valid revision must survive JSON object-key order: " + str(exc))

    def test_canonical_event_json_roundtrip_preserves_valid_revision(self):
        before, after = self.programs()
        old = json.loads(json.dumps(before, sort_keys=True))
        new = json.loads(json.dumps(after, sort_keys=True))
        self.assertEqual(before, old)
        self.assertNotEqual(list(before["reviews"]), list(old["reviews"]))
        self.assert_valid(old, new)

    def test_event_issues_accepts_canonical_replayed_revision(self):
        before, after = self.programs()
        events = []
        for action, program in (("register", before), ("revise", after)):
            events.append({"event_type": "metadata_updated", "actor": "icode",
                           "request_id": "unit-" + action,
                           "payload": {"stage_control": 1, "stage_action": action,
                                       "stage_namespace": "stage_program", "stage_value": program,
                                       "set": {"extensions": {"stage_program": program}},
                                       "append": {}}})
        replayed = json.loads(json.dumps(events, sort_keys=True))
        issues = self.stage.event_issues(replayed, {"extensions": {"stage_program": after}})
        self.assertEqual(issues, [], "Canonical serialization cannot invalidate a legal revision")

    def test_valid_member_permutations_are_equivalent(self):
        before, after = self.programs()
        for permutation in itertools.permutations(["z-first", "z-second", "a-first"]):
            with self.subTest(permutation=permutation):
                proposed = copy.deepcopy(after)
                proposed["plan_revisions"][-1]["invalidated_reviews"] = list(permutation)
                self.assert_valid(before, proposed)

    def test_missing_extra_duplicate_unknown_and_nonlist_are_rejected(self):
        before, after = self.programs()
        invalid = [None, "z-first", {"z-first": True}, ("z-first", "z-second", "a-first"),
                   ["z-first", "z-second"],
                   ["z-first", "z-second", "a-first", "extra"],
                   ["z-first", "z-first", "z-second"],
                   ["z-first", "z-second", "unknown"],
                   ["z-first", "z-second", "accepted-review"],
                   ["z-first", "z-second", 123],
                   ["z-first", "z-second", ["a-first"]]]
        for value in invalid:
            with self.subTest(value=repr(value)):
                proposed = copy.deepcopy(after)
                proposed["plan_revisions"][-1]["invalidated_reviews"] = value
                with self.assertRaises(self.error):
                    self.stage._validate_revision_append(before, proposed)

    def test_accepted_definition_authority_and_old_actions_stay_protected(self):
        before, after = self.programs()
        for kind in ("accepted", "authority", "old_action", "pending_title"):
            with self.subTest(kind=kind):
                proposed = copy.deepcopy(after)
                if kind == "accepted":
                    proposed["plan"]["stages"][0]["required_actions"].append("code")
                elif kind == "authority":
                    proposed["plan"]["authorized_actions"].append("deploy")
                elif kind == "old_action":
                    proposed["plan"]["stages"][1]["required_actions"] = ["code"]
                else:
                    proposed["plan"]["stages"][1]["title"] = "Rewritten obligation"
                proposed["plan_digest"] = self.stage.digest(proposed["plan"])
                proposed["plan_revisions"][-1]["new_plan_digest"] = proposed["plan_digest"]
                with self.assertRaises(self.error):
                    self.stage._validate_revision_append(before, proposed)

    def test_lineage_generation_and_protected_records_stay_protected(self):
        before, after = self.programs()
        for kind in ("lineage", "generation", "bindings", "reviews", "decisions", "history"):
            with self.subTest(kind=kind):
                proposed = copy.deepcopy(after)
                if kind == "lineage":
                    proposed["plan_revisions"][-1]["old_plan_digest"] = "0" * 64
                elif kind == "generation":
                    proposed["generation"] += 1
                elif kind == "bindings":
                    proposed["bindings"]["new"] = {"control_root": "elsewhere"}
                elif kind == "reviews":
                    proposed["reviews"]["zulu"][0]["outcome"] = "pass"
                elif kind == "decisions":
                    proposed["decisions"][0]["decision"] = "reject"
                else:
                    proposed["plan_revisions"] = []
                with self.assertRaises(self.error):
                    self.stage._validate_revision_append(before, proposed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
