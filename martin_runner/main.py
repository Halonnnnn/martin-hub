from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .claude import ClaudeDriver, ClaudeError
from .config import Config, RunnerConfigError, load_config
from .hub import Hub, HubError
from .runner import Runner


def _setup_logging(config: Config) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if config.runner.log_file:
        path = Path(os.path.expanduser(config.runner.log_file))
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def _load(path: str) -> Config:
    config = load_config(path)
    config.check()
    return config


def cmd_run(args: argparse.Namespace) -> int:
    config = _load(args.config)
    _setup_logging(config)
    runner = Runner(config)
    try:
        runner.run()
    except KeyboardInterrupt:
        runner.stop()
    except (HubError, ClaudeError, RunnerConfigError) as exc:
        logging.getLogger("martin-runner").error("%s", exc)
        return 1
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    config = _load(args.config)
    token = config.token()
    print(f"config: {config.path}")
    print(f"workspace: {config.runner.workspace}")
    print(f"hub: {config.hub.base_url}")
    print(f"token: {token[:8]}… ({len(token)} chars)")
    print(f"state: {config.state_dir}")
    hub = Hub(config.hub.base_url, token)
    try:
        state = hub.client.wake_get()
        wake = state.get("wake")
        print(f"wake: {wake.get('kind') if wake else 'not configured'} connected={wake.get('connected') if wake else None}")
    except Exception as exc:
        print(f"wake: ERROR {exc}")
    driver = ClaudeDriver(config.claude, config.runner.workspace)
    print(f"claude: {driver.version()}")
    print(f"trusted: {driver.is_trusted()}")
    if not driver.is_trusted():
        print("  run `claude` in the workspace once and accept the trust dialog,")
        print("  or set claude.trust_workspace = true and run `martin-runner trust`")
    return 0


def cmd_exec(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if not os.path.isdir(config.runner.workspace):
        print(f"workspace is not a directory: {config.runner.workspace}", file=sys.stderr)
        return 1
    driver = ClaudeDriver(config.claude, config.runner.workspace)
    session_id = None if args.new else args.session
    result = driver.run(args.prompt, session_id)
    print(result.text)
    print(f"\n--- session={result.session_id} cost={result.cost_usd} duration_ms={result.duration_ms}")
    return 0


def cmd_trust(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    config.claude.trust_workspace = True
    driver = ClaudeDriver(config.claude, config.runner.workspace)
    if driver.is_trusted():
        print(f"already trusted: {driver.workspace}")
        return 0
    driver.ensure_trusted()
    print(f"marked trusted: {driver.workspace}")
    print(f"config: {driver.claude_config_file()}")
    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    from .store import Store

    store = Store(config.state_dir / "state.db")
    try:
        store.delete_session(args.group)
        store.delete_pending(args.group)
        store.set_cursor(args.group, 0)
        print(f"forgot session/cursor/pending for {args.group}")
    finally:
        store.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="martin-runner", description="Martin push runner (Claude Code)")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the SSE listener and process wake events")
    run.add_argument("--config", required=True)
    run.set_defaults(func=cmd_run)

    check = sub.add_parser("check", help="check config, Hub, workspace and Claude")
    check.add_argument("--config", required=True)
    check.set_defaults(func=cmd_check)

    exec_ = sub.add_parser("exec", help="run one Claude turn directly (no Hub needed)")
    exec_.add_argument("--config", required=True)
    exec_.add_argument("--prompt", required=True)
    exec_.add_argument("--session", default=None)
    exec_.add_argument("--new", action="store_true")
    exec_.set_defaults(func=cmd_exec)

    trust = sub.add_parser("trust", help="mark the workspace trusted by Claude Code")
    trust.add_argument("--config", required=True)
    trust.set_defaults(func=cmd_trust)

    forget = sub.add_parser("forget", help="forget the stored session for a group")
    forget.add_argument("--config", required=True)
    forget.add_argument("--group", required=True)
    forget.set_defaults(func=cmd_forget)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except RunnerConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except ClaudeError as exc:
        print(f"claude error: {exc}", file=sys.stderr)
        return 3
    except HubError as exc:
        print(f"hub error: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
