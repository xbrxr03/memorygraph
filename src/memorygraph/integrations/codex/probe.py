"""Project-scoped Codex MCP inspection and subprocess probing."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from memorygraph import MemoryGraph
from memorygraph.mcp.server import _read_message, _write_message

ProbeSeverity = Literal["error", "warning"]
DatabaseMode = Literal["temporary", "project"]
LaunchSource = Literal["config", "local_module"]

_EXPECTED_TOOL_NAMES = ("recall", "record", "explain", "correct", "forget")


@dataclass(frozen=True, slots=True)
class ProbeIssue:
    """One actionable problem or warning found during inspection/probing."""

    severity: ProbeSeverity
    code: str
    message: str
    action: str | None = None


@dataclass(frozen=True, slots=True)
class CodexMCPConfiguration:
    """Validated project-scoped `.codex/config.toml` information."""

    project_directory: Path
    config_path: Path
    command: str | None
    args: tuple[str, ...]
    default_tools_approval_mode: str | None
    database_argument_index: int | None
    database_argument: str | None
    resolved_database_path: Path | None
    resolved_command: Path | None
    issues: tuple[ProbeIssue, ...]

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


@dataclass(frozen=True, slots=True)
class CodexMCPLaunchResult:
    """Outcome of one real subprocess probe."""

    source: LaunchSource
    argv: tuple[str, ...]
    database_mode: DatabaseMode
    database_path: Path
    ok: bool
    protocol_version: str | None
    tools: tuple[str, ...]
    issues: tuple[ProbeIssue, ...]
    record_observation_id: str | None = None
    recall_hit_count: int | None = None
    forget_observation_id: str | None = None


@dataclass(frozen=True, slots=True)
class CodexMCPProbeReport:
    """Combined configuration inspection and real server probes."""

    configuration: CodexMCPConfiguration
    launches: tuple[CodexMCPLaunchResult, ...]

    @property
    def ok(self) -> bool:
        return self.configuration.ok and all(launch.ok for launch in self.launches)


def inspect_codex_project_config(project_directory: str | Path) -> CodexMCPConfiguration:
    """Inspect `.codex/config.toml` without touching private Codex state."""

    project_path = Path(project_directory).expanduser().resolve()
    config_path = project_path / ".codex" / "config.toml"
    issues: list[ProbeIssue] = []
    command: str | None = None
    args: tuple[str, ...] = ()
    approval_mode: str | None = None
    database_argument_index: int | None = None
    database_argument: str | None = None
    resolved_database_path: Path | None = None
    resolved_command: Path | None = None

    if not config_path.exists():
        issues.append(
            ProbeIssue(
                severity="error",
                code="config_missing",
                message=f"Project config is missing: {config_path}",
                action=f"run `memorygraph install-codex --project {project_path}`",
            )
        )
        return CodexMCPConfiguration(
            project_directory=project_path,
            config_path=config_path,
            command=None,
            args=(),
            default_tools_approval_mode=None,
            database_argument_index=None,
            database_argument=None,
            resolved_database_path=None,
            resolved_command=None,
            issues=tuple(issues),
        )

    try:
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        issues.append(
            ProbeIssue(
                severity="error",
                code="config_parse_error",
                message=f"Could not parse {config_path}: {error}",
                action="fix the TOML syntax or rerun the installer",
            )
        )
        return CodexMCPConfiguration(
            project_directory=project_path,
            config_path=config_path,
            command=None,
            args=(),
            default_tools_approval_mode=None,
            database_argument_index=None,
            database_argument=None,
            resolved_database_path=None,
            resolved_command=None,
            issues=tuple(issues),
        )

    server = payload.get("mcp_servers", {}).get("memorygraph")
    if not isinstance(server, dict):
        issues.append(
            ProbeIssue(
                severity="error",
                code="memorygraph_server_missing",
                message="Missing `[mcp_servers.memorygraph]` section in `.codex/config.toml`.",
                action=f"run `memorygraph install-codex --project {project_path}`",
            )
        )
        return CodexMCPConfiguration(
            project_directory=project_path,
            config_path=config_path,
            command=None,
            args=(),
            default_tools_approval_mode=None,
            database_argument_index=None,
            database_argument=None,
            resolved_database_path=None,
            resolved_command=None,
            issues=tuple(issues),
        )

    raw_command = server.get("command")
    if not isinstance(raw_command, str) or not raw_command.strip():
        issues.append(
            ProbeIssue(
                severity="error",
                code="command_missing",
                message="`mcp_servers.memorygraph.command` must be a non-empty string.",
                action="set it to `memorygraph-mcp` or a Python launcher for `memorygraph.mcp`",
            )
        )
    else:
        command = raw_command.strip()
        resolved_command = _resolve_command(command, project_path)
        if resolved_command is None:
            issues.append(
                ProbeIssue(
                    severity="error",
                    code="executable_not_found",
                    message=(
                        "Configured MCP command is not executable from this environment: "
                        f"{command}"
                    ),
                    action=(
                        "install the `memorygraph-agent` package so `memorygraph-mcp` is on PATH, "
                        "or point the config at an absolute Python/module launcher"
                    ),
                )
            )

    raw_args = server.get("args", [])
    if raw_args is None:
        raw_args = []
    if not isinstance(raw_args, list) or any(not isinstance(item, str) for item in raw_args):
        issues.append(
            ProbeIssue(
                severity="error",
                code="args_invalid",
                message="`mcp_servers.memorygraph.args` must be an array of strings.",
                action="rerun the installer or fix the args list manually",
            )
        )
    else:
        args = tuple(item.strip() for item in raw_args)

    approval_mode_value = server.get("default_tools_approval_mode")
    if approval_mode_value is None:
        issues.append(
            ProbeIssue(
                severity="warning",
                code="approval_mode_missing",
                message="`default_tools_approval_mode` is not set.",
                action="set `default_tools_approval_mode = \"writes\"` to preserve write approvals",
            )
        )
    elif not isinstance(approval_mode_value, str):
        issues.append(
            ProbeIssue(
                severity="error",
                code="approval_mode_invalid",
                message="`default_tools_approval_mode` must be a string.",
                action="set it to `writes`",
            )
        )
    else:
        approval_mode = approval_mode_value.strip() or None
        if approval_mode != "writes":
            issues.append(
                ProbeIssue(
                    severity="warning",
                    code="approval_mode_unexpected",
                    message=(
                        "Configured approval mode is "
                        f"{approval_mode!r}, not the expected `writes`."
                    ),
                    action="set `default_tools_approval_mode = \"writes\"`",
                )
            )

    if command is not None and args:
        database_argument_index = _database_argument_index(command, args)
    elif command is not None and not args:
        database_argument_index = None

    if database_argument_index is None:
        issues.append(
            ProbeIssue(
                severity="error",
                code="database_argument_missing",
                message=(
                    "Could not determine which config argument is the MemoryGraph "
                    "database path."
                ),
                action=(
                    "Use `memorygraph-mcp <database>` or "
                    "`python -m memorygraph.mcp <database>` semantics"
                ),
            )
        )
    else:
        database_argument = args[database_argument_index]
        if not database_argument:
            issues.append(
                ProbeIssue(
                    severity="error",
                    code="database_argument_empty",
                    message="Configured database argument is empty.",
                    action=(
                        "point the MCP server at a SQLite file path such as "
                        "`.memorygraph/memory.db`"
                    ),
                )
            )
        elif database_argument.startswith("-"):
            issues.append(
                ProbeIssue(
                    severity="error",
                    code="database_argument_flag_like",
                    message=(
                        "Configured database argument looks like a flag, not a path: "
                        f"{database_argument}"
                    ),
                    action="put the database file path in the database argument slot",
                )
            )
        else:
            resolved_database_path = _resolve_database_path(project_path, database_argument)
            issues.extend(_database_path_issues(resolved_database_path))

    return CodexMCPConfiguration(
        project_directory=project_path,
        config_path=config_path,
        command=command,
        args=args,
        default_tools_approval_mode=approval_mode,
        database_argument_index=database_argument_index,
        database_argument=database_argument,
        resolved_database_path=resolved_database_path,
        resolved_command=resolved_command,
        issues=tuple(issues),
    )


def probe_codex_mcp(
    project_directory: str | Path,
    *,
    database_mode: DatabaseMode = "temporary",
    launch_sources: tuple[LaunchSource, ...] = ("config", "local_module"),
) -> CodexMCPProbeReport:
    """Inspect config and exercise a real MCP subprocess with safe tool calls."""

    configuration = inspect_codex_project_config(project_directory)
    launches: list[CodexMCPLaunchResult] = []
    for source in launch_sources:
        if source == "config":
            launches.append(_probe_configured_server(configuration, database_mode=database_mode))
            continue
        if source == "local_module":
            launches.append(_probe_local_module_server(configuration.project_directory))
            continue
        raise ValueError(f"Unsupported launch source: {source}")
    return CodexMCPProbeReport(configuration=configuration, launches=tuple(launches))


def _probe_configured_server(
    configuration: CodexMCPConfiguration,
    *,
    database_mode: DatabaseMode,
) -> CodexMCPLaunchResult:
    issues = [issue for issue in configuration.issues if issue.severity == "error"]
    if (
        configuration.command is None
        or configuration.database_argument_index is None
        or configuration.resolved_command is None
    ):
        return CodexMCPLaunchResult(
            source="config",
            argv=tuple(),
            database_mode=database_mode,
            database_path=configuration.resolved_database_path or configuration.project_directory,
            ok=False,
            protocol_version=None,
            tools=tuple(),
            issues=tuple(issues),
        )

    if database_mode == "project":
        if configuration.resolved_database_path is None:
            project_issues = list(issues)
            project_issues.append(
                ProbeIssue(
                    severity="error",
                    code="project_database_unresolved",
                    message="Project database mode requires a resolved database path from config.",
                    action="fix the config args or use temporary probe mode",
                )
            )
            return CodexMCPLaunchResult(
                source="config",
                argv=tuple(),
                database_mode=database_mode,
                database_path=configuration.project_directory,
                ok=False,
                protocol_version=None,
                tools=tuple(),
                issues=tuple(project_issues),
            )
        probe_database = configuration.resolved_database_path
        argv = list(configuration.args)
    else:
        probe_database = configuration.project_directory / ".memorygraph" / "codex-probe.db"
        argv = list(configuration.args)
        argv[configuration.database_argument_index] = os.fspath(probe_database)

    try:
        return _run_probe(
            source="config",
            project_directory=configuration.project_directory,
            command=os.fspath(configuration.resolved_command),
            args=tuple(argv),
            database_path=probe_database,
            database_mode=database_mode,
            initial_issues=issues,
        )
    finally:
        if database_mode == "temporary":
            _cleanup_probe_database(probe_database)


def _probe_local_module_server(project_directory: Path) -> CodexMCPLaunchResult:
    probe_database = project_directory / ".memorygraph" / "codex-local-probe.db"
    try:
        return _run_probe(
            source="local_module",
            project_directory=project_directory,
            command=sys.executable,
            args=("-m", "memorygraph.mcp", os.fspath(probe_database)),
            database_path=probe_database,
            database_mode="temporary",
        )
    finally:
        _cleanup_probe_database(probe_database)


def _run_probe(
    *,
    source: LaunchSource,
    project_directory: Path,
    command: str,
    args: tuple[str, ...],
    database_path: Path,
    database_mode: DatabaseMode,
    initial_issues: list[ProbeIssue] | None = None,
) -> CodexMCPLaunchResult:
    issues = list(initial_issues or [])
    database_path.parent.mkdir(parents=True, exist_ok=True)
    if database_mode == "temporary" and database_path.exists():
        database_path.unlink()
    bank_slug = "project:codex-probe"
    with MemoryGraph.open(database_path) as memory:
        memory.create_bank(bank_slug)

    process: subprocess.Popen[bytes] | None = None
    protocol_version: str | None = None
    tools: tuple[str, ...] = ()
    record_observation_id: str | None = None
    recall_hit_count: int | None = None
    forget_observation_id: str | None = None
    argv = (command, *args)
    try:
        env = os.environ.copy()
        if _needs_source_tree_pythonpath(command, args):
            env["PYTHONPATH"] = _source_tree_pythonpath(env.get("PYTHONPATH"))
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.fspath(project_directory),
            env=env,
        )
    except FileNotFoundError:
        issues.append(
            ProbeIssue(
                severity="error",
                code="probe_spawn_failed",
                message=(
                    "Failed to launch MCP server because the executable was not found: "
                    f"{command}"
                ),
                action="install the package or point the config at a valid executable",
            )
        )
        return CodexMCPLaunchResult(
            source=source,
            argv=argv,
            database_mode=database_mode,
            database_path=database_path,
            ok=False,
            protocol_version=None,
            tools=tuple(),
            issues=tuple(issues),
        )
    except OSError as error:
        issues.append(
            ProbeIssue(
                severity="error",
                code="probe_spawn_failed",
                message=f"Failed to launch MCP server: {error}",
                action="inspect the configured command and its permissions",
            )
        )
        return CodexMCPLaunchResult(
            source=source,
            argv=argv,
            database_mode=database_mode,
            database_path=database_path,
            ok=False,
            protocol_version=None,
            tools=tuple(),
            issues=tuple(issues),
        )

    try:
        assert process.stdin is not None
        assert process.stdout is not None
        _write_message(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
            },
        )
        initialize = _expect_response(process.stdout, request_id=1)
        protocol_version = (
            initialize.get("result", {}).get("protocolVersion")
            if isinstance(initialize.get("result"), dict)
            else None
        )
        if protocol_version != "2025-06-18":
            issues.append(
                ProbeIssue(
                    severity="error",
                    code="protocol_version_unexpected",
                    message=(
                        "Initialize returned protocol version "
                        f"{protocol_version!r}, expected `2025-06-18`."
                    ),
                    action="upgrade the MemoryGraph MCP package or inspect the launched server",
                )
            )

        _write_message(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
        )
        _write_message(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
            },
        )
        listed = _expect_response(process.stdout, request_id=2)
        listed_tools = listed.get("result", {}).get("tools")
        if not isinstance(listed_tools, list):
            issues.append(
                ProbeIssue(
                    severity="error",
                    code="tools_list_invalid",
                    message="`tools/list` did not return a tools array.",
                    action="inspect the launched MCP server implementation",
                )
            )
        else:
            tools = tuple(
                tool["name"] for tool in listed_tools if isinstance(tool, dict) and "name" in tool
            )
            if tools != _EXPECTED_TOOL_NAMES:
                issues.append(
                    ProbeIssue(
                        severity="error",
                        code="tools_list_unexpected",
                        message=f"Expected tools {_EXPECTED_TOOL_NAMES}, received {tools}.",
                        action=(
                            "verify that the MemoryGraph MCP surface still exposes exactly "
                            "five tools"
                        ),
                    )
                )

        _write_message(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "record",
                    "arguments": {
                        "bank": bank_slug,
                        "workspace": project_directory.name,
                        "kind": "attempt",
                        "content": "Run pytest -q before packaging.",
                        "source_key": f"codex:probe:{source}:record-1",
                        "metadata": {
                            "task_key": "package memorygraph release",
                            "outcome": "success",
                            "applicability": {"surface": "codex_probe"},
                            "environment": {"source": source},
                        },
                    },
                },
            },
        )
        recorded = _expect_response(process.stdout, request_id=3)
        structured = recorded.get("result", {}).get("structuredContent", {})
        record_observation_id = (
            structured.get("observation", {}).get("observation_id")
            if isinstance(structured, dict)
            else None
        )
        if record_observation_id is None and isinstance(structured, dict):
            record_observation_id = (
                structured.get("episode", {}).get("source_observation_id")
                if isinstance(structured.get("episode"), dict)
                else None
            )
        if not record_observation_id:
            issues.append(
                ProbeIssue(
                    severity="error",
                    code="record_probe_failed",
                    message="`record` did not return an observation id.",
                    action="inspect the server stderr and the probe database state",
                )
            )

        _write_message(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "recall",
                    "arguments": {
                        "bank": bank_slug,
                        "workspace": project_directory.name,
                        "query": "pytest packaging codex probe",
                        "limit": 3,
                    },
                },
            },
        )
        recalled = _expect_response(process.stdout, request_id=4)
        recall_hits = recalled.get("result", {}).get("structuredContent", {}).get("hits", [])
        if isinstance(recall_hits, list):
            recall_hit_count = len(recall_hits)
        if not recall_hit_count:
            issues.append(
                ProbeIssue(
                    severity="error",
                    code="recall_probe_failed",
                    message="`recall` did not return the recorded probe observation.",
                    action="inspect the probe database and MCP tool results",
                )
            )

        if record_observation_id is not None:
            _write_message(
                process.stdin,
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "forget",
                        "arguments": {
                            "bank": bank_slug,
                            "workspace": project_directory.name,
                            "observation_id": record_observation_id,
                        },
                    },
                },
            )
            forgotten = _expect_response(process.stdout, request_id=5)
            forget_structured = forgotten.get("result", {}).get("structuredContent", {})
            forget_observation_id = (
                forget_structured.get("result", {}).get("observation_id")
                if isinstance(forget_structured, dict)
                else None
            )
            if forget_observation_id != record_observation_id:
                issues.append(
                    ProbeIssue(
                        severity="error",
                        code="forget_probe_failed",
                        message="`forget` did not delete the recorded probe observation.",
                        action="inspect deletion propagation and the launched server response",
                    )
                )
    except ProbeRuntimeError as error:
        issues.append(error.issue)
    finally:
        stderr_text = _finalize_process(process)
        if stderr_text:
            issues.append(
                ProbeIssue(
                    severity="warning",
                    code="server_stderr",
                    message=f"Server wrote to stderr during probe: {stderr_text}",
                    action="inspect the server implementation if the warning repeats",
                )
            )

    return CodexMCPLaunchResult(
        source=source,
        argv=argv,
        database_mode=database_mode,
        database_path=database_path,
        ok=not any(issue.severity == "error" for issue in issues),
        protocol_version=protocol_version,
        tools=tools,
        issues=tuple(issues),
        record_observation_id=record_observation_id,
        recall_hit_count=recall_hit_count,
        forget_observation_id=forget_observation_id,
    )


class ProbeRuntimeError(Exception):
    """Structured probe failure."""

    def __init__(self, issue: ProbeIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue


def _expect_response(stream: Any, *, request_id: int) -> dict[str, Any]:
    try:
        response = _read_message(stream)
    except Exception as error:  # pragma: no cover - transport helper already unit-tested
        raise ProbeRuntimeError(
            ProbeIssue(
                severity="error",
                code="transport_read_failed",
                message=f"Failed reading MCP response for request {request_id}: {error}",
                action="inspect the launched server and its STDIO transport",
            )
        ) from error
    if response is None:
        raise ProbeRuntimeError(
            ProbeIssue(
                severity="error",
                code="unexpected_eof",
                message=f"MCP server exited before responding to request {request_id}.",
                action="inspect stderr and rerun the probe locally",
            )
        )
    if response.get("id") != request_id:
        raise ProbeRuntimeError(
            ProbeIssue(
                severity="error",
                code="response_id_mismatch",
                message=(
                    f"Expected response id {request_id}, received {response.get('id')!r}."
                ),
                action="inspect request ordering and any extra server output on stdout",
            )
        )
    error_payload = response.get("error")
    if isinstance(error_payload, dict):
        message = error_payload.get("message", "unknown MCP error")
        raise ProbeRuntimeError(
            ProbeIssue(
                severity="error",
                code="jsonrpc_error",
                message=f"Request {request_id} failed: {message}",
                action="inspect the launched server implementation and the probe database path",
            )
        )
    return response


def _finalize_process(process: subprocess.Popen[bytes] | None) -> str | None:
    if process is None:
        return None
    stderr_text = ""
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()
    try:
        stderr_bytes = process.stderr.read() if process.stderr is not None else b""
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        stderr_bytes = process.stderr.read() if process.stderr is not None else b""
        process.wait(timeout=5)
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
        return stderr_text or "server timed out during shutdown"
    stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
    return stderr_text or None


def _database_argument_index(command: str, args: tuple[str, ...]) -> int | None:
    del command  # command is reserved for future launch-shape heuristics
    if len(args) >= 3 and args[0] == "-m" and args[1] == "memorygraph.mcp":
        return 2
    if not args:
        return None
    return 0


def _needs_source_tree_pythonpath(command: str, args: tuple[str, ...]) -> bool:
    return Path(command).name.startswith("python") and len(args) >= 2 and args[:2] == (
        "-m",
        "memorygraph.mcp",
    )


def _resolve_command(command: str, project_directory: Path) -> Path | None:
    candidate = Path(command).expanduser()
    if candidate.is_absolute():
        return candidate if _is_executable_file(candidate) else None
    if candidate.parent != Path("."):
        project_candidate = (project_directory / candidate).absolute()
        return project_candidate if _is_executable_file(project_candidate) else None
    located = shutil.which(command)
    return Path(located).absolute() if located is not None else None


def _resolve_database_path(project_directory: Path, raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate
    return (project_directory / candidate).resolve()


def _source_tree_pythonpath(existing: str | None) -> str:
    repo_root = Path(__file__).resolve().parents[4]
    entries = [os.fspath(repo_root / "src"), os.fspath(repo_root)]
    if existing:
        entries.append(existing)
    return os.pathsep.join(entries)


def _database_path_issues(database_path: Path) -> list[ProbeIssue]:
    issues: list[ProbeIssue] = []
    if database_path.exists() and not database_path.is_file():
        issues.append(
            ProbeIssue(
                severity="error",
                code="database_path_not_file",
                message=f"Configured database path exists but is not a file: {database_path}",
                action="point the MCP config at a SQLite file path",
            )
        )
        return issues

    parent = database_path.parent
    if parent.exists():
        if not _directory_is_writable(parent):
            issues.append(
                ProbeIssue(
                    severity="error",
                    code="database_parent_not_writable",
                    message=f"Database parent directory is not writable: {parent}",
                    action="choose a writable database path",
                )
            )
    else:
        ancestor = _first_existing_ancestor(parent)
        if ancestor is None or not _directory_is_writable(ancestor):
            issues.append(
                ProbeIssue(
                    severity="error",
                    code="database_parent_uncreatable",
                    message=f"Cannot create database directory for {database_path}",
                    action="choose a database path under a writable existing directory",
                )
            )
    return issues


def _directory_is_writable(path: Path) -> bool:
    return path.is_dir() and path.exists() and os.access(path, os.W_OK)


def _first_existing_ancestor(path: Path) -> Path | None:
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return current


def _is_executable_file(path: Path) -> bool:
    return path.exists() and path.is_file() and os.access(path, os.X_OK)


def _cleanup_probe_database(database_path: Path) -> None:
    for suffix in ("", "-shm", "-wal"):
        candidate = Path(f"{database_path}{suffix}")
        with suppress(FileNotFoundError):
            candidate.unlink()


def report_to_dict(report: CodexMCPProbeReport) -> dict[str, Any]:
    """Serialize a probe report into stable JSON-ready primitives."""

    return {
        "ok": report.ok,
        "configuration": {
            "ok": report.configuration.ok,
            "project_directory": os.fspath(report.configuration.project_directory),
            "config_path": os.fspath(report.configuration.config_path),
            "command": report.configuration.command,
            "args": list(report.configuration.args),
            "default_tools_approval_mode": report.configuration.default_tools_approval_mode,
            "database_argument_index": report.configuration.database_argument_index,
            "database_argument": report.configuration.database_argument,
            "resolved_database_path": (
                os.fspath(report.configuration.resolved_database_path)
                if report.configuration.resolved_database_path is not None
                else None
            ),
            "resolved_command": (
                os.fspath(report.configuration.resolved_command)
                if report.configuration.resolved_command is not None
                else None
            ),
            "issues": [_issue_to_dict(issue) for issue in report.configuration.issues],
        },
        "launches": [
            {
                "source": launch.source,
                "argv": list(launch.argv),
                "database_mode": launch.database_mode,
                "database_path": os.fspath(launch.database_path),
                "ok": launch.ok,
                "protocol_version": launch.protocol_version,
                "tools": list(launch.tools),
                "record_observation_id": launch.record_observation_id,
                "recall_hit_count": launch.recall_hit_count,
                "forget_observation_id": launch.forget_observation_id,
                "issues": [_issue_to_dict(issue) for issue in launch.issues],
            }
            for launch in report.launches
        ],
    }


def report_to_json(report: CodexMCPProbeReport) -> str:
    """Serialize a probe report to pretty JSON."""

    return json.dumps(report_to_dict(report), indent=2, sort_keys=True)


def _issue_to_dict(issue: ProbeIssue) -> dict[str, Any]:
    return {
        "severity": issue.severity,
        "code": issue.code,
        "message": issue.message,
        "action": issue.action,
    }
