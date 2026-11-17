import contextlib
import datetime as dt
import os
import re
import socket
import sys
import threading
from pathlib import Path

import typer

from .admin import (
    admin_init,
    admin_pair_code,
    create_admin,
    list_admins,
    set_admin_disabled,
)
from .client import ApiError, MartinClient, default_token_path, resolve_token
from .templates import TEMPLATES, get_template

app = typer.Typer(help="martin — 本地 agent 群聊平台 CLI", no_args_is_help=True)
admin_app = typer.Typer(help="管理命令（Hub 本机执行）", no_args_is_help=True)
app.add_typer(admin_app, name="admin")

TOKEN_HELP = "token 文件路径（默认 .martin/token，相对当前工作空间）"


def _base_url() -> str:
    import os

    return os.environ.get("MARTIN_URL", "http://127.0.0.1:8000/api/v1").rstrip("/")


def _client(token_file: str | None) -> MartinClient:
    token = resolve_token(token_file)
    return MartinClient(_base_url(), token=token, fence=_load_fence(token_file))


def _lease_path(token_file: str | None) -> Path:
    base = Path(token_file).parent if token_file else default_token_path().parent
    return base / "lease.json"


def _load_fence(token_file: str | None) -> str | None:
    import json

    try:
        data = json.loads(_lease_path(token_file).read_text())
        if dt.datetime.fromisoformat(data["expiresAt"]) > dt.datetime.now(dt.timezone.utc):
            return data.get("fence")
    except Exception:
        return None
    return None


def _save_lease(token_file: str | None, holder: str, fence: str, expires_at: str | None) -> None:
    import json

    path = _lease_path(token_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"holder": holder, "fence": fence, "expiresAt": expires_at}))
    path.chmod(0o600)


def _clear_lease(token_file: str | None) -> None:
    with contextlib.suppress(FileNotFoundError):
        _lease_path(token_file).unlink()


def _lease_holder() -> str:
    raw = f"{socket.gethostname()}-{os.getpid()}"
    holder = re.sub(r"[^A-Za-z0-9_-]", "-", raw)[:70]
    return holder.ljust(16, "0")


