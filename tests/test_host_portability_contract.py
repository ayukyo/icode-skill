"""回归共享工作流合同中的跨宿主可执行性。"""
from pathlib import Path
import json
import os
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_ticket_directory_rules_do_not_require_gnu_grep():
    text = read("references/dir_and_metadata.md")
    assert "grep -oP" not in text
    assert "resolve-ticket" in text
    assert "REUSE=2" in text


def test_directory_selection_uses_numeric_latest_and_detects_entry_ambiguity(tmp_path):
    root = tmp_path / "project"
    output = root / ".icode_output"
    for number in (2, 10):
        (output / f".icode_output_{number}").mkdir(parents=True)
    candidate = output / ".icode_output_10"
    (candidate / ".ico_metadata.json").write_text(json.dumps({"status": "init_in_progress"}))
    (candidate / "00_init.md").write_text("需求")
    rules = read("references/dir_and_metadata.md")
    section = rules.split("### 复用 / 创建新目录决策", 1)[1].split("```bash", 1)[1].split("```", 1)[0]
    snippet = section[section.index("LAST=$("):section.index("# REUSE=2")]
    snippet = snippet.replace('"{ICODE_SKILL_ROOT}"', shlex.quote(str(ROOT)))
    snippet = snippet.replace("python3 -c", f"{shlex.quote(sys.executable)} -c")
    env = dict(os.environ, WORKSPACE_ROOT=str(root))
    result = subprocess.run(["bash", "-c", snippet + '\nprintf "%s:%s\\n" "$LAST" "$REUSE"'],
                            env=env, text=True, capture_output=True, check=True)
    assert result.stdout.strip() == "10:2"
    (candidate / "01_plan.md").write_text("已有计划")
    result = subprocess.run(["bash", "-c", snippet + '\nprintf "%s:%s\\n" "$LAST" "$REUSE"'],
                            env=env, text=True, capture_output=True, check=True)
    assert result.stdout.strip() == "10:0"
    (candidate / "01_plan.md").unlink()
    (candidate / ".ico_metadata.json").write_text("not-json")
    result = subprocess.run(["bash", "-c", snippet], env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "禁止猜测复用" in result.stderr


def test_effective_mode_controls_directory_review_guidance():
    text = read("references/dir_and_metadata.md")
    mode_rule = text.split("**`mode` 字段**", 1)[1].split("\n\n", 1)[0]
    assert "`effective_mode(metadata)`" in mode_rule
    assert "有效 full 的 review 为默认多轮+对抗" in mode_rule
    assert "有效 fast 为单轮无对抗与 Reverse" in mode_rule


def test_entry_and_compact_rules_allow_evidenced_no_mcp_fallback():
    for path in ("SKILL.md", "references/full_workflow.md"):
        matrix = read(path).split("### 强制阻断边界矩阵", 1)[1].split("**各步骤", 1)[0]
        l1 = next(line for line in matrix.splitlines() if line.startswith("| **L1"))
        assert "必要能力缺失且无可执行替代" in l1
        assert "MCP 完全不可用" not in l1
        assert "即使全部 MCP 不可用" in matrix
        assert "保留证据债务" in matrix
    compact = read("steps/fast.md").split("## 思考与 MCP 边界", 1)[1]
    assert "结构化思考" in compact
    assert "`unavailable_before_call`" in compact


def test_host_review_does_not_require_claude_only_discovery_or_wait():
    text = read("references/adversarial.md")
    assert '禁止跳过 ToolSearch 直接判定 no_spawn_env' not in text
    assert '唯一可机械执行的等待/超时载体' not in text
    wait = read("references/subagent_spawn_wait.md")
    assert "按宿主可用并发容量分批" in wait
    assert "不得冒充独立" in text
    integration = read("references/mcp_integration.md").split("## AI 执行时的工作流", 1)[1]
    assert "查 `~/.claude.json` 的 `mcpServers`" not in integration
    assert "仅在宿主提供发现接口时" in integration
    all_integration = read("references/mcp_integration.md")
    assert "`~/.claude.json` 的 `mcpServers.cheap-research` 段存在" not in all_integration
    assert "`~/.claude.json` 的 `mcpServers.vision-bridge` 段存在" not in all_integration
    assert "配置不可读不能否定" in all_integration
    matrix = read("references/mcp_per_step.md")
    assert "配置不可读不能否定会话已暴露的工具" in matrix
    assert "且 工具可调用（列表直接可见 或 ToolSearch 可取 schema）" not in matrix
    for path in ("references/mcp_per_step.md", "references/anti_laziness.md"):
        assert "不可见才 ToolSearch" not in read(path)
    assert "强证据场景满足（MCP 在**当前宿主**注册" not in read("references/anti_laziness.md")
    anti = read("references/anti_laziness.md")
    assert "质疑者必须用 Agent 工具" not in anti
    assert 'spawn 规格：`subagent_type: "general-purpose"`' not in anti
    assert "含 1 次换 subagent_type" not in anti
    assert "🟢 MCP **必须先实际调用一次**" not in anti


def test_visible_policy_denied_has_explicit_trace_evidence():
    text = read("references/thinking_core.md")
    assert "policy_denied" in text
    assert "policy_ref" in text
    assert "tool_visible=true" in text
    assert "不可见才 ToolSearch 取" not in text


def test_topology_same_path_not_counted_twice():
    text = read("references/worktree_isolation.md")
    assert "规范化真实路径去重" in text
    assert "不同真实路径" in text


def test_unavailable_index_never_hides_a_known_topology_conflict():
    topology = read("references/worktree_isolation.md")
    assert "indexed=false" in topology
    assert "本地可信根" in topology
    assert "已读到冲突记录" in topology
    assert "fail-closed" in topology
    assert "索引同步债务" in topology
    for path in ("steps/01_plan.md", "steps/06_audit.md"):
        text = read(path)
        assert "索引同步债务" in text
        assert "index-write" in text
        assert "受控重试" in text


def test_portable_python_and_open_attempt_recovery():
    full = read("references/full_workflow.md")
    assert "Python 3.10" in full and "ICODE_PYTHON_BIN" in full
    text = read("references/execution_model.md")
    assert "open_execution" in text
    assert "原 attempt" in text


def test_review_summary_precedes_completion_and_missing_rg_does_not_skip_dedup():
    review = read("steps/02_review.md")
    assert review.index("5. **审查输出压缩") < review.index("7. **结束与状态转换")
    assert "整个 §2.5.7 跳过" not in review
    assert "整个 dedup 跳过" not in read("references/mcp_integration.md")


def test_audit_completes_only_after_deviation_and_delivery_decision():
    audit = read("steps/06_audit.md")
    section = audit[audit.index("## 6.2 强制修复"):]
    assert section.index("回写实现偏差备忘") < section.index("transition --dir {ICODE_OUT_DIR} --to completed")
    assert "evidence={affected_repo_roots:[]}" not in audit


def test_fast_single_review_does_not_claim_full_upgrade():
    fast = read("steps/fast.md")
    assert "fast review 一轮无对抗；有效 full 恢复原多轮与对抗" in fast
    assert '`route=full` 时经控制面记录 `risk_profile.effective_mode="full"`' in fast
    assert "显式 `/icode review N` 升级时先登记 full" in fast
    assert "fast 模式不跑 E2E" not in fast
    assert "前端验证" in fast
    assert "mcp_per_step.md" in fast
