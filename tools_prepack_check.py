"""打包前自检：源码可导入、无残留旧标识符、alembic 只有一个 head。

用法：.venv/bin/python tools_prepack_check.py
失败时打印原因并以非 0 退出，pack.sh 会中止打包。
"""
import importlib
import pathlib
import re
import sys

BAD_IDENTIFIERS = ("UTCDateTime",)
MODULES = ("martin.models", "martin.services", "martin.routers.temp", "martin.main")


def main() -> int:
    bad: list[str] = []

    for path in sorted(pathlib.Path("martin").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for name in BAD_IDENTIFIERS:
            if name in text:
                bad.append(f"{path}: 残留旧标识符 {name}")

    for mod in MODULES:
        try:
            importlib.import_module(mod)
        except Exception as exc:  # noqa: BLE001 - 打包前只想要原因
            bad.append(f"{mod} 导入失败: {exc!r}")

    versions = list(pathlib.Path("alembic/versions").glob("*.py"))
    revs, downs = {}, {}
    for path in versions:
        text = path.read_text(encoding="utf-8")
        rev = re.search(r'^revision\s*:\s*str\s*=\s*"([^"]+)"', text, re.M)
        down = re.search(
            r'^down_revision\s*:[^=]*=\s*(?:"([^"]+)"|None)', text, re.M
        )
        if rev:
            revs[rev.group(1)] = path.name
            downs[rev.group(1)] = down.group(1) if down else None
    referenced = {d for d in downs.values() if d}
    heads = [r for r in revs if r not in referenced]
    if len(heads) != 1:
        bad.append(f"alembic head 不唯一：{[(h, revs[h]) for h in heads]}")

    if bad:
        print("\n".join(bad), file=sys.stderr)
        return 1
    print("自检通过：import ok，迁移 head 唯一")
    return 0


if __name__ == "__main__":
    sys.exit(main())
