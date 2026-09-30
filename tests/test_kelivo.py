import sqlite3
import tempfile
import unittest
from pathlib import Path

from river import index, store, workbench
from river.split import split

US = 1_783_000_000_000_000  # 微秒


def make_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE conversation_rows (id TEXT, title TEXT, created_at INTEGER, updated_at INTEGER,
        version_selections_json TEXT DEFAULT '{}');
    CREATE TABLE message_rows (id TEXT, conversation_id TEXT, role TEXT, timestamp INTEGER, model_id TEXT,
        group_id TEXT, version INTEGER DEFAULT 0, message_order INTEGER);
    CREATE TABLE message_part_rows (part_id INTEGER PRIMARY KEY, conversation_id TEXT, revision_id TEXT,
        ordinal INTEGER, kind TEXT, payload TEXT);
    """)
    con.execute("INSERT INTO conversation_rows VALUES ('kv-10aaaa', '10 花园新窗口开张', ?, ?, '{\"g2\": 0}')", (US, US))
    con.execute("INSERT INTO conversation_rows VALUES ('kv-empty0', 'test', ?, ?, '{}')", (US, US))
    msgs = [  # id, role, t, group, version, order
        ("m1", "user", 1, "g1", 0, 0),
        ("m2a", "assistant", 2, "g2", 0, 1),   # 选中的版本
        ("m2b", "assistant", 3, "g2", 1, 2),   # 重 roll 掉的
        ("m3", "user", 4, "g3", 0, 3),
        ("m4", "assistant", 5, "g4", 0, 4),
    ]
    for mid, role, t, g, v, o in msgs:
        con.execute("INSERT INTO message_rows VALUES (?, 'kv-10aaaa', ?, ?, 'claude-opus-4-6', ?, ?, ?)",
                    (mid, role, US + t * 1_000_000, g, v, o))
    parts = [("m1", "text", "花园开张啦"), ("m2a", "reasoning", "想一想"), ("m2a", "text", "恭喜开张"),
             ("m2b", "text", "被重roll掉的回答"), ("m3", "text", "查一下记忆"),
             ("m4", "tool_call", '{"id":"t1","name":"breath","arguments":{},"content":"记忆桶内容","metadata":{}}'),
             ("m4", "text", "查到了")]
    for n, (rid, kind, payload) in enumerate(parts):
        con.execute("INSERT INTO message_part_rows (conversation_id, revision_id, ordinal, kind, payload)"
                    " VALUES ('kv-10aaaa', ?, ?, ?, ?)", (rid, n, kind, payload))
    con.commit()
    con.close()


class KelivoTest(unittest.TestCase):
    def test_import_kelivo_db(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            db = d / "kelivo.db"
            make_db(db)
            paths = store.Paths(d / "data")
            r = split(db, paths.root, account_label="Kelivo")
            self.assertEqual(r.kind, "kelivo")
            self.assertEqual(r.shells, 1)  # 没有消息的对话当空壳跳过
            (_, _, w), = r.rows
            self.assertEqual((w["window"], w["source"], w["message_count"], w["dropped_rerolls"]), ("10", "kelivo", 4, 1))
            self.assertEqual([m["text"] for m in w["messages"]], ["花园开张啦", "恭喜开张", "查一下记忆", "查到了"])
            self.assertEqual([b["type"] for b in w["messages"][1]["blocks"]], ["thinking", "text"])
            self.assertEqual(w["messages"][0]["created_at"], "2026-07-02T13:46:41Z")

            workbench.publish(paths)
            index.build(paths)
            self.assertEqual(index.search(paths, "被重roll掉")[1], 0)
            self.assertEqual(index.search(paths, "花园开张")[1], 1)
            self.assertEqual(index.read(paths, "10", at=3, around=0)[1][0]["tools"], "breath")

            self.assertFalse(split(db, paths.root).raw_new)  # 同一份再导：原文不重复存


if __name__ == "__main__":
    unittest.main()
