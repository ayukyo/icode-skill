#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.." || exit 1

PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf '  PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$1" >&2; }

if python3 tools/icode_agent.py ui --help 2>/dev/null \
  | grep -q -- '--no-browser' \
  && python3 tools/icode_agent.py ui --help 2>/dev/null | grep -q -- '--ticket' \
  && python3 tools/icode_agent.py ui --help 2>/dev/null | grep -q -- '--project' \
  && python3 -S tools/icode_agent.py ui --help >/dev/null 2>&1; then
  ok "UI CLI 可在无第三方 site-packages 时启动帮助"
else
  bad "UI CLI 子命令或 stdlib-only 合同缺失"
fi

if rg -q 'id="project-list"' agent_runtime/icode_agent/ui_assets/index.html \
  && rg -q 'id="ticket-list"' agent_runtime/icode_agent/ui_assets/index.html \
  && rg -q 'id="ticket-search"' agent_runtime/icode_agent/ui_assets/index.html \
  && rg -q 'id="new-ticket-button"' agent_runtime/icode_agent/ui_assets/index.html \
  && rg -q '/api/v1/tickets/create' agent_runtime/icode_agent/ui_assets/app.js; then
  ok "方向 C 提供全局项目与工单导航"
else
  bad "全局项目/工单导航结构缺失"
fi

if rg -q 'id="refresh-button"' agent_runtime/icode_agent/ui_assets/index.html \
  && rg -q 'id="last-refresh"' agent_runtime/icode_agent/ui_assets/index.html \
  && rg -q '/api/v1/refresh' agent_runtime/icode_agent/ui_assets/app.js \
  && rg -q '刷新中' agent_runtime/icode_agent/ui_assets/app.js; then
  ok "手动刷新按钮、时间和一致性刷新 API 已接线"
else
  bad "手动刷新降级能力不完整"
fi

if rg -q 'id="settings-button"' agent_runtime/icode_agent/ui_assets/index.html \
  && rg -q 'id="settings-drawer"' agent_runtime/icode_agent/ui_assets/index.html \
  && rg -q 'id="setting-refresh-interval"' agent_runtime/icode_agent/ui_assets/index.html \
  && rg -q 'id="setting-refresh-interval" type="number" min="5" max="300"' agent_runtime/icode_agent/ui_assets/index.html \
  && rg -q '/api/v1/settings' agent_runtime/icode_agent/ui_assets/app.js \
  && rg -q 'refresh_interval_seconds' agent_runtime/icode_agent/ui_assets/app.js \
  && rg -q 'refresh_interval_seconds.*1000' agent_runtime/icode_agent/ui_assets/app.js; then
  ok "设置入口、30 秒默认自动刷新和持久化 API 可见"
else
  bad "设置 UI 或可配置自动刷新未完整接线"
fi

if rg -q 'reused' tools/icode_agent.py \
  && rg -q 'ui_instance.json' tools/icode_agent.py \
  && rg -q 'instance_id' agent_runtime/icode_agent/ui_server.py; then
  ok "重复 /icode ui 复用同一全局实例"
else
  bad "重复 /icode ui 仍可能启动多个全局实例"
fi

if rg -q 'id="next-step"' agent_runtime/icode_agent/ui_assets/index.html \
  && rg -q 'id="run-step-button"' agent_runtime/icode_agent/ui_assets/index.html \
  && rg -q 'expected_revision' agent_runtime/icode_agent/ui_assets/app.js \
  && rg -q '/api/v1/steps/run' agent_runtime/icode_agent/ui_assets/app.js; then
  ok "推荐步骤通过 revision 保护的 Host API 执行"
else
  bad "直接步骤执行或 revision 防护缺失"
fi

if rg -q 'id="job-list"' agent_runtime/icode_agent/ui_assets/index.html \
  && rg -q '/cancel' agent_runtime/icode_agent/ui_assets/app.js \
  && rg -q 'globalThis.confirm' agent_runtime/icode_agent/ui_assets/app.js \
  && rg -q '/api/v1/job-events' agent_runtime/icode_agent/ui_assets/app.js \
  && rg -q 'outcome_unknown' agent_runtime/icode_agent/host_runner.py; then
  ok "增量任务进度、重启未知态、终态回执与安全取消已接线"
