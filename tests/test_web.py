import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from river import store
from river.split import import_file
from river.web import App, make_handler

from test_split import REROLL, conv


class WebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        export = d / "e.json"
        export.write_text(json.dumps([conv("c-15aaaaaa", "15", REROLL)]))
        self.paths = store.Paths(d / "data")
        import_file(export, self.paths.root)
        self.app = App(self.paths, store.DEFAULT_TZ, "零零")
        self.app.publish()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), None)
        port = self.server.server_address[1]
        self.server.RequestHandlerClass = make_handler(self.app, port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def req(self, path, body=None, token=True):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-River-Token"] = self.app.token
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, headers=headers,
                                   method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_read_edit_publish_search_drop(self):
        status, ws = self.req("/api/windows")
        self.assertEqual((status, ws[0]["window"], ws[0]["state"]), (200, "15", "published"))
        _, page = self.req("/api/messages?uuid=c-15aaaaaa&offset=1&limit=2")
        self.assertEqual([m["i"] for m in page["messages"]], [1, 2])
        self.assertEqual(page["messages"][0]["tools"], ["breath"])

        # 没有口令改不了
        self.assertEqual(self.req("/api/edit", {"uuid": "c-15aaaaaa", "note": "便条"}, token=False)[0], 403)
        self.assertEqual(self.req("/api/edit", {"uuid": "c-15aaaaaa", "note": "便条"})[0], 200)
        self.assertEqual(self.req("/api/windows")[1][0]["state"], "changed")
        self.req("/api/publish", {})
        self.assertEqual(self.req("/api/windows")[1][0]["state"], "published")

        _, found = self.req("/api/search?q=" + urllib.parse.quote("查到"))
        self.assertEqual((found["total"], found["hits"][0]["i"]), (1, 1))

        _, r = self.req("/api/drop", {"uuids": ["c-15aaaaaa"]})
        self.assertEqual((r["dropped"], r["windows"]), (1, 0))


if __name__ == "__main__":
    unittest.main()
