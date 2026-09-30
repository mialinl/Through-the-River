import json
import tempfile
import unittest
from pathlib import Path

from river.sources.claude_export import ROOT_PARENT, main_line, to_window, window_number
from river.split import split


def msg(uuid, parent, sender, ts, blocks, text=""):
    return {
        "uuid": uuid, "parent_message_uuid": parent, "sender": sender,
        "created_at": ts, "updated_at": ts, "text": text, "content": blocks,
        "attachments": [], "files": [],
    }


def conv(uuid, name, messages, updated="2026-09-01T00:00:00Z"):
    return {
        "uuid": uuid, "name": name, "summary": "", "created_at": messages[0]["created_at"] if messages else "2026-09-01T00:00:00Z",
        "updated_at": updated, "account": {"uuid": "acct"}, "chat_messages": messages,
    }


T = lambda s: {"type": "text", "text": s}

# a1 → b1(重roll掉的) / b2(主线) → a2 → b3
REROLL = [
    msg("a1", ROOT_PARENT, "human", "2026-09-01T00:00:00Z", [T("你好")]),
    msg("b1", "a1", "assistant", "2026-09-01T00:00:01Z", [T("断网前的回复")]),
    msg("b2", "a1", "assistant", "2026-09-01T00:00:05Z", [
        T("我先查一下"),
        {"type": "tool_use", "id": "t1", "name": "breath", "input": {}},
        {"type": "tool_result", "tool_use_id": "t1", "name": "breath", "is_error": False,
         "content": [{"type": "text", "text": "记忆桶", "uuid": "x"}]},
        T("查到了"),
    ], text="我先查一下\n```\nThis block is not supported on your current device yet.\n```\n查到了"),
    msg("a2", "b2", "human", "2026-09-01T00:01:00Z", [T("嗯嗯")]),
    msg("b3", "a2", "assistant", "2026-09-01T00:01:05Z", [
        {"type": "flag", "flag": "self_harm_risk", "helpline": None}, T("在")]),
]

SHELL = [msg("s1", ROOT_PARENT, "human", "2026-08-01T00:00:00Z", [])]


class SplitTest(unittest.TestCase):
    def test_main_line_drops_reroll(self):
        self.assertEqual([m["uuid"] for m in main_line(REROLL)], ["a1", "b2", "a2", "b3"])

    def test_window_format(self):
        w = to_window(conv("c-15", "💬 14", REROLL))
        self.assertEqual(w["window"], "14")
        self.assertEqual(w["dropped_rerolls"], 1)
        b2 = w["messages"][1]
        self.assertEqual(b2["text"], "我先查一下\n\n查到了")  # 不带占位符
        self.assertEqual([b["type"] for b in b2["blocks"]], ["text", "tool_use", "tool_result", "text"])
        self.assertEqual(w["messages"][3]["flags"], ["self_harm_risk"])

    def test_old_format_without_blocks_or_parents(self):
        old = [
            {"uuid": "o1", "sender": "human", "created_at": "2026-05-01T00:00:00Z", "text": "旧格式第一句"},
            {"uuid": "o2", "sender": "assistant", "created_at": "2026-05-01T00:00:05Z", "text": "旧格式回复"},
        ]
        w = to_window({"uuid": "old", "name": "3", "created_at": "2026-05-01T00:00:00Z", "chat_messages": old})
        self.assertEqual([m["text"] for m in w["messages"]], ["旧格式第一句", "旧格式回复"])
        self.assertEqual(w["dropped_rerolls"], 0)

    def test_window_number(self):
        self.assertEqual(window_number("14.5with opus5"), "14.5")
        self.assertIsNone(window_number(""))

    def test_split_is_idempotent_and_skips_shells(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            export = d / "conversations.json"
            export.write_text(json.dumps([conv("c-15aaaaaa", "15", REROLL), conv("c-shell", "", SHELL)]))
            data = d / "data"

            r1 = split(export, data)
            self.assertEqual(r1.shells, 1)
            self.assertEqual([s for s, _, _ in r1.rows], ["new"])
            files = list((data / "workbench").glob("*.json"))
            self.assertEqual(len(files), 1)
            self.assertTrue(files[0].name.startswith("2026-09-01_15_c-15aaaa"))

            r2 = split(export, data)
            self.assertFalse(r2.raw_new)
            self.assertEqual([s for s, _, _ in r2.rows], ["unchanged"])

            # 窗口继续聊了、还改了名：旧文件被替换，不留两份
            more = REROLL + [msg("a3", "b3", "human", "2026-09-02T00:00:00Z", [T("早")])]
            export.write_text(json.dumps([conv("c-15aaaaaa", "15 新名字", more)]))
            r3 = split(export, data)
            self.assertEqual([s for s, _, _ in r3.rows], ["updated"])
            files = list((data / "workbench").glob("*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(json.loads(files[0].read_text())["message_count"], 5)
            self.assertEqual(len(list((data / "raw").glob("*.json"))), 2)


if __name__ == "__main__":
    unittest.main()
