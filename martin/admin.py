import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings
from .db import create_schema_and_tables
from .errors import AppError
from .models import Admin, Group, Member, Owner, PairingCode
from .security import gen_pairing_code, hash_password, sha256_hex


def _engine(database_url: str | None = None):
    url = database_url or get_settings().database_url
    return create_async_engine(url, pool_pre_ping=True)


async def admin_init(username: str, password: str, database_url: str | None = None) -> dict:
    settings = get_settings()
    engine = _engine(database_url)
    try:
        await create_schema_and_tables(engine, settings.db_schema)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:  # type: AsyncSession
            existing = (
                await db.execute(select(Owner).where(Owner.display_name == username))
            ).scalar_one_or_none()
            if existing is not None:
                raise SystemExit(f"用户 {username} 已存在")
            owner = Owner(
                display_name=username,
                password_hash=hash_password(password),
                approved_at=dt.datetime.now(dt.timezone.utc),
            )
            db.add(owner)
            await db.flush()
            human_row = await db.execute(select(Member).where(Member.name == username))
            if human_row.scalar_one_or_none() is not None:
                raise SystemExit(f"成员名 {username} 已被占用，无法创建 human 成员")
            human = Member(name=username, platform="web", owner_id=owner.id, human=True)
            db.add(human)
            await db.commit()
            return {"owner": owner.display_name, "humanMember": human.name}
    finally:
        await engine.dispose()


def build_pair_instruction(
    code: str, group_id: int | None = None, reclaim_name: str | None = None
) -> str:
    """给 agent 的接入指令：只交代变量、命令和判据，细节留给接入手册。"""
    addr = (
        "Martin Hub 的地址是 <Hub 地址>（可能是域名、固定 IP、局域网地址或本机）。"
        "把命令里的 <Hub 地址> 和 <平台名> 换成实际值；不知道就问我，别猜、别默认本机。\n"
    )
    gate = (
        "如果你在这个对话里跑不了命令、发不了 HTTP 请求，直接告诉我你接不了："
        "不要谎报已接入，也不要让我替你跑命令。\n"
    )
    tail = (
        "接入成功后按手册 §5–§11 依次做完：保管 token → 写简介 → 设定时检查 → "
        "完成我最初的要求 → 汇报，并在汇报结尾问我一次「多久查一次新消息」。"
        "上线/下线状态也要上报：接入完成和每次运行开始时执行 martin presence online；"
        "下线前先确认 listener、唤醒通道、定时任务、Martin 进程/线程和相关终端全部停止，"
        "最后才执行 martin presence offline；Web「接入 Agent」页、群成员列表与 agent 私聊用绿/灰点显示你的状态。"
        "推送不在接入动作里：不要自己配置唤醒通道、不要起 martin wake --listen 或写 listener 脚本、也不要自己装插件；"
        "要不要即时叫醒由我决定，我说要时你再按手册 §8 配一条。"
        "一旦启用推送，唤醒入口只能有一个（手册 §8.1）：宿主插件 / listener / webhook 三选一，不能并行；"
        "宿主插件由我在 DSH profile 里启用，启用后你跳过定时检查、也不要自己发帖回群。"
    )
    tail_reclaim = (
        "找回成功后按手册 §5 存好新 token，然后按 §11 继续日常检查；"
        "简介和定时检查任务还在的话不必重做。最后把结果汇报给我。"
        "运行开始执行 martin presence online；下线前确认所有 Martin 运行入口停止后再执行 offline；如果已经在常驻 listener 或由宿主插件唤醒（由我启用），保持 online；推送入口只能有一个（手册 §8.1），不要自己新起 listener。"
    )
    if reclaim_name:
        return (
            f"【Martin 找回身份】你之前以 \"{reclaim_name}\" 接入过我的 Martin，现在 token 丢了。\n"
            f"{addr}"
            "在你的工作空间根目录执行：\n"
            "  export MARTIN_URL=<Hub 地址>/api/v1\n"
            f"  martin pair {code} --name \"{reclaim_name}\" --platform <平台名> "
            "--token-file '.martin/<平台名>.token' --reclaim\n"
            "带 --reclaim 成功，你就以原身份回来了：群和记录都在，旧 token 立即失效。\n"
            "若返回 member_active，说明那个 agent 还在运行，先告诉我，等它停够 15 分钟再重试。\n"
            f"{gate}{tail_reclaim}"
        )
    join = (
        f"\n这个码还会把你加入群 id={group_id}（响应的 join 说明是否已进）。\n"
        if group_id
        else "\n"
    )
    return (
        "【Martin 接入】照下面做，细节以《Martin Agent 接入手册》为准（和这条一起发给你的那份）。\n"
        f"{addr}"
        "先按手册 §3 判断自己现在是什么状态；还没接入过就在你的工作空间根目录执行：\n"
        "  export MARTIN_URL=<Hub 地址>/api/v1\n"
        f"  martin pair {code} --name \"<用户名> <平台名>\" --platform <平台名> "
        "--token-file '.martin/<平台名>.token'\n"
        "成功判据：输出\"已接入为 …\"，token 已写入 .martin/（把 .martin/ 加进忽略规则，绝不提交）。"
        f"{join}"
        f"{gate}{tail}"
    )


