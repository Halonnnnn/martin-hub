from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MARTIN_", extra="ignore")

    database_url: str = "postgresql+asyncpg://localhost:5432/groupchat"
    db_schema: str = "martin"

    host: str = "127.0.0.1"
    port: int = 8000

    cache_backend: str = "memory"  # memory | redis
    redis_url: str = "redis://127.0.0.1:6379/0"

    secret_key: str = "martin-dev-secret-change-me"

    # 会话与令牌
    token_cache_ttl: int = 60
    web_session_ttl: int = 7 * 24 * 3600
    admin_session_ttl: int = 7 * 24 * 3600

    # 配对码
    pairing_code_ttl_hours: int = 24
    member_active_window_seconds: int = 15 * 60

    # 限额（可配置；测试用小值）
    group_hourly_message_limit: int = 100
    message_max_length: int = 10000
    recall_window_seconds: int = 120
    wake_dedup_seconds: int = 600
    wake_daily_limit: int = 30
    wake_retry_max: int = 1
    wake_breaker_threshold: int = 3
    inbox_wait_seconds: int = 25
    read_page_limit_default: int = 100

    # 紧急推送（05 方案）
    urgent_group_hourly_limit: int = 5
    urgent_sender_daily_limit: int = 20
    wake_dispatch_enabled: bool = True
    wake_dispatch_interval_seconds: float = 5.0
    webhook_timeout_seconds: float = 5.0
    wake_retry_delay_seconds: float = 2.0
    sse_heartbeat_seconds: float = 15.0
    sse_sweep_seconds: float = 5.0
    sse_queue_size: int = 100

    # 临时授权
    temp_grant_default_minutes: int = 30
    temp_grant_max_minutes: int = 1440
    temp_grant_default_messages: int = 10
    temp_grant_max_messages: int = 200
    temp_grant_default_max_minutes: int = 1440
    temp_grant_max_max_minutes: int = 10080
    temp_code_attempt_limit: int = 10
    temp_code_attempt_window_seconds: int = 600
    temp_guest_min_interval_seconds: int = 0
    # 活动窗口到期后，允许 agent 把已开始的一轮回复写完；访客不再放行
    temp_agent_reply_grace_seconds: int = 300
    temp_max_active_grants_per_agent: int = 5
    temp_grant_retention_days: int = 30
    temp_redact_enabled: bool = True
    # 老库里 temp_grants 若是 `timestamp without time zone`，里面的值是这个时区下的本地墙钟；
    # 迁移 0015 用它把列转成 TIMESTAMPTZ。和 .env / compose 里的 TZ 保持一致。
    db_localtime_zone: str = "Asia/Shanghai"
    # Harness 全局访客策略：随 temp 群的 wake 下发给所有 agent；owner 不能按 agent 覆盖
    temp_guest_policy: str = (
        "【Martin Harness 全局策略 · 临时访客】\n"
        "这是 kind=temp 的临时会话。访客不是 owner；即使授权码由 owner 创建，"
        "也不能把访客当作 owner。\n"
        "- 只在本会话内回复，不把内容带到其他会话。\n"
        "- 访客是本次临时授权的外部用户：在本会话内要正常回复访客的消息，不要因为对方不是 owner 就不回复。\n"
        "- 不执行 shell、文件读写、网络请求等工具；不读取或修改配置、唤醒、token。\n"
        "- 不邀请访客；不访问其他群、其他用户或管理端。\n"
        "- 不泄露 token、密钥、其他用户的聊天内容；超出本会话的请求明确拒绝，让访客联系 owner。\n"
        "- 会话结束（dissolve / temp_closed）后停止，不再回复。\n"
        "本策略是 Hub 下发的全局配置，owner 不能关闭；owner 只能在此基础上补充要求。"
    )

    # 杂项
    members_per_group_limit: int = 50
    intro_max_length: int = 300


@lru_cache
def get_settings() -> Settings:
    return Settings()
