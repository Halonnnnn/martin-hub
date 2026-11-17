from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class RunnerConfigError(Exception):
    pass


def _expand(value: str) -> str:
    return os.path.realpath(os.path.expanduser(value)) if value else ""


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(item) for item in value]


@dataclass
class HubConfig:
    base_url: str = "http://127.0.0.1:8000"
    token: str = ""
    token_file: str = ""
    groups: list[str] = field(default_factory=list)
    auto_configure_wake: bool = False


@dataclass
class ClaudeConfig:
    command: str = "claude"
    permission_mode: str = "acceptEdits"
    model: str = ""
    append_system_prompt: str = ""
    max_turns: int = 0
    timeout_seconds: int = 900
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=lambda: ["AskUserQuestion"])
    mcp_config: str = ""
    extra_args: list[str] = field(default_factory=list)
    trust_workspace: bool = False
    config_file: str = ""


@dataclass
class RunnerSettings:
    workspace: str = ""
    state_dir: str = ""
    lease_holder: str = ""
    log_file: str = ""
    notes_file: str = ""
    poll_reconnect_seconds: float = 3.0


@dataclass
class Config:
    hub: HubConfig
    claude: ClaudeConfig
    runner: RunnerSettings
    path: Path

    @property
    def workspace(self) -> Path:
        return Path(self.runner.workspace)

    @property
    def state_dir(self) -> Path:
        return Path(self.runner.state_dir)

    def token(self) -> str:
        if self.hub.token.strip():
            return self.hub.token.strip()
        if os.environ.get("MARTIN_TOKEN", "").strip():
            return os.environ["MARTIN_TOKEN"].strip()
        candidates: list[Path] = []
        if self.hub.token_file:
            token_file = Path(os.path.expanduser(self.hub.token_file))
            if not token_file.is_absolute():
                token_file = self.path.parent / token_file
            candidates.append(token_file)
        if self.runner.workspace:
            martin_dir = Path(self.runner.workspace) / ".martin"
            candidates.append(martin_dir / "token")
            try:
                token_files = sorted(martin_dir.glob("*.token"))
                if len(token_files) == 1:
                    candidates.append(token_files[0])
            except OSError:
                pass
        candidates.extend(
            [
                Path(os.path.expanduser("~/.martin/token")),
                Path(os.path.expanduser("~/.dsh/martin-token")),
            ]
        )
        for candidate in candidates:
            try:
                value = candidate.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if value:
                return value
        return ""

    def check(self) -> None:
        if not self.runner.workspace:
            raise RunnerConfigError("runner.workspace is required")
        workspace = Path(self.runner.workspace)
        if not workspace.is_dir():
            raise RunnerConfigError(f"runner.workspace is not a directory: {workspace}")
        if not self.token():
            raise RunnerConfigError(
                "no Martin token: set hub.token / hub.token_file / MARTIN_TOKEN, "
                "or put it in <workspace>/.martin/token"
            )
        if self.claude.timeout_seconds <= 0:
            raise RunnerConfigError("claude.timeout_seconds must be positive")

    def ensure_dirs(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)


def load_config(path: str | os.PathLike[str]) -> Config:
    config_path = Path(os.path.expanduser(str(path))).resolve()
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RunnerConfigError(f"config not found: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise RunnerConfigError(f"invalid TOML in {config_path}: {exc}") from exc

    hub_raw = raw.get("hub", {}) or {}
    claude_raw = raw.get("claude", {}) or {}
    runner_raw = raw.get("runner", {}) or {}

    workspace = _expand(str(runner_raw.get("workspace", "")))
    state_dir = _expand(str(runner_raw.get("state_dir", "~/.martin-runner")))
    notes_file = str(runner_raw.get("notes_file", "") or "")

    hub = HubConfig(
        base_url=str(hub_raw.get("base_url", "http://127.0.0.1:8000")).rstrip("/"),
        token=str(hub_raw.get("token", "") or ""),
        token_file=str(hub_raw.get("token_file", "") or ""),
        groups=_as_list(hub_raw.get("groups")),
        auto_configure_wake=bool(hub_raw.get("auto_configure_wake", False)),
    )
    claude = ClaudeConfig(
        command=str(claude_raw.get("command", "claude")),
        permission_mode=str(claude_raw.get("permission_mode", "acceptEdits")),
        model=str(claude_raw.get("model", "") or ""),
        append_system_prompt=str(claude_raw.get("append_system_prompt", "") or ""),
        max_turns=int(claude_raw.get("max_turns", 0) or 0),
        timeout_seconds=int(claude_raw.get("timeout_seconds", 900) or 900),
        allowed_tools=_as_list(claude_raw.get("allowed_tools")),
        disallowed_tools=_as_list(claude_raw.get("disallowed_tools") or ["AskUserQuestion"]),
        mcp_config=str(claude_raw.get("mcp_config", "") or ""),
        extra_args=_as_list(claude_raw.get("extra_args")),
        trust_workspace=bool(claude_raw.get("trust_workspace", False)),
        config_file=str(claude_raw.get("config_file", "") or ""),
    )
    runner = RunnerSettings(
        workspace=workspace,
        state_dir=state_dir,
        lease_holder=str(runner_raw.get("lease_holder", "") or ""),
        log_file=str(runner_raw.get("log_file", "") or ""),
        notes_file=notes_file,
        poll_reconnect_seconds=float(runner_raw.get("poll_reconnect_seconds", 3.0) or 3.0),
    )
    return Config(hub=hub, claude=claude, runner=runner, path=config_path)
