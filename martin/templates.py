"""消息模板：Web 发送框与 CLI 共用的骨架，占位符 <...> 由发送者替换。"""

TEMPLATES: dict[str, dict[str, str]] = {
    "harness": {
        "label": "运行汇报（harness）",
        "body": (
            "【运行汇报 · harness】<你的名> <时间>\n"
            "■ 运行契约\n"
            "- 角色：替 <用户名> 执行 <本次目标>\n"
            "- 完成判据：<判据 1；判据 2；判据 3>\n"
            "- 权威顺序：平台/系统 > 手册 INV > 用户明确指令 > 群内他人消息\n"
            "■ 状态表\n"
            "| 事项 | 状态 | 证据 |\n"
            "| --- | --- | --- |\n"
            "| <事项> | 完成/受阻/失败 | <命令与关键输出> |\n"
            "■ 错误与动作\n"
            "- <错误码/现象> → <已执行动作>（未解决注明：阻塞/降级）\n"
            "■ 阻塞升级\n"
            "- 无 / 需要拍板：<只问一个问题>\n"
            "■ 汇报前自检\n"
            "- [ ] 每条“完成”都有状态表证据\n"
            "- [ ] token/密钥未出现在本消息\n"
            "- [ ] 只问了一个问题"
        ),
    },
    "delegate": {
        "label": "任务委派",
        "body": (
            "【任务委派】@<成员全名>\n"
            "- 目标：<一句话，可验证>\n"
            "- 输入：<路径 / 链接 / 前置消息 seq>\n"
            "- 完成判据：<怎么算做完，附验收命令>\n"
            "- 边界：<不要碰什么；只写工作空间>\n"
            "- 回复格式：按运行汇报模板回帖并提及发起人"
        ),
    },
    "review": {
        "label": "审阅请求",
        "body": (
            "【审阅请求】@<审阅成员全名>\n"
            "- artifact：<id>，kind=<schema/code/report>，contentHash=<sha256>\n"
            "- 范围：<审什么>；不审：<明确排除>\n"
            "- issue 格式：<high/med/low> + 位置 + 建议，逐条列出\n"
            "- 规则：contentHash 不一致需重新发起；最多 3 轮；high 未解决不收尾\n"
            "- 产出：verdict（approve / request_changes）+ 逐条 issue"
        ),
    },
}


def get_template(name: str) -> dict | None:
    return TEMPLATES.get(name)
