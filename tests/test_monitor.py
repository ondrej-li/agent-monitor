"""Unit tests for the pure parts of agent-monitor."""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from monitor import chat, digest, render, store  # noqa: E402


class PromptParsingTests(unittest.TestCase):
    def test_parses_fenced_json(self):
        payload = '```json\n{"headline": "ok", "ideas": []}\n```'
        self.assertEqual(digest.parse_response(payload)["headline"], "ok")

    def test_parses_json_embedded_in_prose(self):
        payload = 'Here you go:\n{"headline": "x"}\nThat is all.'
        self.assertEqual(digest.parse_response(payload)["headline"], "x")

    def test_rejects_non_json(self):
        with self.assertRaises(ValueError):
            digest.parse_response("nothing useful")

    def test_build_prompt_uses_configured_domains(self):
        from monitor import config as config_mod

        cfg = config_mod.Config(
            config_mod.deep_merge(
                config_mod.DEFAULTS,
                {"identity": {"login": "demo", "name": "Demo", "domains": ["billing sync"]}},
            ),
            pathlib.Path("."),
        )
        context = {"identity": {"login": "demo", "name": "Demo", "bio": "x"}}
        system, user = digest.build_prompt(cfg, context)
        self.assertIn("billing sync", system)
        self.assertNotIn("{domains}", system)
        self.assertIn('"login":"demo"', user)

    def test_normalize_clamps_and_defaults(self):
        result = digest.normalize(
            {
                "headline": "h",
                "needs_you": [{"title": "t", "why": "w", "link": "https://x"}],
                "ideas": [{"title": "i", "effort": "x"}],
                "risks": "not a list",
            }
        )
        self.assertEqual(result["needs_you"][0]["title"], "t")
        self.assertEqual(result["ideas"][0]["effort"], "M")
        self.assertEqual(result["risks"], [])


class IdeaDedupeTests(unittest.TestCase):
    def test_repeat_is_suppressed(self):
        existing = [
            {
                "title": "Add a contract test for the Pylon ticket provider",
                "area": "acme/orbit",
            }
        ]
        ideas = [
            {
                "title": "Add contract tests for the Pylon ticket provider",
                "area": "acme/orbit",
            },
            {"title": "Speed up the golden test suite by caching fixtures", "area": "atlas"},
        ]
        kept, dropped = digest.dedupe_ideas(ideas, existing)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["title"], "Speed up the golden test suite by caching fixtures")
        self.assertEqual(len(dropped), 1)

    def test_dedupes_within_a_batch(self):
        ideas = [
            {"title": "Cache Zendesk tenant lookups for a minute", "area": "atlas"},
            {"title": "Cache Zendesk tenant lookups for a minute in-process", "area": "atlas"},
        ]
        kept, dropped = digest.dedupe_ideas(ideas, [])
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dropped), 1)

    def test_different_ideas_survive(self):
        ideas = [
            {"title": "Split the Hades copy job into per-tenant batches", "area": "helios"},
            {"title": "Add SSO login telemetry to the MFA panel", "area": "identity"},
        ]
        kept, _ = digest.dedupe_ideas(ideas, [])
        self.assertEqual(len(kept), 2)

    def test_fingerprint_is_stable_across_word_order(self):
        left = digest.fingerprint({"title": "batch inserts by size", "area": "mdr"})
        right = digest.fingerprint({"title": "size batch by inserts", "area": "mdr"})
        self.assertEqual(left, right)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.digest = {
            "headline": "Quiet morning",
            "needs_you": [{"title": "<script>x</script>", "why": "w", "link": "javascript:alert(1)"}],
            "projects": [{"name": "acme/orbit", "status_line": "s", "bullets": ["b"]}],
            "ideas": [{"title": "An idea", "area": "atlas", "why_now": "now", "effort": "S"}],
            "risks": [],
            "next_actions": ["do it"],
        }
        self.meta = {
            "when": datetime(2026, 11, 23, 8, 0, tzinfo=timezone.utc),
            "prefix": "Secretary",
            "model": "test-model",
            "duration_s": 1.5,
            "counts": {"new": 1, "changed": 2, "open": 3, "commits": 4, "scanned": 9},
        }

    def test_html_escapes_input(self):
        html = render.email_html(self.digest, self.meta)
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_html_drops_unsafe_links(self):
        html = render.email_html(self.digest, self.meta)
        self.assertNotIn("javascript:", html)

    def test_subject_mentions_pending_work(self):
        subject = render.email_subject(self.digest, self.meta)
        self.assertIn("1 need you", subject)
        self.assertIn("1 idea", subject)

    def test_markdown_contains_sections(self):
        markdown = render.to_markdown(self.digest, self.meta)
        self.assertIn("## Needs you", markdown)
        self.assertIn("## Ideas", markdown)


class ItemDiffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = store.Store(pathlib.Path(self.tmp.name) / "store")

    def tearDown(self):
        self.tmp.cleanup()

    def _item(self, **overrides):
        item = {
            "key": "acme/orbit#1",
            "repo": "acme/orbit",
            "kind": "pr",
            "number": 1,
            "title": "t",
            "url": "https://x",
            "state": "open",
            "updated_at": "2026-11-20T10:00:00Z",
            "involvement": ["author"],
        }
        item.update(overrides)
        return item

    def test_new_then_unchanged_then_changed(self):
        now = datetime(2026, 11, 23, 8, 0, tzinfo=timezone.utc)
        first = self.store.record_items([self._item()], now)
        self.assertEqual(len(first["new"]), 1)
        self.assertTrue((self.store.root / "items.json").exists())
        second = self.store.record_items([self._item()], now)
        self.assertEqual(len(second["unchanged"]), 1)
        third = self.store.record_items(
            [self._item(state="closed", updated_at="2026-11-22T10:00:00Z")], now
        )
        self.assertEqual(len(third["changed"]), 1)
        self.assertEqual(third["changed"][0]["previous_state"], "open")
        self.assertEqual(self.store.item("acme/orbit#1")["state"], "closed")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = store.Store(pathlib.Path(self.tmp.name) / "store")

    def tearDown(self):
        self.tmp.cleanup()

    def test_alert_is_reused_and_counted(self):
        first = self.store.record_alerts(
            [{"title": "PR #1 waits for your review", "why": "review", "link": "https://x"}],
            ["Token expires silently"],
            {"when": "2026-11-23T08:00:00Z"},
        )
        second = self.store.record_alerts(
            [{"title": "PR #1 waits for your review", "why": "review", "link": "https://x"}],
            [],
            {"when": "2026-11-23T18:00:00Z"},
        )
        self.assertEqual(first["needs_you"][0]["id"], second["needs_you"][0]["id"])
        record = self.store.alert(first["needs_you"][0]["id"])
        self.assertEqual(record["seen_count"], 2)
        self.assertEqual(record["status"], "open")
        self.store.set_alert_status(record["id"], "acknowledged")
        self.assertEqual(self.store.alert(record["id"])["status"], "acknowledged")
        self.assertEqual(len(self.store.alerts()), 2)

    def test_idea_duplicate_by_fingerprint(self):
        idea = {"title": "Add a Pylon provider", "area": "atlas", "effort": "M"}
        created, record = self.store.add_idea(idea, "fp123", source="chat")
        again, same = self.store.add_idea({**idea, "title": "Add Pylon provider"}, "fp123")
        self.assertTrue(created)
        self.assertFalse(again)
        self.assertEqual(record["id"], same["id"])
        self.assertEqual(self.store.ideas()[0]["seen_count"], 2)

    def test_chat_threads_and_pending_recovery(self):
        message_id = self.store.add_message("idea:abc123", "user", "hello")
        pending = self.store.add_message("idea:abc123", "assistant", "", status="pending")
        self.assertEqual([m["id"] for m in self.store.messages("idea:abc123")], [message_id, pending])
        self.assertEqual(self.store.mark_pending_errors("restarted"), 1)
        self.assertEqual(self.store.messages("idea:abc123")[-1]["status"], "error")
        self.assertIn("idea:abc123", self.store.chat_index())

    def test_mark_running_errors(self):
        run_id = self.store.start_run("p", "m", datetime.now(timezone.utc))
        self.assertEqual(self.store.mark_running_errors("server restarted"), 1)
        record = self.store.run(run_id)
        self.assertEqual(record["status"], "error")
        self.assertEqual(record["error"], "server restarted")

    def test_state_roundtrip(self):
        self.store.set_state("last_success_at", "2026-11-23T08:00:00Z")
        self.assertEqual(self.store.get_state("last_success_at"), "2026-11-23T08:00:00Z")


