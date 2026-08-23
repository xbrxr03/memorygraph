from __future__ import annotations

import argparse
from pathlib import Path

from memorygraph.integrations.codex import probe_codex_mcp, report_to_json


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe a project-scoped MemoryGraph Codex MCP setup."
    )
    parser.add_argument(
        "project",
        nargs="?",
        default=".",
        help="Project directory containing .codex/config.toml",
    )
    parser.add_argument(
        "--database-mode",
        choices=("temporary", "project"),
        default="temporary",
        help="Use a disposable probe database or the configured project database.",
    )
    parser.add_argument(
        "--config-only",
        action="store_true",
        help="Probe only the configured MCP command and skip the local Python module fallback.",
    )
    args = parser.parse_args()
    launch_sources = ("config",) if args.config_only else ("config", "local_module")
    report = probe_codex_mcp(
        Path(args.project),
        database_mode=args.database_mode,
        launch_sources=launch_sources,
    )
    print(report_to_json(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
