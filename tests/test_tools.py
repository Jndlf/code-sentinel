"""Offline-Tests für den Zugriffsschutz (kein API-Key nötig): pytest tests/"""
import dataclasses

import pytest

from agent.config import Config
from agent.tools import Toolbox, ToolError


@pytest.fixture
def box(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "A.java").write_text("class A {\n  String s = \"x\";\n}\n")
    (tmp_path / ".env").write_text("SECRET=1")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("nope")
    cfg = dataclasses.replace(Config(), target_repo=tmp_path)
    return Toolbox(cfg)


def test_read_and_grep(box):
    assert "class A" in box.call("read_file", {"path": "src/A.java"})
    assert "src/A.java:2" in box.call("grep", {"pattern": "String"})


def test_path_traversal_blocked(box):
    with pytest.raises(ToolError):
        box.call("read_file", {"path": "../../etc/passwd"})


def test_secrets_and_denied_dirs_blocked(box):
    with pytest.raises(ToolError):
        box.call("read_file", {"path": ".env"})
    listing = box.call("list_files", {})
    assert ".env" not in listing and "node_modules" not in listing


def test_unknown_tool_rejected(box):
    with pytest.raises(ToolError):
        box.call("bash", {"command": "ls"})


def test_previous_reports_hidden(box):
    (box.root / "docs").mkdir()
    (box.root / "docs" / "security-review-x.md").write_text("Lösung")
    assert "security-review" not in box.call("list_files", {})
    with pytest.raises(ToolError):
        box.call("read_file", {"path": "docs/security-review-x.md"})
