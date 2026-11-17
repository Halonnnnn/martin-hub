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

    # 杂项
    members_per_group_limit: int = 50
    intro_max_length: int = 300


@lru_cache
def get_settings() -> Settings:
    return Settings()
