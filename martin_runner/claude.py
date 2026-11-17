from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ClaudeConfig


class ClaudeError(Exception):
    pass


@dataclass
class ClaudeResult:
    text: str
    session_id: str
    exit_code: int
    cost_usd: float | None = None
    duration_ms: int | None = None


def _tail(value: str, limit: int = 800) -> str:
    value = (value or "").strip()
    return value[-limit:]


class ClaudeDriver:
    """Run one Claude Code turn in a fixed workspace and return its final text."""

    def __init__(self, config: ClaudeConfig, workspace: str):
        self.config = config
        self.workspace = os.path.realpath(workspace)
        self.executable = shutil.which(config.command) or ""
        if not self.executable:
            raise ClaudeError(f"claude executable not found: {config.command}")

    def version(self) -> str:
        try:
            result = subprocess.run(
                [self.executable, "--version"],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ClaudeError(f"cannot run claude --version: {exc}") from exc
        return (result.stdout or result.stderr).strip()

    # ---- workspace trust -------------------------------------------------
    def claude_config_file(self) -> Path:
        if self.config.config_file:
            return Path(os.path.expanduser(self.config.config_file))
        config_dir = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
        if config_dir:
            candidate = Path(os.path.expanduser(config_dir)) / ".claude.json"
            if candidate.exists():
                return candidate
        return Path(os.path.expanduser("~/.claude.json"))

    def is_trusted(self) -> bool:
        path = self.claude_config_file()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        project = (data.get("projects") or {}).get(self.workspace) or {}
        return bool(project.get("hasTrustDialogAccepted"))

    def ensure_trusted(self) -> None:
        if self.is_trusted():
            return
        if not self.config.trust_workspace:
            raise ClaudeError(
                f"workspace is not trusted by Claude Code: {self.workspace}; "
                "run `claude` there once and accept the trust dialog, "
                "or set claude.trust_workspace = true"
            )
        path = self.claude_config_file()
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError) as exc:
            raise ClaudeError(f"cannot read {path}: {exc}") from exc
        projects = data.setdefault("projects", {})
        project = projects.setdefault(self.workspace, {})
        project["hasTrustDialogAccepted"] = True
        if path.exists():
            backup = path.with_name(f"{path.name}.bak-{int(time.time())}")
            try:
                backup.write_bytes(path.read_bytes())
                os.chmod(backup, 0o600)
            except OSError as exc:
                raise ClaudeError(f"cannot back up {path}: {exc}") from exc
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    # ---- invocation ------------------------------------------------------
    def build_command(self, session_id: str | None) -> tuple[list[str], str]:
        new_session_id = str(uuid.uuid4())
        args = [self.executable, "-p", "--output-format", "stream-json", "--verbose"]
        if self.config.permission_mode:
            args += ["--permission-mode", self.config.permission_mode]
        if self.config.model:
            args += ["--model", self.config.model]
        if self.config.max_turns > 0:
            args += ["--max-turns", str(self.config.max_turns)]
        if self.config.allowed_tools:
            args += ["--allowedTools", ",".join(self.config.allowed_tools)]
        if self.config.disallowed_tools:
            args += ["--disallowedTools", ",".join(self.config.disallowed_tools)]
        if self.config.mcp_config:
            args += ["--mcp-config", str(Path(os.path.expanduser(self.config.mcp_config)))]
        if self.config.append_system_prompt:
            args += ["--append-system-prompt", self.config.append_system_prompt]
        if session_id:
            args += ["--resume", session_id]
        else:
            args += ["--session-id", new_session_id]
        args += self.config.extra_args
        return args, new_session_id

    def run(self, prompt: str, session_id: str | None = None) -> ClaudeResult:
        self.ensure_trusted()
        args, new_session_id = self.build_command(session_id)
        process = subprocess.Popen(
            args,
            cwd=self.workspace,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(input=prompt, timeout=self.config.timeout_seconds)
        except subprocess.TimeoutExpired:
            self._kill_tree(process)
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            raise ClaudeError(
                f"claude timed out after {self.config.timeout_seconds}s; stderr: {_tail(stderr)}"
            )

        observed_session = ""
        final_text = ""
        result_error = ""
        cost: float | None = None
        duration: int | None = None
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event.get("session_id"), str) and event["session_id"]:
                observed_session = event["session_id"]
            if event.get("type") != "result":
                continue
            cost = _as_float(event.get("total_cost_usd"))
            duration = _as_int(event.get("duration_ms"))
            if event.get("subtype") == "success" and not event.get("is_error"):
                final_text = str(event.get("result") or "").strip()
            else:
                errors = event.get("errors") or []
                result_error = "; ".join(str(item) for item in errors) or str(
                    event.get("subtype") or "claude result error"
                )

        effective_session = observed_session or session_id or new_session_id
        if process.returncode != 0:
            raise ClaudeError(
                f"claude exited {process.returncode}: {result_error or _tail(stderr)}"
            )
        if result_error:
            raise ClaudeError(f"claude failed: {result_error}")
        if not final_text:
            raise ClaudeError(f"claude returned no final text; stderr: {_tail(stderr)}")
        if not observed_session and not session_id:
            raise ClaudeError("claude did not report a session id; cannot continue this conversation")
        return ClaudeResult(
            text=final_text,
            session_id=effective_session,
            exit_code=process.returncode,
            cost_usd=cost,
            duration_ms=duration,
        )

    @staticmethod
    def _kill_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            process.terminate()
        deadline = time.monotonic() + 5
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if process.poll() is None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()
