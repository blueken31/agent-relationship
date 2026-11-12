"""The offline preview must not turn host/model content into browser code."""

from html.parser import HTMLParser
import unittest

from agent_relationship import RelationshipTracker
from examples.task_review_preview import build_demo, render_preview


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.attributes = []
        self.pill_labels = []
        self._pill_depth = 0
        self._pill_text = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(attrs)
        if self._pill_depth:
            self._pill_depth += 1
        elif tag == "span" and "pill" in dict(attrs).get("class", "").split():
            self._pill_depth = 1
            self._pill_text = []

    def handle_endtag(self, tag):
        if self._pill_depth:
            self._pill_depth -= 1
            if self._pill_depth == 0:
                self.pill_labels.append("".join(self._pill_text).strip())

    def handle_data(self, data):
        if self._pill_depth:
            self._pill_text.append(data)


class TestTaskPreview(unittest.TestCase):
    def test_untrusted_observations_assessments_and_identifiers_are_escaped(self):
        attack = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
        card = {
            "title": attack, "agents": [attack, "reviewer"],
            "observation": {"event_id": attack, "result": attack, "narrative": attack},
            "assessment": {"source": attack, "reason": attack, "goal_status": "unknown"},
            "handling": {
                "state": "cooldown", "last_outcome": "partial", "evidence_event_id": attack,
                "observation": {"event_id": attack, "result": attack, "narrative": attack},
                "assessment": {"source": attack, "reason": attack, "goal_status": "unknown"},
            },
            "next_action": {"label": attack}, "evidence_ids": [attack],
            "resolution_event_id": attack,
            "host_verification": {
                "status": "passed", "event_id": attack, "event_ids": [attack],
                "observation": {"result": attack, "narrative": attack},
                "assessment": {"status": "unknown", "source": attack, "reason": attack},
            },
        }
        view = {"task_id": attack, "recovery_status": "waiting", "incidents": [card]}
        html = render_preview([view], {"tasks": [view], "unlinked_incidents": []}, {attack: attack})
        parsed = Elements()
        parsed.feed(html)
        self.assertNotIn("script", parsed.tags)
        self.assertNotIn("img", parsed.tags)
        self.assertFalse(any(key.startswith("on") for key, _ in parsed.attributes))
        self.assertIn("&lt;script&gt;", html)
        self.assertTrue(all(value.startswith("#") for key, value in parsed.attributes if key == "href"))
        self.assertIn("本次核验结果", html)

    def test_demo_shows_partial_result_and_uses_the_declared_retest_action(self):
        views, queue = build_demo()
        tasks = {view["task_id"]: view for view in views}
        partial = tasks["report-citations"]["incidents"][0]
        self.assertEqual(partial["handling"]["evidence_event_id"], "report-citations-verification")
        self.assertEqual(partial["observation"]["event_id"], "report-citations-observation")
        self.assertEqual(tasks["report-retest"]["incidents"][0]["handling"]["action"], "restricted_retest")
        html = render_preview(views, queue)
        self.assertIn("report-citations-verification", html)
        self.assertIn("已对齐资料版本，引用来源仍需补充", html)
        self.assertIn("三处引用尚未对应到原始资料", html)

    def test_passed_retest_and_unknown_assessment_share_one_original_task_card(self):
        views, _ = build_demo()
        view = next(view for view in views if view["task_id"] == "purchase-retest")
        self.assertEqual(view["open_incident_count"], 2)
        html = render_preview([view], {"tasks": [view], "unlinked_incidents": []})
        parsed = Elements()
        parsed.feed(html)
        self.assertEqual(parsed.tags.count("article"), 1)
        self.assertEqual(parsed.pill_labels, ["复测通过 · 评估待核对"])
        self.assertIn("执行验证", html)
        self.assertIn("宿主报告通过", html)
        self.assertIn("协作评估", html)
        self.assertIn("尚未完成", html)
        self.assertIn("核对协作评估", html)
        self.assertIn("purchase-retest-verification", html)
        self.assertNotIn("准备核验方案", html)
        self.assertNotIn("已核验解决", parsed.pill_labels)

    def test_unrelated_assessment_issue_is_not_hidden_by_preview_grouping(self):
        from copy import deepcopy
        views, _ = build_demo()
        view = deepcopy(next(v for v in views if v["task_id"] == "purchase-retest"))
        child = next(c for c in view["incidents"] if c["related_incident_id"])
        child["related_incident_id"] = None
        child["observation"]["narrative"] = "另一条未关联的未知观察"
        parsed = Elements()
        html = render_preview([view], {"tasks": [view], "unlinked_incidents": []})
        parsed.feed(html)
        self.assertEqual(parsed.tags.count("article"), 2)
        self.assertIn("另一条未关联的未知观察", html)

    def test_preview_has_only_local_navigation_and_evidence_controls(self):
        html = render_preview([], {"tasks": [], "unlinked_incidents": []})
        parsed = Elements()
        parsed.feed(html)
        self.assertFalse(set(parsed.tags) & {"button", "form", "script", "iframe", "link"})
        self.assertIn("离线模拟 · 只读预览", html)
        self.assertIn("不会授予权限", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)

    def test_missing_verification_evidence_overrides_historical_resolved_badge(self):
        with RelationshipTracker() as tracker:
            issue = tracker.track("a", "b", {
                "event_id": "original", "task_id": "report", "action": "handoff", "result": "failed",
            }).incident_id
            plan = tracker.propose_repair(issue)
            tracker.begin_repair(plan["id"], host_authorized=True)
            tracker.track("a", "b", {
                "event_id": "verification", "task_id": "report", "action": plan["action"],
                "repair_plan_id": plan["id"], "scenario": "success", "result": "success",
                "verification_passed": True,
            })
            tracker.complete_repair(plan["id"], "resolved", "verification")
            # Simulate incomplete retained evidence in an advanced memory host.
            del tracker.engine.event_records["verification"]
            view, queue = tracker.task_view("report"), tracker.task_queue()
        self.assertEqual(view["recovery_status"], "needs_attention")
        self.assertEqual(view["incidents"][0]["handling"]["state"], "evidence_missing")
        self.assertEqual(view["incidents"][0]["handling"]["last_outcome"], "resolved")
        self.assertEqual([item["task_id"] for item in queue["tasks"]], ["report"])
        parsed = Elements()
        parsed.feed(render_preview([view], queue))
        self.assertEqual(parsed.pill_labels, ["依据不完整"])
        self.assertNotIn("已核验解决", parsed.pill_labels)


if __name__ == "__main__":
    unittest.main()
