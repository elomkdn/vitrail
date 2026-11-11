"""Engine and handler tests. Run from the repository's parent: python -m pytest vitrail/tests
or directly: python vitrail/tests/test_engine.py"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_package():
    """Import the plugin folder as the package `vitrail_under_test`, as Hermes would."""
    name = "vitrail_under_test"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


pkg = _load_package()
engine = importlib.import_module("vitrail_under_test.engine")
handlers = importlib.import_module("vitrail_under_test.handlers")
hooks = importlib.import_module("vitrail_under_test.hooks")

GOOD = """#version 300 es
uniform vec3 iResolution;
uniform float iTime;
uniform float speed; // @slider 0 2 0.5
uniform vec3 u_tintColor; // @color #FF8800
uniform bool rings; // @toggle on
uniform float lost;
void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 uv = fragCoord / iResolution.xy; // main() is not here
  fragColor = vec4(uv, 0.5 + 0.5 * sin(iTime * speed), 1.0);
}
"""


class CheckTests(unittest.TestCase):
    def test_normalize_keeps_line_numbers(self):
        checked = engine.check(GOOD)
        lines = checked["code"].split("\n")
        self.assertEqual(lines[0], "")
        self.assertEqual(lines[1], "")
        self.assertEqual(lines[2], "")
        self.assertTrue(lines[3].startswith("uniform float speed"))
        self.assertNotIn("#version", checked["code"])
        self.assertNotIn("iResolution;", checked["code"])

    def test_controls(self):
        checked = engine.check(GOOD)
        by_name = {c["name"]: c for c in checked["controls"]}
        self.assertEqual(set(by_name), {"speed", "u_tintColor", "rings"})
        self.assertEqual(by_name["speed"]["min"], 0)
        self.assertEqual(by_name["speed"]["max"], 2)
        self.assertEqual(by_name["speed"]["value"], 0.5)
        self.assertAlmostEqual(by_name["speed"]["step"], 0.01)
        self.assertEqual(by_name["u_tintColor"]["value"], "#ff8800")
        self.assertEqual(by_name["u_tintColor"]["label"], "Tint Color")
        self.assertIs(by_name["rings"]["value"], True)
        self.assertTrue(any("lost" in w for w in checked["warnings"]))

    def test_slider_default_is_clamped(self):
        controls, _ = engine.parse_controls("uniform float a; // @slider 0 1 5\n")
        self.assertEqual(controls[0]["value"], 1)

    def test_rejections(self):
        cases = {
            "": "empty",
            "void main() { }": "mainImage",
            "void mainImage(out vec4 c, in vec2 p) { gl_FragColor = vec4(1.0); }": "gl_FragColor",
            "void mainImage(out vec4 c, in vec2 p) { c = texture(iChannel0, p); }": "procedural",
            "void mainImage(out vec4 c, in vec2 p) { c = vec4(1.0; }": "Unbalanced",
            "void mainImage(out vec4 c, in vec2 p) {} void main() {}": "Remove",
            "float x = 1.0;": "entry point",
        }
        for code, expected in cases.items():
            with self.assertRaises(engine.ShaderError) as caught:
                engine.check(code)
            self.assertIn(expected, str(caught.exception), code)
        with self.assertRaises(engine.ShaderError):
            engine.check("// pad\n" * 20000 + "void mainImage(out vec4 c, in vec2 p) {}")

    def test_comment_only_main_is_fine(self):
        engine.check("// void main() {}\nvoid mainImage(out vec4 c, in vec2 p) { c = vec4(1.0); }")

    def test_slug_and_title(self):
        self.assertEqual(engine.slugify("Lagune au Crépuscule !"), "lagune-au-cr-puscule")
        self.assertEqual(engine.slugify("***"), "shader")
        self.assertEqual(engine.clean_title("  a\n b  "), "a b")
        self.assertEqual(engine.clean_title(None), "Untitled shader")

    def test_standalone_html_is_inert(self):
        html = engine.standalone_html('<img src=x onerror=1> "t"',
                                      'void mainImage(){} // </script><script>alert(1)</script>', [])
        self.assertNotIn("<img", html)
        self.assertEqual(html.count("</script>"), 1)
        self.assertEqual(html.count("<script>"), 1)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = engine.Store(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_get_list_delete(self):
        saved = self.store.save("Lagoon", engine.check(GOOD), "water")
        self.assertRegex(saved["id"], engine.ID_RE)
        self.assertTrue(Path(saved["html"]).is_file())
        found = self.store.get(saved["id"])
        self.assertEqual(found["title"], "Lagoon")
        self.assertIsNone(found["report"])
        self.assertEqual([r["id"] for r in self.store.list()], [saved["id"]])
        self.assertIn("code", self.store.list(with_code=True)[0])
        self.assertTrue(self.store.delete(saved["id"]))
        self.assertEqual(self.store.list(), [])

    def test_bad_ids_never_touch_the_disk(self):
        for bad in ("../x", "a/../../etc", "", "UPPER-abcdef", "x" * 80, None, "a-zzzzzz"):
            self.assertIsNone(self.store.get(bad))
            with self.assertRaises(engine.ShaderError):
                self.store.delete(bad)
            with self.assertRaises(engine.ShaderError):
                self.store.write_report(bad, {"ok": True})

    def test_report_and_poster(self):
        saved = self.store.save("A", engine.check(GOOD))
        jpeg = b"\xff\xd8\xff\xe0" + b"0" * 64
        self.assertTrue(self.store.write_report(
            saved["id"], {"ok": True, "log": "", "compile_ms": 12.34, "frame_ms": "x", "extra": 1}, jpeg))
        found = self.store.get(saved["id"])
        self.assertEqual(found["report"]["compile_ms"], 12.3)
        self.assertNotIn("frame_ms", found["report"])
        self.assertNotIn("extra", found["report"])
        self.assertTrue(found["poster"].endswith("poster.jpg"))
        # Not a JPEG: ignored.
        other = self.store.save("B", engine.check(GOOD))
        self.store.write_report(other["id"], {"ok": True}, b"<html>")
        self.assertIsNone(self.store.get(other["id"])["poster"])

    def test_failed_shaders_leave_the_library(self):
        saved = self.store.save("A", engine.check(GOOD))
        self.store.write_report(saved["id"], {"ok": False, "log": "ERROR: 0:3: boom"})
        self.assertEqual(self.store.list(), [])

    def test_desktop_heartbeat(self):
        self.assertFalse(self.store.desktop_is_live())
        self.store.desktop_seen()
        self.assertTrue(self.store.desktop_is_live())
        self.assertEqual(self.store.desktop_caps(), {"tool_media": False, "directive": False})
        self.store.desktop_seen({"tool_media": None, "directive": True})
        self.assertEqual(self.store.desktop_caps(), {"tool_media": False, "directive": True})
        self.store.desktop_seen()  # a later heartbeat without a body keeps what it knew
        self.assertEqual(self.store.desktop_caps(), {"tool_media": False, "directive": True})


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tools = handlers.make_handlers(lambda: self.root)
        self._ask = handlers._ask_desktop

    def tearDown(self):
        handlers._ask_desktop = self._ask
        self.tmp.cleanup()

    def test_render_without_desktop(self):
        started = time.monotonic()
        out = json.loads(self.tools["vitrail_render"]({"title": "Lagoon", "code": GOOD}))
        self.assertLess(time.monotonic() - started, 1.0)  # nobody listening: no wait
        self.assertTrue(out["success"])
        self.assertTrue(out["deliver"].startswith("MEDIA:"))
        self.assertTrue(out["deliver"].endswith(out["id"] + ".html"))
        self.assertIn("not verified", out["gpu"])
        self.assertEqual(out["controls"], ["speed", "u_tintColor", "rings"])

    def test_render_rejects_bad_input_as_json(self):
        for args in ({}, {"code": 5}, {"code": "void main(){}"}, None, "x"):
            out = json.loads(self.tools["vitrail_render"](args))
            self.assertIs(out["success"], False)
            self.assertTrue(out["error"])

    def _desktop(self, verdict):
        """Stand in for the Desktop half: answer the compile request after a short delay."""
        store = engine.Store(self.root)
        store.desktop_seen()

        def ask(shader_id, code):
            jpeg = b"\xff\xd8\xff\xe0" + b"0" * 32
            threading.Timer(0.15, lambda: store.write_report(shader_id, verdict, jpeg)).start()
            return True

        handlers._ask_desktop = ask

    def test_render_with_gpu_ok(self):
        self._desktop({"ok": True, "log": "", "compile_ms": 40, "frame_ms": 55})
        out = json.loads(self.tools["vitrail_render"]({"title": "Lagoon", "code": GOOD}))
        self.assertTrue(out["success"])
        self.assertIs(out["gpu"]["compiled"], True)
        self.assertIn("Slow", out["gpu"]["note"])
        self.assertTrue(out["still"].endswith("poster.jpg"))

    def test_render_with_gpu_error_returns_the_log(self):
        self._desktop({"ok": False, "log": "ERROR: 0:9: 'uvv' : undeclared identifier"})
        out = json.loads(self.tools["vitrail_render"]({"title": "Lagoon", "code": GOOD}))
        self.assertIs(out["success"], False)
        self.assertIn("undeclared identifier", out["log"])
        self.assertNotIn("deliver", out)

    def test_directive_delivery_for_a_desktop_without_tool_media(self):
        store = engine.Store(self.root)
        store.desktop_seen({"tool_media": False, "directive": True})
        handlers._ask_desktop = lambda shader_id, code: False
        out = json.loads(self.tools["vitrail_render"]({"title": "Lagoon", "code": GOOD}))
        self.assertEqual(out["deliver"], '::vitrail{id="%s"}' % out["id"])
        self.assertTrue(out["instruction"].endswith(out["deliver"]))
        store.desktop_seen({"tool_media": True, "directive": True})
        out = json.loads(self.tools["vitrail_render"]({"title": "Lagoon", "code": GOOD}))
        self.assertTrue(out["deliver"].startswith("MEDIA:"))

    def test_library(self):
        made = json.loads(self.tools["vitrail_render"]({"title": "Lagoon", "code": GOOD}))
        listing = json.loads(self.tools["vitrail_library"]({}))
        self.assertEqual(listing["count"], 1)
        one = json.loads(self.tools["vitrail_library"]({"id": made["id"]}))
        self.assertIn("mainImage", one["code"])
        missing = json.loads(self.tools["vitrail_library"]({"id": "nope-000000"}))
        self.assertIs(missing["success"], False)


class HookTests(unittest.TestCase):
    def test_media_line_is_appended_once(self):
        result = json.dumps({"success": True, "deliver": "MEDIA:/data/a-abcdef/a-abcdef.html"})
        hooks.record_media(tool_name="vitrail_render", args={}, result=result,
                           session_id="s", turn_id="t")
        out = hooks.append_missing_media(response_text="Here it is.", session_id="s", turn_id="t")
        self.assertTrue(out.endswith("\n\nMEDIA:/data/a-abcdef/a-abcdef.html"))
        self.assertIsNone(hooks.append_missing_media(response_text="again", session_id="s", turn_id="t"))

    def test_already_delivered_or_failed(self):
        path = "/data/b-abcdef/b-abcdef.html"
        ok = json.dumps({"success": True, "deliver": f"MEDIA:{path}"})
        hooks.record_media(tool_name="vitrail_render", result=ok, session_id="s", turn_id="u")
        self.assertIsNone(hooks.append_missing_media(
            response_text=f"Done.\n\nMEDIA:{path}", session_id="s", turn_id="u"))
        failed = json.dumps({"success": False, "error": "x", "deliver": f"MEDIA:{path}"})
        hooks.record_media(tool_name="vitrail_render", result=failed, session_id="s", turn_id="v")
        self.assertIsNone(hooks.append_missing_media(response_text="x", session_id="s", turn_id="v"))
        hooks.record_media(tool_name="other_tool", result=ok, session_id="s", turn_id="w")
        self.assertIsNone(hooks.append_missing_media(response_text="x", session_id="s", turn_id="w"))


    def test_only_the_final_render_of_a_turn_is_appended(self):
        for shader in ("a-aaaaaa", "b-bbbbbb"):
            hooks.record_media(tool_name="vitrail_render", session_id="s", turn_id="it",
                               result=json.dumps({"success": True, "deliver": f'::vitrail{{id="{shader}"}}'}))
        out = hooks.append_missing_media(response_text="Done.", session_id="s", turn_id="it")
        self.assertEqual(out, 'Done.\n\n::vitrail{id="b-bbbbbb"}')
        # The model delivered the first one itself: nothing is added.
        for shader in ("a-aaaaaa", "b-bbbbbb"):
            hooks.record_media(tool_name="vitrail_render", session_id="s", turn_id="it2",
                               result=json.dumps({"success": True, "deliver": f'::vitrail{{id="{shader}"}}'}))
        self.assertIsNone(hooks.append_missing_media(
            response_text='Both:\n\n::vitrail{id="a-aaaaaa"}', session_id="s", turn_id="it2"))

    def test_directive_line_is_appended(self):
        line = '::vitrail{id="lagoon-abcdef"}'
        ok = json.dumps({"success": True, "deliver": line})
        hooks.record_media(tool_name="vitrail_render", result=ok, session_id="s", turn_id="d1")
        out = hooks.append_missing_media(response_text="Here it is.\n", session_id="s", turn_id="d1")
        self.assertEqual(out, "Here it is.\n\n" + line)
        hooks.record_media(tool_name="vitrail_render", result=ok, session_id="s", turn_id="d2")
        self.assertIsNone(hooks.append_missing_media(
            response_text="Done.\n\n" + line, session_id="s", turn_id="d2"))
        evil = json.dumps({"success": True, "deliver": '::other{id="x"}'})
        hooks.record_media(tool_name="vitrail_render", result=evil, session_id="s", turn_id="d3")
        self.assertIsNone(hooks.append_missing_media(response_text="x", session_id="s", turn_id="d3"))


class RegisterTests(unittest.TestCase):
    def test_register_declares_what_the_manifest_says(self):
        seen = {"tools": [], "hooks": [], "skills": []}

        class Ctx:
            def register_tool(self, name, **kw):
                assert callable(kw["handler"]) and kw["schema"]["name"] == name
                seen["tools"].append(name)

            def register_hook(self, event, callback, **kw):
                seen["hooks"].append(event)

            def register_skill(self, name, path, **kw):
                assert Path(path).is_file()
                seen["skills"].append(name)

        pkg.register(Ctx())
        manifest = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
        for name in seen["tools"] + seen["hooks"]:
            self.assertIn(f"  - {name}\n", manifest)
        self.assertEqual(seen["tools"], ["vitrail_render", "vitrail_library"])
        self.assertEqual(seen["hooks"], ["post_tool_call", "transform_llm_output"])
        self.assertEqual(seen["skills"], ["shader-studio"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