class AttentionTests(unittest.TestCase):
    def test_ancient_items_are_parked_not_idle(self):
        from monitor import config as config_mod

        cfg = config_mod.Config(config_mod.deep_merge(config_mod.DEFAULTS, {}), pathlib.Path("."))

        def item(number, idle):
            repo = "acme/orbit"
            return {
                "key": f"{repo}#{number}",
                "repo": repo,
                "kind": "pr",
                "number": number,
                "title": f"t{number}",
                "url": f"https://github.com/{repo}/pull/{number}",
                "state": "open",
                "involvement": ["author"],
                "idle_days": idle,
                "priority": 1,
                "updated_at": "2026-01-01T00:00:00Z",
            }

        collection = {
            "login": "demo-user",
            "window": {"since": "2026-01-01T00:00:00Z"},
            "repos": [],
            "prs": [],
            "issues": [],
            "commits": [],
            "details": {},
        }
        context = digest.build_context(
            cfg,
            collection,
            {"new": [], "changed": [], "unchanged": []},
            [item(1, 5), item(2, 10), item(3, 300), item(4, 250)],
            [],
            datetime.now(timezone.utc),
            notes=[{"text": "focus on atlas this week"}],
        )
        self.assertEqual({entry["number"] for entry in context["idle"]}, {2})
        self.assertEqual(context["parked"][0]["count"], 2)
        self.assertEqual(len(context["parked"]), 1)
        self.assertEqual(context["user_notes"], ["focus on atlas this week"])


class LinkGuardTests(unittest.TestCase):
    def test_invented_links_are_dropped(self):
        context = {
            "new": [{"url": "https://github.com/o/r/pull/1"}],
            "changed": [],
            "closed": [],
            "idle": [],
        }
        briefing = {
            "needs_you": [
                {"title": "real", "link": "https://github.com/o/r/pull/1/"},
                {"title": "fake", "link": "https://github.com/o/r/pull/999"},
            ],
            "ideas": [{"title": "i", "link": "https://example.com/made-up"}],
        }
        digest.enforce_links(briefing, context)
        self.assertEqual(briefing["needs_you"][0]["link"], "https://github.com/o/r/pull/1")
        self.assertEqual(briefing["needs_you"][1]["link"], "")
        self.assertEqual(briefing["ideas"][0]["link"], "")


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = store.Store(pathlib.Path(self.tmp.name) / "store")

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_accepts_fenced_json_with_actions(self):
        raw = '```json\n{"reply": "ok", "actions": [{"type": "note", "text": "focus on atlas"}]}\n```'
        parsed = chat.parse(raw)
        self.assertEqual(parsed["reply"], "ok")
        self.assertEqual(parsed["actions"][0]["type"], "note")

    def test_fallback_reply_recovers_prose_and_json(self):
        self.assertEqual(chat.fallback_reply("Just prose."), "Just prose.")
        recovered = chat.fallback_reply('{"reply": "recovered answer", "actions": [')
        self.assertEqual(recovered, "recovered answer")

    def test_parse_rejects_missing_reply(self):
        with self.assertRaises(ValueError):
            chat.parse('{"actions": []}')

    def test_note_action_is_saved(self):
        applied = chat.apply_actions(self.store, "general", [{"type": "note", "text": "ship atlas first"}])
        self.assertEqual(applied[0]["status"], "saved")
        self.assertEqual(self.store.notes()[0]["text"], "ship atlas first")

    def test_idea_action_is_saved_once(self):
        idea = {
            "type": "idea",
            "title": "Add a Pylon ticket provider",
            "area": "acme/orbit",
            "why_now": "the contract just landed",
            "effort": "M",
        }
        first = chat.apply_actions(self.store, "general", [idea])
        second = chat.apply_actions(self.store, "general", [idea])
        self.assertEqual(first[0]["status"], "saved")
        self.assertEqual(second[0]["status"], "duplicate")
        self.assertEqual(len(self.store.ideas()), 1)

    def test_thread_labels_for_items_ideas_and_alerts(self):
        self.assertEqual(chat.thread_label(self.store, "general"), "General")
        self.assertEqual(chat.thread_label(self.store, "item:o/r#1"), "Item · o/r#1")
        _, idea = self.store.add_idea({"title": "A tracked idea", "area": "atlas"}, "fpx")
        self.assertEqual(chat.thread_label(self.store, f"idea:{idea['id']}"), "Idea · A tracked idea")
        alert = self.store.record_alerts([{"title": "Attention", "why": "", "link": ""}], [], {})
        alert_id = alert["needs_you"][0]["id"]
        self.assertEqual(chat.thread_label(self.store, f"alert:{alert_id}"), "Alert · Attention")


class TopicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = store.Store(pathlib.Path(self.tmp.name) / "store")

    def tearDown(self):
        self.tmp.cleanup()

    def test_topic_key_is_stable_and_scoped_by_kind(self):
        self.assertEqual(store.topic_key("project", "acme/orbit"), store.topic_key("project", "acme/orbit"))
        self.assertNotEqual(store.topic_key("project", "x"), store.topic_key("risk", "x"))

    def test_record_topic_counts_repeats(self):
        first = self.store.record_topic("project", "acme/orbit", {"status_line": "a"})
        second = self.store.record_topic("project", "acme/orbit", {"status_line": "b"})
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.store.topic(first["id"])["seen_count"], 2)
        self.assertEqual(self.store.topic(first["id"])["payload"]["status_line"], "b")

    def test_topic_thread_label(self):
        record = self.store.record_topic("risk", "Imports time out", {"text": "Imports time out"})
        self.assertEqual(chat.thread_label(self.store, f"topic:{record['id']}"), "Risk · Imports time out")

    def test_every_digest_item_gets_a_discuss_button(self):
        digest = {
            "headline": "h",
            "needs_you": [{"title": "Review #1", "why": "w", "link": "", "alert_id": "20260101T000000-abcdef"}],
            "projects": [{"name": "acme/orbit", "status_line": "s", "bullets": ["b"]}],
            "ideas": [{"idea_id": "20260101T000000-123456", "title": "i", "area": "a", "why_now": "w", "effort": "S"}],
            "risks": ["Imports time out"],
            "next_actions": ["Review #1"],
        }
        meta = {"when": datetime(2026, 11, 23, 8, 0, tzinfo=timezone.utc), "counts": {}}
        html = render.digest_body_html(digest, meta, palette=render.PAGE, interactive=True)
        self.assertIn("thread=alert:20260101T000000-abcdef", html)
        self.assertEqual(html.count("thread=topic:project:"), 1)
        self.assertEqual(html.count("thread=topic:risk:"), 1)
        self.assertEqual(html.count("thread=topic:action:"), 1)
        self.assertEqual(html.count("thread=idea:20260101T000000-123456"), 1)
        self.assertNotIn("Discuss", render.digest_body_html(digest, meta))


class ThemeTests(unittest.TestCase):
    def test_titles_lead_with_secretary_and_boot_theme(self):
        html = render.page("Chat", "<p>x</p>", active="chat")
        self.assertIn("<title>Secretary · Chat</title>", html)
        self.assertIn("localStorage.getItem('theme')", html)
        self.assertIn('prefers-color-scheme:dark', render.PAGE_CSS)

    def test_run_progress_widget(self):
        html = render.run_progress_html("Collecting from GitHub", "since 08:00", 20, 3.2)
        self.assertIn('id="run-fill"', html)
        self.assertIn("width:20%", html)
        self.assertIn("/api/run", html)
        self.assertIn("3s", html)

    def test_item_key_from_url(self):
        self.assertEqual(
            render.item_key_from_url("https://github.com/acme/orbit/pull/111744"),
            "acme/orbit#111744",
        )
        self.assertEqual(render.item_key_from_url("https://example.com/x"), "")

    def test_digest_body_is_themeable(self):
        digest = {"headline": "h", "needs_you": [], "projects": [], "ideas": [], "risks": [], "next_actions": []}
        meta = {"when": datetime(2026, 11, 23, 8, 0, tzinfo=timezone.utc), "counts": {}}
        themed = render.digest_body_html(digest, meta, palette=render.PAGE)
        plain = render.digest_body_html(digest, meta)
        self.assertIn("var(--ink)", themed)
        self.assertIn("#0f172a", plain)


class FallbackTests(unittest.TestCase):
    def test_fallback_marks_review_requests(self):
        context = {
            "new": [
                {
                    "state": "open",
                    "involvement": ["review requested"],
                    "repo": "acme/orbit",
                    "number": 7,
                    "url": "https://x",
                }
            ],
            "changed": [],
            "idle": [],
            "activity": [],
            "stats": {"open_items": 1, "new": 1, "changed": 0, "commits": 0},
        }
        result = digest.fallback_digest(context)
        self.assertEqual(len(result["needs_you"]), 1)
        self.assertIn("waits for your review", result["needs_you"][0]["title"])


if __name__ == "__main__":
    unittest.main()
