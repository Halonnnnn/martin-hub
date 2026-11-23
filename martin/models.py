import datetime as dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from .config import get_settings
from .timeutil import now_utc
from .security import gen_public_id

settings = get_settings()
SCHEMA = settings.db_schema


def _member_public_id(context) -> str:
    human = bool(context.get_current_parameters().get("human", False))
    return gen_public_id("u" if human else "a")


class Base(DeclarativeBase):
    pass


def _localtz(name: str) -> dt.tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return dt.timezone.utc


class AwareDateTime(TypeDecorator):
    """时区安全的时间列：读回来一定带 tzinfo，任何比较都是同一时间基准。

    - TIMESTAMPTZ 正常返回 aware 值，归一到 UTC 直接用；
    - `timestamp without time zone`（老库/手工建表/换过驱动）返回 naive 值，
      和 aware 的 now 直接比较会抛 TypeError。这里不盲目按 UTC 解释：
      Postgres 的 `timestamp` 列存的是会话时区下的**本地墙钟**，按 UTC 硬读
      会整整偏一个时区（UTC+8 就是「刚进来就到期」）；所以按 `localtime_zone`
      解释 naive 值，再统一转 UTC。
    """

    impl = DateTime(timezone=True)
    cache_ok = False  # 实例带着 localtime_zone，缓存键要区分

    def __init__(self, localtime_zone: str = "UTC", **kwargs):
        super().__init__(**kwargs)
        self.localtime_zone = localtime_zone

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=_localtz(self.localtime_zone)).astimezone(
                dt.timezone.utc
            )
        return value.astimezone(dt.timezone.utc)


def _ts() -> Mapped[dt.datetime]:
    # 应用侧写 UTC；不用 server_default=now()：会话时区不同会写入本地墙钟
    return mapped_column(
        DateTime(timezone=True), default=now_utc, server_default=func.now()
    )


class Owner(Base):
    __tablename__ = "owners"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    session_version: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    created_at: Mapped[dt.datetime] = _ts()
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    ui_theme: Mapped[str | None] = mapped_column(String(20))


class Admin(Base):
    """系统管理员：独立于聊天用户，只维护系统信息，不进群、不拥有 agent。"""

    __tablename__ = "admins"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[dt.datetime] = _ts()
    disabled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Member(Base):
    __tablename__ = "members"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, default=_member_public_id
    )
    name: Mapped[str] = mapped_column(String(100), unique=True)
    platform: Mapped[str] = mapped_column(String(50))
    owner_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.owners.id"))
    human: Mapped[bool] = mapped_column(Boolean, default=False)
    intro: Mapped[str] = mapped_column(String(300), default="")
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    token_prefix: Mapped[str | None] = mapped_column(String(16))
    token_revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    presence: Mapped[str] = mapped_column(
        String(10), default="offline", server_default="offline"
    )
    presence_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _ts()
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class PairingCode(Base):
    __tablename__ = "pairing_codes"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.owners.id"))
    group_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.groups.id"))
    reclaim_member_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.members.id"))
    reclaim_name: Mapped[str | None] = mapped_column(String(100))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _ts()


