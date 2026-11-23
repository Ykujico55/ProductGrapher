import tempfile
import textwrap
import unittest
from pathlib import Path

from how2trust.project import build_graph


def graph_for(files):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for name, body in files.items():
            dest = root / name
            dest.parent.mkdir(exist_ok=True, parents=True)
            dest.write_text(textwrap.dedent(body).lstrip(), encoding="utf8")
        return build_graph(root)


class CliEntryTest(unittest.TestCase):
    def test_guarded_command_launchers_reexport_and_source_locations(self):
        graph = graph_for({
            "src/app/__init__.py": "",
            "src/app/commands.py": """
                import sys as system
                try:
                    import click as framework
                except ImportError:
                    system.exit(1)
                @framework.group()
                @framework.pass_context
                def cli(ctx): pass
                @cli.command("open")
                def open_project(): pass
            """,
            "src/app/__main__.py": """
                from .commands import cli
                if __name__ == "__main__":
                    cli()
            """,
            "setup.py": """
                from setuptools import setup as package
                package(entry_points={"console_scripts": [
                    "my-app = app.__main__:cli",
                    "external = other_project:cli",
                    "missing = app.commands:missing"
                ]})
            """,
        })
        nodes = {n["id"]: n for n in graph["nodes"]}
        endpoints = {n["label"]: n for n in nodes.values() if n["kind"] == "entry"}
        self.assertEqual(set(endpoints), {"my-app", "src/app/__main__.py · __main__", "Click · cli", "open"})
        bound = {nodes[e["source"]]["label"]: (e["file"], e["line"], nodes[e["target"]]["qualname"])
                 for e in graph["edges"] if e["kind"] == "handles"}
        self.assertEqual(bound, {"my-app": ("setup.py", 3, "cli"),
                                 "src/app/__main__.py · __main__": ("src/app/__main__.py", 3, "cli"),
                                 "Click · cli": ("src/app/commands.py", 6, "cli"),
                                 "open": ("src/app/commands.py", 9, "open_project")})
        self.assertFalse(any(e["kind"] == "calls" and e["file"] == "src/app/__main__.py" for e in graph["edges"]))

    def test_conditional_import_and_dynamic_console_target_stay_unresolved(self):
        graph = graph_for({
            "app.py": """
                try:
                    import click
                except ImportError:
                    pass
                @click.group()
                def cli(): pass
                def main(): pass
            """,
            "setup.py": """
                from setuptools import setup
                target = "runner = app:main"
                setup(entry_points={"console_scripts": [target, "absent = app:cli"]})
            """,
        })
        self.assertFalse(any(n["kind"] == "entry" for n in graph["nodes"]))

    def test_guard_binding_is_unavailable_inside_failed_import_handler(self):
        graph = graph_for({"lib.py": "def work(): pass", "app.py": """
            try:
                from lib import work
            except ImportError:
                work()
                raise
            work()
        """})
        calls = [e for e in graph["edges"] if e["kind"] == "calls"]
        self.assertEqual([(e["file"], e["line"]) for e in calls], [("app.py", 6)])
