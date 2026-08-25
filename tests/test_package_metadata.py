from __future__ import annotations

import tomllib
from pathlib import Path

import memorygraph

ROOT = Path(__file__).resolve().parents[1]


def test_beta_version_and_classifier_stay_consistent() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["version"] == memorygraph.__version__ == "0.1.0b1"
    assert "Development Status :: 4 - Beta" in project["classifiers"]
    assert (ROOT / "docs/releases/v0.1.0b1.md").exists()
