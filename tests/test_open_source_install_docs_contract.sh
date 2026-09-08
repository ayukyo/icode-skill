#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

python3 - "$ROOT" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
english = (root / "README.md").read_text(encoding="utf-8")
chinese = (root / "README.zh-CN.md").read_text(encoding="utf-8")
step = (root / "steps/install.md").read_text(encoding="utf-8")
router = (root / "SKILL.md").read_text(encoding="utf-8")

repo = "git clone https://github.com/ayukyo/icode-skill ~/icode-skill"
primary = [repo, "cd ~/icode-skill", "./install.sh --client all"]
compat_clone = (
    "git clone https://github.com/ayukyo/icode-skill "
    "~/.claude/skills/icode"
)

checks = {
    "English primary install is clone -> cd -> root installer":
        all(item in english for item in primary)
        and [english.index(item) for item in primary]
        == sorted(english.index(item) for item in primary),
    "Chinese primary install is clone -> cd -> root installer":
        all(item in chinese for item in primary)
        and [chinese.index(item) for item in primary]
        == sorted(chinese.index(item) for item in primary),
    "English documents the direct-Claude compatibility path":
        compat_clone in english and "/icode install --client all" in english,
    "Chinese documents the direct-Claude compatibility path":
        compat_clone in chinese and "/icode install --client all" in chinese,
    "Both READMEs state Claude-only is the default":
        "default: claude" in english.lower()
        and "默认只安装 Claude" in chinese,
    "Both READMEs state unmanaged conflicts are refused":
        "unmanaged same-name" in english.lower()
        and "未托管的同名技能" in chinese,
    "Both READMEs label sync-to-global as a developer command":
        "developer update" in english.lower()
        and "开发者更新" in chinese,
    "Workflow install step invokes the repository root installer":
        "bash <工程根>/install.sh" in step
        and "ICODE 与共享技能" in step,
    "Router labels install as the unified open-source installer":
        "开源统一安装步骤" in router,
    "Unsupported no-auto-install is absent from public contracts":
        "--no-auto-install" not in english
        and "--no-auto-install" not in chinese
        and "--no-auto-install" not in step
        and "--no-auto-install" not in router,
}

failed = 0
for label, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'} {label}")
    failed += not passed
print(f"\nResult: {len(checks) - failed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
PY