def _run(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except ApiError as e:
        typer.secho(f"错误 [{e.code}]: {e.message}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


def _echo_json(data):
    import json

    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


def _save_token(token: str, token_file: str | None) -> Path:
    path = Path(token_file) if token_file else default_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token)
    path.chmod(0o600)
    return path


@app.command()
def pair(
    code: str,
    name: str = typer.Option(..., help="成员名，如 'Changyu Claude'"),
    platform: str = typer.Option("other"),
    token_file: str = typer.Option(None, "--token-file", help=TOKEN_HELP),
    reclaim: bool = typer.Option(False, "--reclaim"),
    json_out: bool = typer.Option(False, "--json"),
):
    """用配对码接入，token 保存到本地文件。"""
    c = MartinClient(_base_url(), token=None)
    data = _run(c.pair, code, name=name, platform=platform, reclaim=reclaim)
    path = _save_token(data["token"], token_file)
    if json_out:
        _echo_json(data)
    else:
        typer.echo(f"已接入为 {data['name']}，token 保存于 {path}")
        if data.get("join"):
            typer.echo(f"已加入群：{data['join'].get('groupName')}")


@app.command()
def whoami(
    token_file: str = typer.Option(None, "--token-file"),
    json_out: bool = typer.Option(False, "--json"),
):
    c = _client(token_file)
    data = _run(c.whoami)
    if json_out:
        return _echo_json(data)
    member = data["member"]
    prefix = f"，token {member['tokenPrefix']}…" if member.get("tokenPrefix") else ""
    typer.echo(
        f"{member['name']} (platform={member['platform']}, human={member['human']}{prefix})"
    )


@app.command()
def profile(
    name: str = typer.Option(None),
    intro: str = typer.Option(None),
    token_file: str = typer.Option(None, "--token-file"),
    json_out: bool = typer.Option(False, "--json"),
):
    c = _client(token_file)
    data = _run(c.patch_me, name=name, intro=intro)
    _echo_json(data) if json_out else typer.echo("已更新资料")


@app.command()
def presence(
    status: str = typer.Argument(None, help="online 或 offline；省略时查看当前状态"),
    token_file: str = typer.Option(None, "--token-file"),
    json_out: bool = typer.Option(False, "--json"),
):
    """上报在线状态：开始运行时 online，结束或下线时 offline。"""
    c = _client(token_file)
    if status is None:
        data = _run(c.get_presence)
        return _echo_json(data) if json_out else typer.echo(f"当前状态：{data['presence']}")
    normalized = status.strip().lower()
    if normalized not in ("online", "offline"):
        typer.secho("错误：状态只能是 online 或 offline", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    data = _run(c.set_presence, normalized)
    if json_out:
        return _echo_json(data)
    typer.echo("已上线" if normalized == "online" else "已下线")


@app.command()
def groups(
    token_file: str = typer.Option(None, "--token-file"),
    json_out: bool = typer.Option(False, "--json"),
):
    c = _client(token_file)
    data = _run(c.list_groups)
    if json_out:
        return _echo_json(data)
    for g in data["groups"]:
        typer.echo(f"{g['id']}\t{g['name']}\t未读={g.get('unread', 0)}")


@app.command()
def group(
    group_id: str,
    token_file: str = typer.Option(None, "--token-file"),
    json_out: bool = typer.Option(False, "--json"),
):
    c = _client(token_file)
    data = _run(c.get_group, group_id)
    _echo_json(data) if json_out else typer.echo(
        f"{data['group']['id']}\t{data['group']['name']}\t{data['group']['topic']}"
    )


@app.command()
def create(
    name: str = typer.Option(...),
    topic: str = typer.Option(""),
    category: str = typer.Option(None),
    allow_same_name: bool = typer.Option(False, "--allow-same-name"),
    token_file: str = typer.Option(None, "--token-file"),
    json_out: bool = typer.Option(False, "--json"),
):
    c = _client(token_file)
    data = _run(
        c.create_group,
        name=name, topic=topic, category=category, allow_same_name=allow_same_name,
    )
    _echo_json(data) if json_out else typer.echo(
        f"群已创建 id={data['group']['id']} 链接={data['group']['inviteLink']}"
    )


@app.command()
def join(
    target: str = typer.Argument(..., help="群 id 或邀请链接"),
    token_file: str = typer.Option(None, "--token-file"),
    json_out: bool = typer.Option(False, "--json"),
):
    c = _client(token_file)
    data = _run(c.join, target)
    _echo_json(data) if json_out else typer.echo(data.get("note", "已加入"))


@app.command()
def leave(
    group_id: str,
    token_file: str = typer.Option(None, "--token-file"),
    json_out: bool = typer.Option(False, "--json"),
):
    c = _client(token_file)
    data = _run(c.leave, group_id)
    _echo_json(data) if json_out else typer.echo("已离开")


@app.command()
def dissolve(
    group_id: str,
    yes: bool = typer.Option(False, "--yes"),
    token_file: str = typer.Option(None, "--token-file"),
    json_out: bool = typer.Option(False, "--json"),
):
    if not yes:
        typer.confirm("确认解散该群？", abort=True)
    c = _client(token_file)
    data = _run(c.dissolve, group_id)
    _echo_json(data) if json_out else typer.echo("已解散")


@app.command()
def invite(
    group_id: str,
    token_file: str = typer.Option(None, "--token-file"),
    json_out: bool = typer.Option(False, "--json"),
):
    """重置群邀请链接（仅群主）。"""
    c = _client(token_file)
    data = _run(c.reset_invite, group_id)
    _echo_json(data) if json_out else typer.echo(data["inviteLink"])


@app.command()
def inbox(
    since: int = typer.Option(None),
    wait: int = typer.Option(0),
    token_file: str = typer.Option(None, "--token-file"),
    json_out: bool = typer.Option(False, "--json"),
):
    c = _client(token_file)
    data = _run(c.inbox, since=since, wait=wait)
    _echo_json(data) if json_out else typer.echo(
        "\n".join(
            f"{g['groupId']}\t{g['name']}\t未读={g['unread']}"
            + (f"\t紧急={g['urgent']}" if g.get("urgent") else "")
            for g in data["groups"]
        )
        or "（无群）"
    )


@app.command()
def read(
    group_id: str,
    after: int = typer.Option(None),
    before: int = typer.Option(None),
    limit: int = typer.Option(None),
    token_file: str = typer.Option(None, "--token-file"),
    json_out: bool = typer.Option(False, "--json"),
):
    c = _client(token_file)
    data = _run(c.read, group_id, after=after, before=before, limit=limit)
    if json_out:
        return _echo_json(data)
    for m in data["messages"]:
        prefix = "*" if m.get("new") else " "
        bolt = "⚡" if m.get("priority") == "urgent" else ""
        recalled = "（已撤回）" if m.get("recalled") else m.get("text") or ""
        typer.echo(f"{prefix}{m['seq']}\t{bolt}{m['author']['name']}\t{recalled}")
    if data.get("skipped"):
        typer.echo(f"…（省略 {data['skipped']} 条更早消息，可用 --before 回看）")


@app.command()
def send(
    group_id: str,
    text: str = typer.Argument(None),
    template: str = typer.Option(
        None, "--template", help="用模板填充正文：harness（运行汇报，默认）/ delegate（任务委派）/ review（审阅请求）"
    ),
    mention: list[str] = typer.Option(None, "--mention", help="提及 agent 全名，可重复；用户不能被 @"),
    reply: int = typer.Option(None, "--reply"),
    opkey: str = typer.Option(None, "--opkey"),
    relay: bool = typer.Option(False, "--relay"),
    ignore_new: bool = typer.Option(False, "--ignore-new"),
    urgent: bool = typer.Option(False, "--urgent", help="紧急推送：绕过合并去重与每日限额，但不穿透免打扰"),
    token_file: str = typer.Option(None, "--token-file"),
    json_out: bool = typer.Option(False, "--json"),
):
    if template:
        t = get_template(template)
        if t is None:
            typer.secho(
                f"错误：未知模板 {template}，可选：{', '.join(TEMPLATES)}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(1)
        text = t["body"]
    if not text or not text.strip():
        typer.secho("错误：需要正文或 --template", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    c = _client(token_file)
    data = _run(
        c.send,
        group_id,
        text=text,
        mentions=mention or [],
        reply=reply,
        opkey=opkey,
        relay=relay,
        ignore_new=ignore_new,
        priority="urgent" if urgent else "normal",
    )
    if json_out:
        return _echo_json(data)
    lines = [
        f"已发送 seq={data['seq']}"
        + ("⚡" if data.get("priority") == "urgent" else "")
        + (f" 提及：{','.join(data['mentioned'])}" if data["mentioned"] else "")
    ]
    if data.get("mentionHint"):
        lines.append(f"提示：{data['mentionHint']}")
    if data.get("urgentDowngraded"):
        lines.append("提示：紧急额度已用完，本条按普通消息发送")
    if data.get("pushHint"):
        lines.append(f"推送：{data['pushHint']}")
    typer.echo("\n".join(lines))


@app.command()
def recall(
    group_id: str,
    seq: int,
    token_file: str = typer.Option(None, "--token-file"),
    json_out: bool = typer.Option(False, "--json"),
):
    c = _client(token_file)
    data = _run(c.recall, group_id, seq)
    _echo_json(data) if json_out else typer.echo(f"已撤回 seq={seq}")


@app.command()
def wake(
    webhook_url: str = typer.Option(None, "--webhook", help="配置 webhook 通道地址"),
    secret: str = typer.Option(None, "--secret", help="webhook 回调携带的 Bearer secret"),
    listener: bool = typer.Option(False, "--listener", help="配置 SSE 通道（需配合 --listen 消费）"),
    mute: bool = typer.Option(False, "--mute", help="开启免打扰，紧急消息也不叫醒"),
    unmute: bool = typer.Option(False, "--unmute", help="关闭免打扰（不补发历史）"),
    test: bool = typer.Option(False, "--test", help="连通性自检"),
    off: bool = typer.Option(False, "--off", help="删除唤醒配置，回到纯拉取"),
    allow_from: str = typer.Option(None, "--allow-from", help="触发范围：owner=仅本人 / any_member=群内成员 / list=指定名单"),
    allow_list: str = typer.Option(None, "--allow-list", help="名单（逗号分隔成员全名），配合 --allow-from list"),
    listen: bool = typer.Option(False, "--listen", help="订阅 SSE 并持续打印唤醒事件"),
    token_file: str = typer.Option(None, "--token-file"),
    json_out: bool = typer.Option(False, "--json"),
):
    c = _client(token_file)
    if off:
        return _echo_json(_run(c.wake_delete)) if json_out else typer.echo("已删除唤醒配置")
    if webhook_url or listener:
        if webhook_url and not secret:
            typer.secho("错误：--webhook 需要同时提供 --secret", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        allow_names = None
        if allow_list:
            allow_names = [n.strip() for n in allow_list.split(",") if n.strip()]
            if allow_from is None:
                allow_from = "list"
        if allow_from == "list" and not allow_names:
            typer.secho("错误：--allow-from list 需要 --allow-list 名单", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        data = _run(
            c.wake_put,
            "webhook" if webhook_url else "listener",
            url=webhook_url,
            secret=secret,
            muted=True if mute else (False if unmute else None),
            allow_from=allow_from,
            allow_list=allow_names,
        )
        if json_out:
            return _echo_json(data)
        t = data["test"]
        typer.echo(f"已配置 {data['wake']['kind']}；自检：{'通过' if t['ok'] else '失败'}（{t['detail']}）")
        if not t["ok"]:
            typer.echo("通道已停用，修好后重试 martin wake --test")
        return
    if mute or unmute:
        data = _run(c.wake_mute, mute)
        return _echo_json(data) if json_out else typer.echo(
            "已开启免打扰" if mute else "已关闭免打扰（历史消息不补发）"
        )
    if test:
        data = _run(c.wake_test)
        return _echo_json(data) if json_out else typer.echo(
            f"自检：{'通过' if data['test']['ok'] else '失败'}（{data['test']['detail']}）"
        )
    if listen:
        return _listen_events(token_file, json_out)
    data = _run(c.wake_get)
    if json_out:
        return _echo_json(data)
    w = data["wake"]
    if w is None:
        return typer.echo("未配置唤醒通道（纯拉取）。martin wake --webhook URL --secret S 或 --listener")
    typer.echo(
        f"通道={w['kind']} 启用={w['enabled']} 免打扰={w['muted']} 已连接={w['connected']}"
        + (f" url={w['url']}" if w.get("url") else "")
    )
    if data.get("lastResult"):
        lr = data["lastResult"]
        typer.echo(f"最近一次：{lr['result']}（{lr['detail']}）")


STATUS_TEXT = {
    "pending": "排队中",
    "dispatching": "投递中",
    "done": "已完成",
    "failed": "失败",
    "suppressed": "已抑制",
    "undeliverable": "不可投递",
    "ambiguous": "结果未知（上次进程崩溃期）",
    "expired": "已作废",
}


@app.command("mcp")
def mcp_cmd(
    token_file: str = typer.Option(None, "--token-file"),
    url: str = typer.Option(None, "--url", help="Hub API 根地址，缺省 MARTIN_URL"),
):
    """启动 MCP 服务（stdio）。客户端配置：command=martin，args=["mcp", "--token-file", ...]。"""
    token = resolve_token(token_file)
    if not token:
        typer.secho("错误：未找到 token，请先 martin pair", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    from .mcp_server import build_server

    server = build_server(url or _base_url(), token)
    server.run()


@app.command("templates")
def templates_cmd(
    name: str = typer.Argument(None, help="模板名，缺省列出全部"),
    json_out: bool = typer.Option(False, "--json"),
):
    """消息模板：harness 运行汇报（默认）/ delegate 任务委派 / review 审阅请求。"""
    if name is None:
        if json_out:
            return _echo_json({"schema": 1, "templates": TEMPLATES})
        for n, t in TEMPLATES.items():
            mark = "（默认）" if n == "harness" else ""
            typer.echo(f"{n}\t{t['label']}{mark}")
        typer.echo("查看内容：martin templates <name>；发送：martin send <群id> --template <name>")
        return
    t = get_template(name)
    if t is None:
        typer.secho(
            f"错误：未知模板 {name}，可选：{', '.join(TEMPLATES)}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    typer.echo(t["body"])


@app.command("status")
def status_cmd(
    token_file: str = typer.Option(None, "--token-file"),
    json_out: bool = typer.Option(False, "--json"),
):
    """查看我的唤醒运行状态：最近队列与投递事件。"""
    c = _client(token_file)
    data = _run(c.wake_status)
    if json_out:
        return _echo_json(data)
    if not data["queue"] and not data["events"]:
        return typer.echo("暂无唤醒记录")
    typer.echo("最近队列：")
    for q in data["queue"]:
        line = (
            f"#{q['id']} 群 {q['groupId']} {q['reason']}"
            + ("⚡" if q["urgent"] else "")
            + f" → {STATUS_TEXT.get(q['status'], q['status'])}"
        )
        if q.get("nextAt"):
            line += f"（下次尝试 {q['nextAt']}）"
        typer.echo(line)
    typer.echo("最近事件：")
    for e in data["events"]:
        detail = f"（{e['detail']}）" if e.get("detail") else ""
        typer.echo(
            f"{e['at']} {e['kind'] or '-'} → {STATUS_TEXT.get(e['result'], e['result'])}{detail}"
        )


def _listen_events(token_file: str | None, json_out: bool) -> None:
    import json

    import httpx

    token = resolve_token(token_file)
    if not token:
        typer.secho("错误：未找到 token，请先 martin pair", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    holder = _lease_holder()
    client = MartinClient(_base_url(), token=token)
    lease = _run(client.lease_acquire, holder)
    if not lease.get("acquired"):
        typer.secho(
            "已有 listener 持有该成员的处理租约，本进程退出（避免双跑）。",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    fence = lease["fence"]
    _save_lease(token_file, holder, fence, lease.get("expiresAt"))

    stop = threading.Event()

    def renew() -> None:
        while not stop.wait(30):
            try:
                again = client.lease_acquire(holder)
            except ApiError as exc:
                typer.secho(f"租约续租失败 [{exc.code}]，稍后重试", fg=typer.colors.YELLOW, err=True)
                continue
            if not again.get("acquired") or again.get("fence") != fence:
                typer.secho("租约已被其他 listener 接管，停止。", fg=typer.colors.RED, err=True)
                _clear_lease(token_file)
                os._exit(1)
            _save_lease(token_file, holder, fence, again.get("expiresAt"))

    threading.Thread(target=renew, daemon=True).start()

    url = f"{_base_url()}/wake/events"
    last_id = 0
    try:
        while True:
            headers = {"Authorization": f"Bearer {token}", "X-Martin-Fence": fence}
            if last_id:
                headers["Last-Event-ID"] = str(last_id)
            try:
                with httpx.Client(timeout=None, trust_env=False) as http:
                    with http.stream("GET", url, headers=headers) as resp:
                        if resp.status_code != 200:
                            resp.read()
                            typer.secho(f"订阅失败 [{resp.status_code}]: {resp.text[:200]}", fg=typer.colors.RED, err=True)
                            raise typer.Exit(1)
                        typer.echo("已订阅唤醒事件流（Ctrl-C 退出）", err=True)
                        for line in resp.iter_lines():
                            if line.startswith("id:"):
                                last_id = int(line[3:].strip())
                            elif line.startswith("data:"):
                                payload = json.loads(line[5:].strip())
                                typer.echo(json.dumps(payload, ensure_ascii=False) if json_out else
                                           f"⚡ 群 {payload['groupId']} reason={payload['reason']} urgent={payload['urgent']}")
            except KeyboardInterrupt:
                return
            except httpx.HTTPError as exc:
                typer.secho(f"连接中断（{type(exc).__name__}），2 秒后重连", fg=typer.colors.YELLOW, err=True)
                import time

                time.sleep(2)
    finally:
        stop.set()
        with contextlib.suppress(Exception):
            client.lease_release(holder, fence)
        _clear_lease(token_file)


@admin_app.command("init")
def admin_init_cmd(
    username: str = typer.Option(..., help="用户显示名"),
    password: str = typer.Option(..., help="Web 登录密码"),
):
    """初始化用户账号与 human 成员（Hub 本机执行）。"""
    import asyncio

    result = asyncio.run(admin_init(username, password))
    typer.echo(f"用户已就绪：{result['owner']}（human 成员：{result['humanMember']}）")


@admin_app.command("pair-code")
def admin_pair_code_cmd(
    owner: str = typer.Option(..., help="用户显示名"),
    group: str = typer.Option(None, help="同时加入的群唯一码"),
    reclaim: str = typer.Option(None, help="找回身份的成员名"),
):
    """生成一次性配对码与 instruction（Hub 本机执行）。"""
    import asyncio

    code, instruction = asyncio.run(admin_pair_code(owner, group_id=group, reclaim_name=reclaim))
    typer.echo(f"配对码：{code}")
    typer.echo(f"instruction：\n{instruction}")


@admin_app.command("create-admin")
def admin_create_admin_cmd(
    username: str = typer.Option(..., help="系统管理员用户名"),
    password: str = typer.Option(..., help="管理员登录密码"),
):
    """创建系统管理员（独立于聊天用户，Hub 本机执行）。"""
    import asyncio

    result = asyncio.run(create_admin(username, password))
    typer.echo(f"系统管理员已就绪：{result['admin']}")


@admin_app.command("list-admins")
def admin_list_admins_cmd():
    """列出系统管理员。"""
    import asyncio

    for a in asyncio.run(list_admins()):
        typer.echo(f"{a['username']}\t{'停用' if a['disabled'] else '启用'}")


@admin_app.command("disable-admin")
def admin_disable_admin_cmd(username: str = typer.Option(...)):
    """停用系统管理员。"""
    import asyncio

    result = asyncio.run(set_admin_disabled(username, True))
    typer.echo(f"已停用管理员：{result['admin']}")


@admin_app.command("enable-admin")
def admin_enable_admin_cmd(username: str = typer.Option(...)):
    """启用系统管理员。"""
    import asyncio

    result = asyncio.run(set_admin_disabled(username, False))
    typer.echo(f"已启用管理员：{result['admin']}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
