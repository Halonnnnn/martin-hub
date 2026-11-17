from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PairRequest(BaseModel):
    code: str
    name: str = Field(min_length=1, max_length=100)
    platform: str = Field(default="other", max_length=50)
    reclaim: bool = False


class MePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    intro: str | None = Field(default=None, max_length=300)


class PresenceUpdate(BaseModel):
    status: str = Field(pattern="^(online|offline)$")


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    topic: str = Field(default="", max_length=500)
    rules: str = Field(default="", max_length=2000)
    visibility: str = Field(default="link")
    category: str | None = None
    allowSameName: bool = False


class GroupPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    topic: str | None = Field(default=None, max_length=500)
    rules: str | None = Field(default=None, max_length=2000)


class JoinById(BaseModel):
    pass


class JoinByInvite(BaseModel):
    invite: str


class ResetInvite(BaseModel):
    pass


class MessageSend(BaseModel):
    text: str = Field(min_length=1)
    mentions: list[str] = Field(default_factory=list)
    replyTo: int | None = None
    opKey: str | None = Field(default=None, max_length=100)
    relay: bool = False
    ignoreNew: bool = False
    priority: str = Field(default="normal", pattern="^(normal|urgent)$")
    question: "QuestionBody | None" = None


class QuestionOption(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    consequence: str | None = Field(default=None, max_length=240)


class QuestionBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=500)
    options: list[QuestionOption] = Field(min_length=2, max_length=4)
    recommendedIndex: int | None = Field(default=None, ge=0, le=3)
    recommendedReason: str | None = Field(default=None, max_length=240)


MessageSend.model_rebuild()


class TempGrantCreate(BaseModel):
    accessMinutes: int = Field(ge=1, le=1440)
    maxAccessMinutes: int = Field(ge=1, le=43200)
    maxMessages: int = Field(ge=1, le=200)
    label: str | None = Field(default=None, max_length=20)


class TempRedeem(BaseModel):
    sessionId: str = Field(min_length=1, max_length=16)
    code: str = Field(min_length=1, max_length=40)
    label: str | None = Field(default=None, max_length=20)


class TempMessageSend(BaseModel):
    """访客消息白名单：只有文本与幂等键，任何扩展字段都拒绝。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    opKey: str | None = Field(default=None, max_length=100)


class WakeConfigPut(BaseModel):
    kind: str = Field(pattern="^(webhook|listener)$")
    url: str | None = Field(default=None, max_length=500)
    secret: str | None = Field(default=None, max_length=200)
    muted: bool | None = None
    allowFrom: str | None = Field(default=None, pattern="^(owner|any_member|list)$")
    allowList: list[str] | None = Field(default=None, max_length=50)


class WakeMutePatch(BaseModel):
    muted: bool


class LeaseAcquire(BaseModel):
    holder: str = Field(min_length=16, max_length=80)


class LeaseRelease(BaseModel):
    holder: str = Field(min_length=16, max_length=80)
    fence: str = Field(min_length=1, max_length=100)


class WebSetup(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=6, max_length=200)


class WebPairCode(BaseModel):
    groupId: str | None = None
    reclaimName: str | None = Field(default=None, max_length=100)


class ReadMark(BaseModel):
    seq: int | None = None
    all: bool = False


class WebLogin(BaseModel):
    username: str
    password: str


class AdminLogin(BaseModel):
    username: str
    password: str


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=6, max_length=200)


class AdminPasswordReset(BaseModel):
    password: str = Field(min_length=6, max_length=200)


class FriendRequestCreate(BaseModel):
    to: str
    note: str = Field(default="", max_length=300)


class FriendRequestAction(BaseModel):
    action: str = Field(pattern="^(accept|ignore)$")


class ChatCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    with_: str = Field(alias="with")


class WebPasswordChange(BaseModel):
    currentPassword: str
    newPassword: str = Field(min_length=6, max_length=200)


UI_THEMES = ("classic", "warm", "dark", "glass", "brutal")


class WebThemeUpdate(BaseModel):
    uiTheme: Literal[UI_THEMES]


class WebGroupAgentInvite(BaseModel):
    agentId: str


class WebGroupArchive(BaseModel):
    archived: bool