else
  bad "任务管理区域不完整"
fi

if rg -q 'id="ticket-status-filter"' agent_runtime/icode_agent/ui_assets/index.html \
  && rg -q 'id="ticket-sort"' agent_runtime/icode_agent/ui_assets/index.html \
  && rg -q 'STEP_LABELS' agent_runtime/icode_agent/ui_assets/app.js \
  && rg -q '制定实施计划' agent_runtime/icode_agent/ui_assets/app.js; then
  ok "工单筛选、排序与新手动作翻译已接线"
else
  bad "规模化导航或新手动作翻译缺失"
fi

if rg -q 'id="progress-steps"' agent_runtime/icode_agent/ui_assets/index.html \
  && rg -q 'id="artifact-list"' agent_runtime/icode_agent/ui_assets/index.html \
  && rg -q 'id="verification-summary"' agent_runtime/icode_agent/ui_assets/index.html \
  && rg -q '_cockpit_projection' agent_runtime/icode_agent/ui_server.py; then
  ok "控制面投影的工单驾驶舱已接线"
else
  bad "工单驾驶舱投影缺失"
fi

if rg -q 'ui_jobs.json' agent_runtime/icode_agent/ui_server.py \
  && rg -q 'PERSISTED_JOB_FIELDS' agent_runtime/icode_agent/host_runner.py \
  && ! rg -q '"output".*PERSISTED_JOB_FIELDS' agent_runtime/icode_agent/host_runner.py; then
  ok "任务恢复投影不持久化原始输出"
else
  bad "任务恢复投影边界不完整"
fi

if rg -q '@media.*max-width' agent_runtime/icode_agent/ui_assets/style.css \
  && rg -q 'prefers-reduced-motion' agent_runtime/icode_agent/ui_assets/style.css; then
  ok "小屏与减少动画偏好有响应式适配"
else
  bad "响应式或可访问动画降级缺失"
fi

if rg -q '127\.0\.0\.1' agent_runtime/icode_agent/ui_server.py \
  && ! python3 tools/icode_agent.py ui --help 2>/dev/null | grep -q -- '--host'; then
  ok "第一版 UI 固定 loopback 且不暴露远程 bind"
else
  bad "UI 可能被配置为远程监听"
fi

if [ -s agent_runtime/icode_agent/ui_assets/index.html ] \
  && [ -s agent_runtime/icode_agent/ui_assets/app.js ] \
  && [ -s agent_runtime/icode_agent/ui_assets/style.css ] \
  && ! rg -q 'https?://|<script[^>]*>[^<]' agent_runtime/icode_agent/ui_assets; then
  ok "UI 静态资源随仓且无外链/内联脚本"
else
  bad "UI 静态资源不完整或含外部依赖"
fi

if ! rg -q 'innerHTML|document\.write|eval\(' \
    agent_runtime/icode_agent/ui_assets/app.js \
  && rg -q 'textContent' agent_runtime/icode_agent/ui_assets/app.js; then
  ok "动态内容只走 textContent，未引入直接 HTML 注入"
else
  bad "UI DOM 更新存在注入风险"
fi

if rg -q 'X-ICODE-UI-Token' agent_runtime/icode_agent/ui_server.py \
  && rg -q 'MAX_REQUEST_BYTES' agent_runtime/icode_agent/ui_server.py \
  && rg -q 'Content-Security-Policy' agent_runtime/icode_agent/ui_server.py; then
  ok "UI token、请求上限和浏览器安全头已落地"
else
  bad "UI HTTP 安全合同缺失"
fi

if ! rg -qi 'name="(api[_-]?key|token)"|id="(api[_-]?key|token)"|OPENAI_API_KEY' \
      agent_runtime/icode_agent/ui_assets \
  && rg -q '不.*API Key|API Key.*不' agent_runtime/README.md; then
  ok "UI 不采集 API Key"
else
  bad "UI 暴露凭据输入或文档缺边界"
fi

if python3 tools/icode_agent.py status --help >/dev/null \
  && python3 tools/icode_agent.py run --help >/dev/null; then
  ok "既有 status/run CLI 保持可用"
else
  bad "UI 改动破坏既有 Agent CLI"
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