class Group(Base):
    __tablename__ = "groups"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, default=lambda: gen_public_id("g")
    )
    name: Mapped[str] = mapped_column(String(100))
    topic: Mapped[str] = mapped_column(String(500), default="")
    rules: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(10), default="group")
    visibility: Mapped[str] = mapped_column(String(10), default="link")
    category: Mapped[str | None] = mapped_column(String(30))
    join_question: Mapped[str | None] = mapped_column(String(300))
    join_answer_hash: Mapped[str | None] = mapped_column(String(64))
    invite_code: Mapped[str] = mapped_column(String(20), unique=True)
    owner_member_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.members.id"))
    created_at: Mapped[dt.datetime] = _ts()
    dissolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "member_id"),
        {"schema": SCHEMA},
    )

    group_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.groups.id"), primary_key=True
    )
    member_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.members.id"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(10), default="member")
    joined_at: Mapped[dt.datetime] = _ts()
    last_read_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    told_owner_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class TempGrant(Base):
    """一次性临时授权：一码 = 一次核销 = 一个临时会话。

    访客在会话内是 owner 的临时授权人；不新增 responder/scoped token，
    agent 用现有 Member token 正常收发，服务端只限制范围。
    """

    __tablename__ = "temp_grants"
    __table_args__ = (
        Index("ix_temp_grants_owner_created", "owner_id", "created_at"),
        Index("ix_temp_grants_agent", "agent_member_id"),
        Index("ix_temp_grants_expires", "expires_at"),
        Index("ix_temp_grants_max_access", "max_access_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, default=lambda: gen_public_id("tg")
    )
    owner_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.owners.id"))
    agent_member_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.members.id"))
    label: Mapped[str | None] = mapped_column(String(20))
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    access_seconds: Mapped[int] = mapped_column(Integer)
    max_access_seconds: Mapped[int] = mapped_column(Integer)
    max_access_at: Mapped[dt.datetime] = mapped_column(AwareDateTime)
    max_messages: Mapped[int] = mapped_column(Integer)
    messages_used: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    expires_at: Mapped[dt.datetime] = mapped_column(AwareDateTime)
    session_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.groups.id"))
    guest_member_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.members.id"))
    redeemed_at: Mapped[dt.datetime | None] = mapped_column(AwareDateTime)
    redeemed_ip: Mapped[str | None] = mapped_column(String(64))
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(AwareDateTime)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(AwareDateTime)
    closed_at: Mapped[dt.datetime | None] = mapped_column(AwareDateTime)
    close_reason: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[dt.datetime] = mapped_column(  # 迁移 0013 给的是 now()
        AwareDateTime, default=now_utc, server_default=func.now()
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("group_id", "seq"),
        UniqueConstraint("author_id", "op_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.groups.id"))
    seq: Mapped[int] = mapped_column(BigInteger)
    author_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.members.id"))
    type: Mapped[str] = mapped_column(String(30), default="text")
    priority: Mapped[str] = mapped_column(
        String(10), default="normal", server_default="normal"
    )
    text: Mapped[str] = mapped_column(Text)
    mentions: Mapped[list] = mapped_column(JSONB, default=list)
    reply_to_seq: Mapped[int | None] = mapped_column(BigInteger)
    relay: Mapped[bool] = mapped_column(Boolean, default=False)
    op_key: Mapped[str | None] = mapped_column(String(100))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = _ts()
    recalled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class WakeQueue(Base):
    __tablename__ = "wake_queue"
    __table_args__ = (
        Index("ix_wake_queue_dispatch", "status", text("urgent DESC"), "next_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.members.id"))
    group_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.groups.id"))
    reason: Mapped[str] = mapped_column(String(40))
    urgent: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[dt.datetime] = _ts()


class WakeEvent(Base):
    __tablename__ = "wake_events"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.members.id"))
    group_id: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.groups.id"))
    reason: Mapped[str] = mapped_column(String(40))
    urgent: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    kind: Mapped[str | None] = mapped_column(String(20))
    result: Mapped[str] = mapped_column(String(20))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = _ts()


class WakeConfig(Base):
    __tablename__ = "wake_config"
    __table_args__ = {"schema": SCHEMA}

    member_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.members.id"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    url: Mapped[str | None] = mapped_column(String(500))
    secret_hash: Mapped[str | None] = mapped_column(String(64))
    secret_cipher: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    muted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    allow_from: Mapped[str] = mapped_column(
        String(12), default="any_member", server_default="any_member"
    )
    allow_list: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    configured_at: Mapped[dt.datetime] = _ts()


class Checkin(Base):
    __tablename__ = "checkins"
    __table_args__ = {"schema": SCHEMA}

    member_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.members.id"), primary_key=True
    )
    every_minutes: Mapped[int] = mapped_column(Integer)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Friendship(Base):
    __tablename__ = "friendships"
    __table_args__ = (
        UniqueConstraint("member_a", "member_b"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    member_a: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.members.id"))
    member_b: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.members.id"))
    created_at: Mapped[dt.datetime] = _ts()


class FriendRequest(Base):
    __tablename__ = "friend_requests"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    from_member: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.members.id"))
    to_member: Mapped[int] = mapped_column(ForeignKey(f"{SCHEMA}.members.id"))
    note: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[dt.datetime] = _ts()


class ReceiverLease(Base):
    """一个成员同时只允许一个 listener 持有处理租约；fence 用于校验写路径。"""

    __tablename__ = "receiver_leases"
    __table_args__ = {"schema": SCHEMA}

    member_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.members.id"), primary_key=True
    )
    holder: Mapped[str] = mapped_column(String(80))
    fence: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
