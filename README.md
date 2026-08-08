<div align="center">

# ICode — End-to-End Coding Workflow for Claude Code

**6-step workflow: Plan → Review → Finalize → Code → Deep Check → Audit.** Run all at once, or step-by-step and switch models between steps.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-Skill-8A2BE2.svg)](SKILL.md)
[![Version](https://img.shields.io/badge/version-v2.11.0-blue.svg)](SKILL.md)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/ayukyo/icode-skill/issues)

</div>

ICode is a Claude Code Skill that breaks the journey from requirement to delivery into strict, individually invokable steps. It adds a **quality gate, adversarial review, cross-project memory, and crash recovery** on top of vanilla Claude Code — without locking you into a single model or a single pass.

## Why ICode?

| Concern | Vanilla Claude Code | ICode |
|---|---|---|
| Process discipline | Depends on your prompt | Hard 6-step gates + L1–L4 blocking matrix |
| Review quality | Single-perspective self-review | Independent skeptic sub-agents with adversarial verification (self-delegation forbidden) |
| Laziness resistance | None | 27 hard anti-laziness rules + mandatory Read confirmation lines + file:line evidence |
| Reusing past decisions | Every ticket starts cold | Cross-project history retrieval with a global index + **verdict-based anti-misleading injection** (disproved tickets inject the trap, not the ADR) |
| Project knowledge | None | `/icode doc` generates a global per-project/branch knowledge base, auto-injected at phase zero |
| Crash recovery | Restart from scratch | `.ico_metadata.json` status + round counters enable resumable runs at any step |
| Cost control | Everything on the main model | `cheap-research` offloads 23 low-risk sub-tasks to cheap models; `/icode fast` ≈ 65% of full-flow cost |
| Model freedom | Manual | Every step is a separate command, so you can switch models between steps |

## Quick Start

```bash
# 1) Install into your skills directory (needs one-time setup)
git clone https://github.com/ayukyo/icode-skill ~/.claude/skills/icode

# 2) Install the 7 MCP servers the workflow relies on (self-checking, fills in what's missing)
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
- **Anti-duplicate injection**: history retrieval and project-doc retrieval share an injection cache, avoiding repeated injection within one dev chain
- **Decision anchors** (v2.8): steps pass concise decision summaries (`.decision_anchors.json`) downstream — saves tokens, keeps reasoning continuity
- **Resumable runs**: `.ico_metadata.json` status + round counters support crash recovery at steps 2/4/5
- **Two optional entries**: `/icode log` log root-cause analysis (baseline check first, then adversarial analysis; domain-agnostic) → fix requirement; `/icode init` multi-turn requirement draft → `00_init.md`
- **Project constraint red lines** (`/icode limit`): define this project's forbidden zones / constraints; the plan step treats them as a hard baseline (plan → implementation → audit convergence)

## Installation

Clone this repository into your Claude Code skills directory:

```bash
git clone https://github.com/ayukyo/icode-skill ~/.claude/skills/icode
```

Then run the MCP environment check + one-click install (scans `mcp/*/install.sh`, self-checks venv/Node/npm per sub-project, fills in what's missing, registers to `~/.claude.json`):

```bash
/icode install
```

New clone / new machine / CI bootstrap → run once. The workflow degrades gracefully if optional MCPs aren't installed (declared downgrade paths, never blocks).

## Optional Enhancements

Both are platform-agnostic: point them at any OpenAI Chat Completions-compatible endpoint (OpenAI / Claude / Gemini / Chinese vendors / self-hosted / OpenRouter / local Ollama). Not installing either doesn't affect the main workflow.

### Image/Video Understanding (vision-bridge)

```bash
cd ~/.claude/skills/icode/mcp/vision-bridge
./install.sh                          # auto: venv + install deps + register to ~/.claude.json
# edit the generated config.json with your base_url / api_key / model
# restart Claude Code to take effect
```

All image/video handling flows through `mcp__vision-bridge__analyze_media`. Videos are pre-sampled locally with ffmpeg to save API quota. When unconfigured, it returns a fallback hint and session models fall back to native capability — no error, no blocking.

### Cheap LLM Inference (cheap-research)

```bash
cd ~/.claude/skills/icode/mcp/cheap-research
./install.sh                          # auto: venv + install deps + register to ~/.claude.json
# edit the generated config.json with your base_url / api_key / model
# restart Claude Code to take effect
```

Offloads 23 low-risk sub-tasks (long-context compression / history retrieval / template filling / structured extraction / TB-comment pre-extraction / code-fact audit / pattern scanning / symbol tracing / diff summaries) to a cheap model. It **never takes over decisions** — 3-skeptic adversarial verification, architecture decisions, final audit, and fix proposals stay on the main session (zero gray area).

## Commands

| Command | Description |
| --- | --- |
| `/icode help` | Help: show usage examples |
| `/icode log [scattered info...]` | Optional entry: log root-cause analysis → fix requirement `00_init.md` (domain-agnostic) |
| `/icode init [<rough req>]` | Optional Step 0: multi-turn dialogue → `00_init.md` |
| `/icode start <req>` | Full flow: create/reuse dir → steps 1–6 |
| `/icode fast <req>` | Trimmed full flow: plan→review(1 round, no adversarial)→merge→code→deepcheck(Reverse only)→audit (~65% cost) |
| `/icode plan <req>` | Step 1 only: draft project plan |
| `/icode review [N]` | Step 2 only: review the plan (N=soft cap rounds, default 3) |
| `/icode merge` | Step 3 only: merge reviews & finalize |
| `/icode code` | Step 4 only: implement code |
| `/icode deepcheck` | Step 5 only: three-phase progressive check (Reverse → Fixed → Free) |
| `/icode audit` | Step 6 only: final audit + fix (produces `06_audit.md`) |
| `/icode readme` | Optional Step 7: generate delivery report |
| `/icode patch [issue or new need]` | Follow-up modification (standalone step): keep modifying an existing ticket after/between main steps — test findings / new needs. Lightweight 4-phase (re-survey → incremental plan → minimal change → reverse re-check), context reloaded from disk artifacts (continuable across sessions), appended to `08_patch.md`; optional `--listen` → on-device deploy verify (deploy + poll LOG + incremental analyze); configure `~/.claude/icode_data/device_config/<project_id>.json` (template `templates/device_config.json.template`, single-file multi-conn adb/ssh/serial) |
| `/icode doc [natural language]` | Project-level knowledge base (standalone step), auto-injected at phase zero |
| `/icode limit [natural language]` | Project constraint red lines (standalone step); hard baseline for the plan step |
| `/icode status` | Read-only: query current ticket status (+ `--verdict` annotation) |
| `/icode list [keywords]` | Cross-project ticket search (pure read-only) |

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
