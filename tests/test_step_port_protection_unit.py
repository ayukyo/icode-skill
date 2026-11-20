"""受保护步骤输入端口的三个回归：章节归一化、瞬时故障 fail-closed、出生字段补齐。

背景（2026-11-20 端到端实跑暴露）：

1. audit 被合同要求回写 `03_plan_final.md` 的「实现偏差备忘」，而该文件同时是 audit 的
   受保护输入 → 整文件取哈希时，合规回写必然被判成漂移，终审每次都得多跑一轮 blocked 重入。
2. `workspace_git_head` 端口在 `git rev-parse` 瞬时失败时 `continue` 掉，`roots` 变空、
   digest 消失 → 被误报成「工作区漂移」（实测：HEAD 全程未变，却 blocked 一次）。
3. 文档写 `requirement_deltas` 可选且「缺失视为 []」，而完成态 transition 走 `--strict`
   要求显式存在 → 新工单在 plan→plan_done 处被卡。
"""
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('step_port_control', ROOT / 'tools/icode_control.py')
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)

MEMO_HEADING = '## 实现偏差备忘'


def _audit_final_plan_port():
    """取真实 catalog 里 audit 的 final_plan 端口，避免测试与契约各自漂移。"""
    catalog = json.loads((ROOT / 'mcp/workflow-gate/gates.json').read_text(encoding='utf-8'))
    contract = catalog['execution_model']['step_contracts']['audit']
    port = next(item for item in contract['inputs'] if item['id'] == 'final_plan')
    return port


@pytest.fixture
def ticket(tmp_path):
    out = tmp_path / '.icode_output' / '.icode_output_1'
    out.mkdir(parents=True)
    return out, {'project_path': str(tmp_path)}


def test_audit_final_plan_port_declares_the_mutable_section():
    port = _audit_final_plan_port()
    assert port['protected'] is True
    assert port.get('ignore_sections') == [MEMO_HEADING], (
        'audit 的 final_plan 端口必须显式声明可由终审回写的章节，否则合规回写会被判漂移')


def test_appending_the_memo_section_does_not_change_the_protected_digest(ticket):
    out, meta = ticket
    plan = out / '03_plan_final.md'
    plan.write_text('# 定稿计划\n\n正文第一段。\n\n正文第二段。\n\n'
                    + MEMO_HEADING + '\n\n_（待终审回写）_\n', encoding='utf-8', newline='\n')
    before = ctl._resolve_port_legacy(out, meta, _audit_final_plan_port())

    plan.write_text('# 定稿计划\n\n正文第一段。\n\n正文第二段。\n\n'
                    + MEMO_HEADING + '\n\n**无实质偏差。**\n\n| ID | 内容 |\n|---|---|\n| D1 | 细节 |\n',
                    encoding='utf-8', newline='\n')
    after = ctl._resolve_port_legacy(out, meta, _audit_final_plan_port())

    assert before['exists'] and after['exists']
    assert before['sha256'] == after['sha256'], '回写偏差备忘不应触发受保护输入漂移'
    assert after['ignored_sections'] == [MEMO_HEADING]


def test_changing_the_plan_body_still_changes_the_protected_digest(ticket):
    out, meta = ticket
    plan = out / '03_plan_final.md'
    plan.write_text('# 定稿计划\n\n正文。\n\n' + MEMO_HEADING + '\n', encoding='utf-8', newline='\n')
    before = ctl._resolve_port_legacy(out, meta, _audit_final_plan_port())

    plan.write_text('# 定稿计划\n\n正文被改了。\n\n' + MEMO_HEADING + '\n',
                    encoding='utf-8', newline='\n')
    after = ctl._resolve_port_legacy(out, meta, _audit_final_plan_port())

    assert before['sha256'] != after['sha256'], '正文改动必须仍被判为漂移'


