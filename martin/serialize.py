import datetime as dt


def _iso(v: dt.datetime | None) -> str | None:
    return v.isoformat() if v is not None else None


def author_dict(m, viewer) -> dict:
    is_guest = getattr(m, "platform", None) == "guest"
    your_owner = bool(
        viewer is not None
        and viewer.owner_id is not None
        and m.owner_id == viewer.owner_id
        and not m.human
        and m.id != viewer.id
    )
    if is_guest:
        label = "guest"
    elif m.human and your_owner:
        label = "your owner"
    elif m.human:
        label = "person"
    elif your_owner and viewer is not None and viewer.human:
        label = "your agent"
    elif your_owner:
        label = "your owner's agent"
    else:
        label = "agent"
    return {
        "id": m.public_id,
        "name": m.name,
        "platform": m.platform,
        "human": m.human,
        "guest": is_guest,
        "yourOwner": your_owner or bool(m.human and viewer is not None and viewer.owner_id is not None and m.owner_id == viewer.owner_id),
        "ownerId": m.owner_id,
        "label": label,
    }


def compute_reach(m, wake_cfg, checkin_row, now: dt.datetime, connected: bool = False) -> str:
    if wake_cfg is not None:
        if wake_cfg.muted:
            return "muted"
        if wake_cfg.enabled and (wake_cfg.kind != "listener" or connected):
            return "wake"
        return "away"
    if checkin_row is not None:
        return "checkin"
    return "away"


def member_dict(m, viewer, reach: str = "away", push_connected: bool = False) -> dict:
    d = author_dict(m, viewer)
    d.update(
        {
            "intro": m.intro,
            "reach": reach,
            "pushConnected": push_connected,
            "presence": getattr(m, "presence", "offline") or "offline",
            "presenceAt": _iso(getattr(m, "presence_at", None)),
            "createdAt": _iso(m.created_at),
            "lastSeenAt": _iso(m.last_seen_at),
        }
    )
    if viewer is not None and m.id == viewer.id:
        d["tokenPrefix"] = getattr(m, "token_prefix", None)
    return d


def group_dict(g, invite_code: str | None = None) -> dict:
    d = {
        "id": g.public_id,
        "name": g.name,
        "topic": g.topic,
        "rules": g.rules,
        "kind": g.kind,
        "visibility": g.visibility,
        "category": g.category,
        "dissolved": g.dissolved_at is not None,
        "archived": getattr(g, "archived_at", None) is not None,
        "createdAt": _iso(g.created_at),
    }
    if invite_code:
        d["inviteLink"] = f"/join/{invite_code}"
        d["inviteMessage"] = f"{g.name}：{g.topic or '（无主题）'}。加入链接：{d['inviteLink']}"
    return d


def owner_admin_dict(owner, agent_count: int, human_name: str | None) -> dict:
    return {
        "id": owner.id,
        "displayName": owner.display_name,
        "email": owner.email,
        "humanMember": human_name,
        "agentCount": agent_count,
        "approved": owner.approved_at is not None,
        "disabled": owner.disabled_at is not None,
        "createdAt": _iso(owner.created_at),
    }


def message_dict(msg, author, viewer, new: bool = False) -> dict:
    recalled = msg.recalled_at is not None
    d = {
        "seq": msg.seq,
        "author": author_dict(author, viewer),
        "type": msg.type,
        "priority": getattr(msg, "priority", "normal") or "normal",
        "text": None if recalled else msg.text,
        "mentions": msg.mentions or [],
        "replyToSeq": msg.reply_to_seq,
        "relay": msg.relay,
        "recalled": recalled,
        "new": new,
        "createdAt": _iso(msg.created_at),
    }
    if msg.type == "question" and not recalled:
        d["question"] = msg.payload
    return d
