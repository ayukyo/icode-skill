<div align="center">

# ICode — End-to-End Coding Workflow for Claude Code

**6-step workflow: Plan → Review → Finalize → Code → Deep Check → Audit.** Run all at once, or step-by-step and switch models between steps.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-Skill-8A2BE2.svg)](SKILL.md)
[![Version](https://img.shields.io/badge/version-v2.18.0-blue.svg)](SKILL.md)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/ayukyo/icode-skill/issues)

</div>

ICode is a Claude Code Skill that breaks the journey from requirement to delivery into strict, individually invokable steps. It adds a **quality gate, adversarial review, cross-project memory, and crash recovery** on top of vanilla Claude Code — without locking you into a single model or a single pass.

## Why ICode?

| Concern | Vanilla Claude Code | ICode |
|---|---|---|
| Process discipline | Depends on your prompt | Hard 6-step gates + L1–L4 blocking matrix |
| Review quality | Single-perspective self-review | Independent skeptic sub-agents with adversarial verification (self-delegation forbidden) |
| Laziness resistance | None | 33 hard anti-laziness rules + mandatory Read confirmation lines + file:line evidence |
| Reusing past decisions | Every ticket starts cold | Cross-project history retrieval with a global index + **verdict-based anti-misleading injection** (disproved tickets inject the trap, not the ADR) |
| Project knowledge | None | `/icode doc` generates a global per-project/branch knowledge base, auto-injected at phase zero |
| Crash recovery | Restart from scratch | `.ico_metadata.json` status + round counters enable resumable runs at any step |
| Cost control | Everything on the main model | `cheap-research` offloads 23 low-risk sub-tasks to cheap models; `/icode fast` ≈ 65% of full-flow cost |
| Model freedom | Manual | Every step is a separate command, so you can switch models between steps |

## Quick Start

```bash
# 1) Install into your skills directory (needs one-time setup)
git clone https://github.com/ayukyo/icode-skill ~/.claude/skills/icode

# 2) Install the 6 MCP servers the workflow relies on (self-checking, fills in what's missing)
/icode install

# 3) Run a full flow
/icode start Implement MCU rain sensor I2C driver
```

Or run step by step (switch models between steps anytime):

```bash
/icode plan Implement MCU rain sensor I2C driver   # Step 1: Draft plan
/icode review                                       # Step 2: Review plan (soft cap 3 rounds; auto-extends if issues remain)
/icode merge                                        # Step 3: Merge & finalize
/icode code                                         # Step 4: Code implementation
/icode deepcheck                                    # Step 5: Iterative re-review
/icode audit                                        # Step 6: Final audit & fix
```

Other entry points:

```bash
# Trimmed full flow (fast mode: single-file/small changes; ~65% of full-flow cost)
/icode fast "Add isqrt function to calc.c"          # plan→review(1 round, no adversarial)→merge→code→deepcheck(Reverse only)→audit

# Requirement unclear? Draft it in conversation first
/icode init Record sensor data re-bag                # Step 0: kick-off draft + dialogue

# From a bug log: analyze root cause first, then fix
/icode log ~/work/log/service-anomaly "no response after startup"   # Entry: log root-cause analysis → fix requirement

# Project-level knowledge base (standalone step, runs anytime)
/icode doc myproject                                 # Generate/update this project's knowledge base chapters

# Backup all work orders before deleting a project (safety net — run BEFORE deleting)
/icode bak                                           # Snapshot current project's entire .icode_output/ to ~/.claude/icode_data/project_backup/
/icode bak --project ~/work/myproj                   # Backup a specified project
# Repeatable: each run creates a new timestamp snapshot (rsync --link-dest hardlink dedup).
# After the project is deleted, history retrieval still finds full work orders from the backup (project-first, backup fallback).

# Opt-in worktree isolation (any of the entry points above)
/icode start --worktree Implement MCU rain sensor I2C driver   # Full flow + isolated branch/dir
/icode fast --worktree  "Add isqrt function to calc.c"         # Fast mode + isolated branch/dir
/icode plan --worktree  Implement MCU rain sensor I2C driver    # Step 1 only + isolated branch/dir
/icode init --worktree  Record sensor data re-bag              # Step 0 + isolated branch/dir
/icode log --worktree   ~/work/log/service-anomaly "no response"  # Log + isolated branch/dir
# Without --worktree, tickets are created in-place by default — no prompt.
# Triggers also accept natural language ("use worktree isolation"), with reverse declarations taking precedence; AI shows a status line on entry. Projects can block via `/icode limit worktree 强制禁止`.
# New worktrees are created on the current remote-tracking baseline (@{u}) with upstream tracking, so git status/pull/merge stay correct.
# Worktree lifecycle (after creation):
/icode worktree --update    # Move the active implementation to a new checkout on the latest remote baseline (tracked upstream)
/icode worktree --close     # After you committed/pushed/merged: verify online evidence + safe cleanup + record baseline
/icode worktree --reopen    # Restore a closed ticket's active checkout on the latest baseline (then /icode patch)
```

## The Workflow

```text
  /icode log (optional)        /icode init (optional)
  log root-cause analysis ─┐    requirement draft ─┐
                           └──────────┬────────────┘
                                      ▼
  ┌────────┐   ┌────────┐   ┌────────┐   ┌────────┐   ┌────────┐   ┌────────┐
  │ Plan   │ → │ Review │ → │ Merge  │ → │ Code   │ → │ Deep   │ → │ Audit  │
  │ Step 1 │   │ Step 2 │   │ Step 3 │   │ Step 4 │   │ Check  │   │ Step 6 │
  └────────┘   └────────┘   └────────┘   └────────┘   │ Step 5 │   └────────┘
                                                       └────────┘
       /icode start = steps 1→6 chained  |  /icode fast = trimmed chain (~65% cost)
```

Every step produces a real artifact in `.icode_output/.icode_output_N/` (plan → review → final plan → code → deep-check report → audit report), tracked by `.ico_metadata.json` for cross-session recovery and resumable runs.

## Features

- **Closed-loop delivery**: (optional) Requirement Draft → Plan → Review → Finalize → Code → Deep Check → Audit; each step callable independently, runs in the main session without model switching
- **Dual modes**: `/icode start` full flow (multi-round review + adversarial verification) / `/icode fast` trimmed (1 round, no adversarial, ~65% cost)
- **Anti-laziness quality gates**: triple-phase deepcheck (Reverse/Fixed/Free), plan assertion verification, ADR decision records, adversarial verification (independent skeptics — insufficient evidence is never confirmed, honest downgrade over fake consensus)
- **Cross-project history retrieval**: init/log/plan/start auto-search similar past tickets and inject by command; references stay in-session, never pollute project artifacts. **Verdict-based injection** prevents disproved/superseded tickets from misleading new work
- **Project-level knowledge base** (`/icode doc`): global per-project/per-branch knowledge base (module docs generated once and reused across projects), auto-retrieved and injected by phase-zero search
- **Project work-order backup** (`/icode bak`): snapshot the project's entire `.icode_output/` (tickets + debug twins + limit.local + ppt) to global `~/.claude/icode_data/project_backup/`, repeatable with hardlink dedup. Run it before deleting a project — once deleted, history retrieval still reads full work orders from the backup (project-first, backup fallback), and `/icode list` marks them `[path_gone→backup]`. For closed worktree tickets whose `project_path` is gone but whose `archive_path` is valid, `/icode list` marks them `[path_gone→archive]` (or `[path_gone→archive+backup]` when both archive and backup exist)
- **Anti-duplicate injection**: history retrieval and project-doc retrieval share an injection cache, avoiding repeated injection within one dev chain
- **Decision anchors**: steps pass concise decision summaries (`.decision_anchors.json`) downstream — saves tokens, keeps reasoning continuity
- **Resumable runs**: `.ico_metadata.json` status + round counters support crash recovery at steps 2/4/5
- **Two optional entries**: `/icode log` log root-cause analysis (baseline check first, then adversarial analysis; domain-agnostic) → fix requirement, plus a cross-audience brief at completion — `log_problem_brief.md`, with a `<ticket>` prefix (`DEMO-26_log_problem_brief.md`) when the source is a TB ticket (no icode terminology); `/icode init` multi-turn requirement draft → `00_init.md`
- **Project constraint red lines** (`/icode limit`): define this project's forbidden zones / constraints; the plan step treats them as a hard baseline (plan → implementation → audit convergence)

## Installation

Clone this repository into your Claude Code skills directory:

```bash
git clone https://github.com/ayukyo/icode-skill ~/.claude/skills/icode
```

Then run the MCP environment check + one-click install (scans `mcp/*/install.sh`, self-checks venv/Node/npm per sub-project, fills in what's missing, registers to Claude Code (`~/.claude.json`; add `--client all` to also register to Codex via `codex mcp add`, default `--client claude` never touches Codex):

```bash
/icode install
/icode install --client all   # also register to Codex (default: claude only)
```

New clone / new machine / CI bootstrap → run once. The workflow degrades gracefully if optional MCPs aren't installed (declared downgrade paths, never blocks).

## Optional Data Source: Pull from DingTalk Docs

When an entry command (`/icode init` / `log` / `plan` / `start`) or the `patch` step (phase 0, insertable anytime) receives a DingTalk share link (`alidocs.dingtalk.com` / `/i/nodes/{token}`), it can auto-pull the doc/drive files into `dingtalk_source/` as requirement/reference input. Pull-only, never writes back to DingTalk; native-format docs (`.axls`/`.doci`) require the user to export first in the DingTalk UI. Prerequisites: logged into DingTalk docs in Chrome + `pip install browser_cookie3`. See [SKILL.md「方式 H」](SKILL.md) and `~/.claude/skills/icode/tools/dingtalk/README.md`.

## Optional Data Source: Pull from Teambition Bug Tickets

When `/icode log` receives a Teambition project URL or a `<LIB>-<NUM>` ticket ref (e.g. `DEMO-26`) in its scattered input, it can optionally pull the ticket's title / description / comments / log attachments into `tb_source/<ID>/` as analysis input — replacing local logs when the ticket carries attachments. Pull-only for analysis, never writes back to Teambition; with no TB reference, `/icode log` falls back to the pure local-log path (behavior unchanged). Config (optional; multi-project shortcuts + cookie) and the cookie helper: see `~/.claude/skills/icode/tools/tb/README.md` and `tools/tb/scripts/tb_cookie.py --domain <domain>`. Re-running the same ticket (new comments/attachments on TB) prompts "reuse old ticket / create new" — reuse re-pulls the latest and re-runs incremental adversarial analysis. A batch mode also analyzes every "open / unfinished" ticket in a project (triggered by an "analyze all TB" intent; filtered by the real taskflow status name, not `isDone`) — see [SKILL.md「方式 D2 / D3」](SKILL.md).

**Scheduled incremental monitoring** (`tools/tb/scripts/tb_watch.py`): periodically polls one or more projects' "open / unfinished" tickets (newest ticket number first), and when a ticket gains new comments/attachments/status changes it auto-launches a headless `claude` session for **full `/icode log --debug` deep analysis** (downloads & extracts TB log attachments; artifacts under `{project}/.icode_output/.debug/`, never touching the global index; first-run tickets are auto-baselined one per round). Config is a JSON file listing projects — the minimal entry is just the project URL; every round it writes a searchable "latest analysis status" report to `{project}/.icode_output/tb_watch_report.md`, and refreshes it immediately after each triggered analysis completes. Config / start / stop / risks: see [tools/tb/README.md](tools/tb/README.md)「定时增量监控」section.

## Optional Enhancements

Both are platform-agnostic: point them at any OpenAI Chat Completions-compatible endpoint (OpenAI / Claude / Gemini / Chinese vendors / self-hosted / OpenRouter / local Ollama). Not installing either doesn't affect the main workflow.

### Image/Video Understanding (vision-bridge)

```bash
cd ~/.claude/skills/icode/mcp/vision-bridge
./install.sh                          # auto: venv + install deps + register to ~/.claude.json
# edit the generated config.json with your base_url / api_key / model
# restart Claude Code to take effect
```

All image/video handling flows through either the `mcp__vision-bridge__analyze_media` MCP tool or the local CLI channel (`<server.py 目录>/.venv/bin/python <server.py> --analyze-media <path>`) for clients that don't inject MCP tools (e.g. codex). Videos are pre-sampled locally with ffmpeg to save API quota. When both channels are unavailable, session models fall back to native capability — no error, no blocking. Image/video is never injected into session model messages.

### Cheap LLM Inference (cheap-research)

```bash
cd ~/.claude/skills/icode/mcp/cheap-research
./install.sh                          # auto: venv + install deps + register to ~/.claude.json
# edit the generated config.json with your base_url / api_key / model
# restart Claude Code to take effect
```

Offloads 23 low-risk sub-tasks (long-context compression / history retrieval / template filling / structured extraction / TB-comment pre-extraction / code-fact audit / pattern scanning / symbol tracing / diff summaries) to a cheap model. It **never takes over decisions** — 3-skeptic adversarial verification, architecture decisions, final audit, and fix proposals stay on the main session (zero gray area).

### The Other 4 MCPs (sequential-thinking / memory / context7 / playwright)

The remaining 4 of the 6 MCPs are workflow utilities, installed by `/icode install` and used by the steps — no per-user config needed:

- **sequential-thinking** — mandatory structured-thinking gate before plan mode / complex / refactor tasks (each step lists its required MCPs first, then calls them)
- **memory** — cross-project knowledge graph (`mcp__memory__read_graph`), recalled during search/injection so past tickets and project docs resurface across sessions
- **context7** — live library-doc lookup during init/plan/code when the requirement touches third-party libraries
- **playwright** — browser automation during deepcheck/audit for front-end projects

Each MCP has an explicit strong-evidence trigger and a declared graceful-downgrade path (see [SKILL.md「MCP 工具集」](SKILL.md)); none blocks the workflow when missing.

## Commands

| Command | Description |
| --- | --- |
| `/icode help` | Help: show usage examples |
| `/icode log [scattered info...]` | Optional entry: log root-cause analysis → fix requirement `00_init.md` (domain-agnostic); auto-generates cross-audience brief at completion (`log_problem_brief.md`, `<ticket>_log_problem_brief.md` for TB sources; external wording follows the shared brief contract — attribution grading, role clarification, explicit fix/verify state) |
| `/icode init [<rough req>]` | Optional Step 0: multi-turn dialogue → `00_init.md` |
| `/icode start <req>` | Full flow: create/reuse dir → steps 1–6 |
| `/icode fast <req>` | Trimmed full flow: plan→review(1 round, no adversarial)→merge→code→deepcheck(Reverse only)→audit (~65% cost) |
| `/icode plan <req>` | Step 1 only: draft project plan |
| `/icode review [N]` | Step 2 only: review the plan (N=soft cap rounds, default 3) |
| `/icode merge` | Step 3 only: merge reviews & finalize |
| `/icode code` | Step 4 only: implement code |
| `/icode deepcheck` | Step 5 only: three-phase progressive check (Reverse → Fixed → Free) |
| `/icode audit` | Step 6 only: final audit + fix (produces `06_audit.md`) |
| `/icode readme` | Optional Step 7: one call, two docs — full report (for yourself) + `_brief.md` (concise, for other modules' dev/test/PM, key changed code included) |
| `/icode patch [issue or new need]` | Follow-up modification (standalone step): keep modifying an existing ticket after/between main steps — test findings / new needs. Lightweight 4-phase (re-survey → incremental plan → minimal change → reverse re-check), context reloaded from disk artifacts (continuable across sessions), appended to `08_patch.md`; optional `--listen` (auto-monitor) / `--test` (explicit trigger verify) → on-device deploy verify (deploy + poll LOG + incremental analyze); configure `~/.claude/icode_data/device_config/<project_id>.json` (template `templates/device_config.json.template`, single-file multi-conn adb/ssh/serial) |
| `/icode doc [natural language]` | Project-level knowledge base (standalone step), auto-injected at phase zero |
| `/icode limit [natural language]` | Project constraint red lines (standalone step); hard baseline for the plan step |
| `/icode ppt [natural language]` | PPT generation (standalone deliverable step): natural language → real `.pptx` for **project / module / current feature dev / current bug fix**; content sourced from icode artifacts & knowledge base (no fabrication), 16 built-in templates (`tools/ppt/templates/`, the AI shortlists 2-3 style-matched candidates and the user picks; user may also name a template directly), editable `edits.json` for re-run; outputs to `<project_root>/.icode_output/ppt/` (outside any ticket dir); needs `pip install python-pptx` (LibreOffice+poppler optional for PNG preview). Built-in templates are **non-commercial** (see `tools/ppt/NOTICE`) |
| `/icode status` | Read-only: query current ticket status (+ `--verdict` annotation) |
| `/icode list [keywords]` | Cross-project ticket search (pure read-only) |
| `/icode worktree --update [--to-ref <ref>]` | Worktree lifecycle (standalone): controlled migration of the active implementation checkout to a new one on the latest/specified baseline — 11-phase state machine, failure keeps the old active root, interrupt-recoverable & idempotent. Switching baselines must go through this command (no silent pointer changes); multi-subrepo handled as one transaction |
| `/icode worktree --close` | Worktree lifecycle (standalone): after you've committed/pushed/merged — verify online evidence → mark submitted → safely clean up checkouts → record `submitted_baseline`. Never commits/pushes for you; never deletes unique uncommitted code or unarchived artifacts; idempotent |
| `/icode worktree --reopen [--to-ref <ref>]` | Worktree lifecycle (standalone): explicit restore for a closed `completed` ticket — create a new active checkout on the latest online baseline (no new ticket, patch history kept). Closed tickets must reopen before `patch` |

> Full command details (incl. "Creates Dir?" column + reuse rules + `--verdict`/`--scan-verdict` flags): see the [SKILL.md](SKILL.md) `Commands` section.

## Execution / Directory Structure / Workflow

Execution (main session + no automatic model switching) + Directory Structure (`.icode_output_N/` output layout) + Workflow (Steps 1→6 data-flow + fast-mode branch + reuse rules): see the [SKILL.md](SKILL.md) `General Rules` section.

## Demo

`demo/` is a minimal C calculator project (`calc.h` / `calc.c` / `main.c` / `Makefile`), **purpose-built for end-to-end testing of the icode workflow** — all five invocation modes (A full-flow / B step-by-step / C init→start / D log→start / E fast trimmed) can be run against it.

```bash
cd demo && make && ./calc_demo   # confirm the baseline builds and runs
```

Example test requirements:

- **Mode A**: `cd demo && /icode start add modulo and power operations to the calculator, plus integer overflow checks`
- **Mode B (step-by-step)**: `cd demo && /icode plan add isqrt to calc.c` then `/icode review` `/icode merge` `/icode code` `/icode deepcheck` `/icode audit`
- **Mode C (init then start)**: `cd demo && /icode init add new feature to calculator` (multi-turn dialogue to clarify) → `/icode start`
- **Mode D (log then start)**: `cd demo && /icode log <log_path> "symptom"` → outputs root cause + fix requirement → `/icode start`
- **Mode E (fast trimmed)**: `cd demo && /icode fast add isqrt to calc.c` (~65% time cost)

## License

MIT — see [LICENSE](LICENSE).

---

[中文文档](README.zh-CN.md)
