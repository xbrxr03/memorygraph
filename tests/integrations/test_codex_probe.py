from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

from memorygraph.integrations.codex import inspect_codex_project_config, probe_codex_mcp


def test_inspect_codex_project_config_resolves_database_path_and_launcher_shape(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config_path = project / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        "\n".join(
            [
                "[mcp_servers.memorygraph]",
                f"command = {json.dumps(sys.executable)}",
                'args = ["-m", "memorygraph.mcp", ".memorygraph/memory.db"]',
                'default_tools_approval_mode = "writes"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    configuration = inspect_codex_project_config(project)

    assert configuration.ok is True
    assert configuration.command == sys.executable
    assert configuration.database_argument_index == 2
    assert configuration.database_argument == ".memorygraph/memory.db"
    assert (
        configuration.resolved_database_path == (project / ".memorygraph" / "memory.db").resolve()
    )
    assert configuration.resolved_command == Path(sys.executable)


def test_inspect_codex_project_config_flags_missing_database_argument_and_approval_mode(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config_path = project / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        "\n".join(
            [
                "[mcp_servers.memorygraph]",
                'command = "memorygraph-mcp"',
                "args = []",
                'default_tools_approval_mode = "always"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with patch(
        "memorygraph.integrations.codex.probe.shutil.which",
        return_value="/usr/bin/memorygraph-mcp",
    ):
        configuration = inspect_codex_project_config(project)

    codes = {issue.code for issue in configuration.issues}
    assert "approval_mode_unexpected" in codes
    assert "database_argument_missing" in codes


def test_probe_codex_mcp_runs_real_subprocess_using_project_config_and_local_module(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config_path = project / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        "\n".join(
            [
                "[mcp_servers.memorygraph]",
                f"command = {json.dumps(sys.executable)}",
                'args = ["-m", "memorygraph.mcp", ".memorygraph/memory.db"]',
                'default_tools_approval_mode = "writes"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = probe_codex_mcp(project)

    assert report.configuration.ok is True
    assert len(report.launches) == 2
    assert all(launch.ok for launch in report.launches)
    assert {launch.source for launch in report.launches} == {"config", "local_module"}
    assert all(launch.protocol_version == "2025-06-18" for launch in report.launches)
    assert all(
        launch.tools == ("recall", "record", "explain", "correct", "forget")
        for launch in report.launches
    )
    assert all(launch.record_observation_id is not None for launch in report.launches)
    assert all(
        launch.recall_hit_count and launch.recall_hit_count >= 1 for launch in report.launches
    )
    assert all(
        launch.forget_observation_id == launch.record_observation_id for launch in report.launches
    )
    assert all(not launch.database_path.exists() for launch in report.launches)


def test_probe_codex_mcp_reports_missing_configured_executable_actionably(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config_path = project / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        "\n".join(
            [
                "[mcp_servers.memorygraph]",
                'command = "missing-memorygraph-mcp"',
                'args = [".memorygraph/memory.db"]',
                'default_tools_approval_mode = "writes"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = probe_codex_mcp(project, launch_sources=("config",))

    assert report.ok is False
    assert len(report.launches) == 1
    launch = report.launches[0]
    assert launch.source == "config"
    assert launch.ok is False
    codes = {issue.code for issue in launch.issues}
    assert "executable_not_found" in codes
