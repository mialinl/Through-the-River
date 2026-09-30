import json
import tempfile
import unittest
from pathlib import Path

from river import index, store, workbench
from river.split import split

from test_split import REROLL, T, conv, msg


def setup(d):
    export = d / "conversations.json"
    export.write_text(json.dumps([
        conv("c-15aaaaaa", "15", REROLL),
        conv("c-12bbbbbb", "12", [msg("x1", "00000000-0000-4000-8000-000000000000", "human",
                                      "2026-08-07T00:00:00Z", [T("铁盒 和 像素边牧")])]),
    ]))
    paths = store.Paths(d / "data")
    split(export, paths.root)
    return export, paths


class WorkbenchTest(unittest.TestCase):
    def test_publish_merges_edits_and_survives_reimport(self):
        with tempfile.TemporaryDirectory() as d:
            export, paths = setup(Path(d))
            workbench.edit(paths, "15", note="opus5.5 那一窗", name="十五")
            workbench.publish(paths)
            pub = store.read_json(store.files_for_uuid(paths.windows, "c-15aaaaaa")[0])
            self.assertEqual((pub["name"], pub["original_name"], pub["note"]), ("十五", "15", "opus5.5 那一窗"))
            self.assertEqual(len(pub["messages"]), 4)

            # 15 窗聊多了，重新导入：原文换新，说明还在
            more = REROLL + [msg("a3", "b3", "human", "2026-09-02T00:00:00Z", [T("早")])]
            export.write_text(json.dumps([conv("c-15aaaaaa", "15", more)]))
            split(export, paths.root)
            rows = dict((m["uuid"], s) for s, m in workbench.publish(paths))
            self.assertEqual(rows["c-15aaaaaa"], "updated")
            self.assertEqual(rows["c-12bbbbbb"], "unchanged")  # 这次导出里没有 12，也不删
            pub = store.read_json(store.files_for_uuid(paths.windows, "c-15aaaaaa")[0])
            self.assertEqual((pub["message_count"], pub["note"]), (5, "opus5.5 那一窗"))
            self.assertEqual(len(store.window_files(paths.windows)), 2)

    def test_hide_and_renumber(self):
        with tempfile.TemporaryDirectory() as d:
            _, paths = setup(Path(d))
            workbench.publish(paths)
            workbench.edit(paths, "12", hidden=True)
            workbench.edit(paths, "15", window="3")
            workbench.publish(paths)
            self.assertEqual([store.read_json(p)["window"] for p in store.window_files(paths.windows)], ["3"])
            workbench.edit(paths, "c-12b", hidden=False)
            workbench.publish(paths)
            self.assertEqual(len(store.window_files(paths.windows)), 2)

    def test_search_and_read(self):
        with tempfile.TemporaryDirectory() as d:
            _, paths = setup(Path(d))
            workbench.publish(paths)
            index.build(paths)
            hits, total = index.search(paths, "像素边牧")      # 全文索引
            self.assertEqual((total, hits[0].window, hits[0].i), (1, "12", 0))
            hits, total = index.search(paths, "铁盒")          # 两个字，走逐条扫描
            self.assertEqual(total, 1)
            self.assertIn("【铁盒】", hits[0].snippet(["铁盒"]))
            hits, total = index.search(paths, "查到", window="15", sender="assistant")
            self.assertEqual([h.i for h in hits], [1])
            _, total = index.search(paths, "记忆桶")  # 工具返回不进搜索
            self.assertEqual(total, 0)
            _, total = index.search(paths, "你好", date_from="2026-09-02")
            self.assertEqual(total, 0)
            w, rows = index.read(paths, "15", tail=True, around=2)
            self.assertEqual([r["i"] for r in rows], [2, 3])
            w, rows = index.tail_chars(paths, chars=3)          # 不指定窗口 = 最后说过话的那窗
            self.assertEqual((w["window"], [r["i"] for r in rows]), ("15", [2, 3]))
            self.assertEqual([r["tools"] for r in index.read(paths, "15", at=1, around=0)[1]], ["breath"])


class CliTest(unittest.TestCase):
    def test_hide_new_then_show_some(self):
        import contextlib, io
        from river.cli import main
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            export = d / "old.json"
            root = "00000000-0000-4000-8000-000000000000"
            export.write_text(json.dumps([
                conv("old-0001", "1", [msg("o1", root, "human", "2026-05-01T00:00:00Z", [T("第一窗")])]),
                conv("old-0002", "工作的事", [msg("o2", root, "human", "2026-05-02T00:00:00Z", [T("不相干")])]),
                conv("old-0003", "2", [msg("o3", root, "human", "2026-05-03T00:00:00Z", [T("第二窗")])]),
            ]))
            data = str(d / "data")
            with contextlib.redirect_stdout(io.StringIO()):
                main(["--data", data, "split", str(export), "--label", "旧号", "--hide-new"])
                paths = store.Paths(Path(data))
                self.assertEqual(store.window_files(paths.windows), [])      # 全藏着，什么都没发布
                main(["--data", data, "edit", "1", "old-0003", "--show"])      # 一次放出两窗
                main(["--data", data, "publish"])
                # 同一份再导一次：藏着的还藏着，放出来的还在
                main(["--data", data, "split", str(export), "--hide-new"])
            names = sorted(store.read_json(p)["name"] for p in store.window_files(paths.windows))
            self.assertEqual(names, ["1", "2"])
            self.assertEqual({store.read_json(p)["account_label"] for p in store.window_files(paths.windows)}, {"旧号"})
            _, total = index.search(paths, "不相干")
            self.assertEqual(total, 0)

    def test_import_accepts_zip_folder_json_and_refuses_unknown(self):
        import contextlib, io, zipfile
        from river.cli import main
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            root = "00000000-0000-4000-8000-000000000000"
            convs = json.dumps([conv("z-000001", "1", [msg("z1", root, "human", "2026-05-01T00:00:00Z", [T("嗨")])])])
            folder = d / "解压出来的"
            (folder / "sub").mkdir(parents=True)
            (folder / "sub" / "conversations.json").write_text(convs)
            (folder / "users.json").write_text("[]")
            with zipfile.ZipFile(d / "export.zip", "w") as z:
                z.writestr("users.json", "[]")
                z.writestr("conversations.json", convs)
            weird = d / "weird.json"
            weird.write_text(json.dumps({"messages": []}))
            data = str(d / "data")
            with contextlib.redirect_stdout(io.StringIO()):
                for src in (d / "export.zip", folder, folder / "sub" / "conversations.json"):
                    main(["--data", data, "import", str(src)])
                main(["--data", data, "split", str(d / "export.zip")])  # 旧名字还能用
                with self.assertRaises(SystemExit) as e:
                    main(["--data", data, "import", str(weird)])
            self.assertIn("还不认识", str(e.exception))
            self.assertEqual(len(store.window_files(store.Paths(Path(data)).windows)), 1)


if __name__ == "__main__":
    unittest.main()
