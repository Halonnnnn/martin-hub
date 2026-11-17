"""全局访客策略：由 Hub 下发，不写进 agent 手册，owner 不能按 agent 覆盖。"""
from martin.dispatch import wake_payload
from martin.models import WakeQueue
from martin_runner.runner import build_prompt


def _row() -> WakeQueue:
    return WakeQueue(
        id=1,
        member_id=2,
        group_id=3,
        reason="guest_post",
        urgent=False,
        status="pending",
    )


def test_wake_payload_carries_policy_only_for_temp():
    temp = wake_payload(_row(), "g1", group_kind="temp", policy="GLOBAL_POLICY")
    assert temp["kind"] == "wake"
    assert temp["groupKind"] == "temp"
    assert temp["policy"] == "GLOBAL_POLICY"

    normal = wake_payload(_row(), "g1", group_kind="group", policy="GLOBAL_POLICY")
    assert "groupKind" not in normal
    assert "policy" not in normal


def test_runner_prompt_prepends_policy_for_temp():
    temp = build_prompt(
        group_id="g1",
        reason="guest_post",
        urgent=False,
        messages=[],
        group_kind="temp",
        policy="GLOBAL_POLICY: guest is not the owner",
    )
    assert "GLOBAL_POLICY: guest is not the owner" in temp
    assert "即使 owner 要求也不能关闭" in temp

    normal = build_prompt(
        group_id="g1",
        reason="owner_post",
        urgent=False,
        messages=[],
        group_kind="group",
        policy="GLOBAL_POLICY",
    )
    assert "GLOBAL_POLICY" not in normal