def test_plan_without_the_memo_section_hashes_the_whole_file(ticket):
    out, meta = ticket
    plan = out / '03_plan_final.md'
    plan.write_text('# 定稿计划\n\n正文。\n', encoding='utf-8', newline='\n')
    fact = ctl._resolve_port_legacy(out, meta, _audit_final_plan_port())

    # 章节缺失时摘要退化为整段正文（尾部空行归一化）；`ignored_sections` 标记仍保留，
    # 这样“定稿时还没有备忘段、终审才追加”这件事本身也不会被算成漂移。
    assert fact['ignored_sections'] == [MEMO_HEADING]
    assert fact['sha256'] == hashlib.sha256('# 定稿计划\n\n正文。'.encode('utf-8')).hexdigest()


def test_adding_the_memo_section_later_is_not_a_drift(ticket):
    out, meta = ticket
    plan = out / '03_plan_final.md'
    plan.write_text('# 定稿计划\n\n正文。\n', encoding='utf-8', newline='\n')
    before = ctl._resolve_port_legacy(out, meta, _audit_final_plan_port())

    plan.write_text('# 定稿计划\n\n正文。\n\n' + MEMO_HEADING + '\n\n**无实质偏差。**\n',
                    encoding='utf-8', newline='\n')
    after = ctl._resolve_port_legacy(out, meta, _audit_final_plan_port())

    assert before['sha256'] == after['sha256'], '终审新增偏差备忘段不应触发漂移'


def _workspace_head_port():
    catalog = json.loads((ROOT / 'mcp/workflow-gate/gates.json').read_text(encoding='utf-8'))
    contract = catalog['execution_model']['step_contracts']['deepcheck']
    return next(item for item in contract['inputs'] if item['id'] == 'workspace_head')


def test_transient_git_failure_is_fail_closed_not_a_fake_drift(ticket, monkeypatch):
    out, meta = ticket

    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd='git', timeout=10)

    monkeypatch.setattr(ctl.subprocess, 'run', boom)
    with pytest.raises(ctl.ControlError) as excinfo:
        ctl._resolve_port_legacy(out, meta, _workspace_head_port())
    assert excinfo.value.extra.get('gate_id') == 'step_ports_transient'


def test_missing_git_binary_stays_a_stable_absence(ticket, monkeypatch):
    out, meta = ticket

    def no_git(*args, **kwargs):
        raise FileNotFoundError('git not installed')

    monkeypatch.setattr(ctl.subprocess, 'run', no_git)
    fact = ctl._resolve_port_legacy(out, meta, _workspace_head_port())
    assert fact['exists'] is False
    assert 'digest' not in fact


def test_non_git_target_stays_a_stable_absence(ticket, monkeypatch):
    out, meta = ticket

    def not_a_repo(*args, **kwargs):
        return subprocess.CompletedProcess(args=('git',), returncode=128,
                                           stdout='', stderr='fatal: not a git repository')

    monkeypatch.setattr(ctl.subprocess, 'run', not_a_repo)
    fact = ctl._resolve_port_legacy(out, meta, _workspace_head_port())
    assert fact['exists'] is False
    assert 'digest' not in fact


def test_new_birth_seeds_requirement_deltas(tmp_path, capsys):
    out = tmp_path / '.icode_output' / '.icode_output_1'
    args = SimpleNamespace(dir=str(out), ticket_id='DELTA-FIXTURE', requirement='fixture',
                           birth='plan', metadata_json='{}', request_id='birth-delta')
    assert ctl.cmd_create(args) == 0
    capsys.readouterr()
    meta = ctl.load_metadata(out)
    assert meta['requirement_deltas'] == [], '新工单必须自带 requirement_deltas=[]，与 strict 门禁口径一致'


def test_birth_keeps_user_supplied_requirement_deltas(tmp_path, capsys):
    out = tmp_path / '.icode_output' / '.icode_output_1'
    supplied = [{'at': '2026-11-20T00:00:00', 'user_input_summary': '补充约束',
                 'changed_aspect': 'scope', 'classification': 'clarification_only',
                 'impact': 'none', 'user_confirm': True}]
    args = SimpleNamespace(dir=str(out), ticket_id='DELTA-KEEP', requirement='fixture',
                           birth='plan', metadata_json=json.dumps({'requirement_deltas': supplied}),
                           request_id='birth-delta-keep')
    assert ctl.cmd_create(args) == 0
    capsys.readouterr()
    assert ctl.load_metadata(out)['requirement_deltas'] == supplied