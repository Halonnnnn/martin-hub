"""时间基准：整个 Hub 只认 UTC。

所有截止时间都存成带时区的 UTC（TIMESTAMPTZ），比较前也一律换算成 UTC；
渲染交给浏览器按用户本地时区完成，服务端不拼任何 UTC/本地墙钟文案。
"""
import datetime as dt


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def as_utc(value: dt.datetime | None) -> dt.datetime | None:
    """把可能 naive 的时间按 UTC 解释，保证和 now_utc() 可直接比较。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)