async def admin_pair_code(
    owner_name: str,
    group_id: str | None = None,
    reclaim_name: str | None = None,
    database_url: str | None = None,
) -> tuple[str, str]:
    settings = get_settings()
    engine = _engine(database_url)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            owner = (
                await db.execute(select(Owner).where(Owner.display_name == owner_name))
            ).scalar_one_or_none()
            if owner is None:
                raise SystemExit(f"用户 {owner_name} 不存在，请先运行 martin admin init")

            internal_group_id = None
            if group_id is not None:
                group = (
                    await db.execute(
                        select(Group).where(
                            Group.public_id == group_id, Group.dissolved_at.is_(None)
                        )
                    )
                ).scalar_one_or_none()
                if group is None:
                    raise SystemExit(f"群 {group_id} 不存在")
                internal_group_id = group.id

            code = gen_pairing_code()
            expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
                hours=settings.pairing_code_ttl_hours
            )
            db.add(
                PairingCode(
                    code_hash=sha256_hex(code),
                    owner_id=owner.id,
                    group_id=internal_group_id,
                    reclaim_name=reclaim_name,
                    expires_at=expires,
                )
            )
            await db.commit()

            return code, build_pair_instruction(code, group_id, reclaim_name)
    finally:
        await engine.dispose()


async def create_owner_with_human(db: AsyncSession, username: str, password: str) -> Owner:
    existing = (
        await db.execute(select(Owner).where(Owner.display_name == username))
    ).scalar_one_or_none()
    if existing is not None:
        raise AppError("owner_exists", "用户已存在", 409)
    taken = (
        await db.execute(select(Member).where(Member.name == username))
    ).scalar_one_or_none()
    if taken is not None:
        raise AppError("member_exists", "成员名已被占用，无法创建 human 成员", 409)
    owner = Owner(display_name=username, password_hash=hash_password(password))
    db.add(owner)
    await db.flush()
    db.add(Member(name=username, platform="web", owner_id=owner.id, human=True))
    await db.commit()
    await db.refresh(owner)
    return owner


async def create_admin(username: str, password: str, database_url: str | None = None) -> dict:
    settings = get_settings()
    engine = _engine(database_url)
    try:
        await create_schema_and_tables(engine, settings.db_schema)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            existing = (
                await db.execute(select(Admin).where(Admin.username == username))
            ).scalar_one_or_none()
            if existing is not None:
                raise SystemExit(f"管理员 {username} 已存在")
            admin = Admin(username=username, password_hash=hash_password(password))
            db.add(admin)
            await db.commit()
            return {"admin": admin.username}
    finally:
        await engine.dispose()


async def set_admin_disabled(username: str, disabled: bool, database_url: str | None = None) -> dict:
    settings = get_settings()
    engine = _engine(database_url)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            admin = (
                await db.execute(select(Admin).where(Admin.username == username))
            ).scalar_one_or_none()
            if admin is None:
                raise SystemExit(f"管理员 {username} 不存在")
            admin.disabled_at = dt.datetime.now(dt.timezone.utc) if disabled else None
            await db.commit()
            return {"admin": admin.username, "disabled": disabled}
    finally:
        await engine.dispose()


async def list_admins(database_url: str | None = None) -> list[dict]:
    settings = get_settings()
    engine = _engine(database_url)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            rows = (await db.execute(select(Admin).order_by(Admin.id))).scalars().all()
            return [
                {"username": a.username, "disabled": a.disabled_at is not None}
                for a in rows
            ]
    finally:
        await engine.dispose()
