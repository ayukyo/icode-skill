# ICode — End-to-End Coding Workflow (Step 0 + 1~6, with log root-cause analysis entry + project-level knowledge base)

ICode is a Claude Code Skill that breaks down the journey from requirement to delivery into strict steps. Each step can be invoked independently, allowing you to switch models between steps.

- **Entry commands (optional)**: `/icode log` log root-cause analysis (domain-agnostic) → fix requirement; `/icode init` requirement-draft conversation
- **Step 0 (optional)**: Iterative requirement-draft conversation, output as `00_init.md`
- **Steps 1~6**: Plan → Review → Finalize → Code → Deep Check → Audit

## Features

- **Closed-loop delivery**: (optional) Requirement Draft → Plan → Review → Finalize → Code → Deep Check → Audit; each step callable independently, runs in the main session without model switching
- **Dual modes**: `/icode start` full flow (multi-round review + adversarial verification) / `/icode fast` trimmed (1 round, no adversarial, ~65% cost); auto-chains steps 1→6
- **Anti-laziness quality gates**: triple-phase deepcheck (Reverse/Fixed/Free), plan assertion verification, ADR decision records, adversarial verification (independent skeptics — insufficient evidence is never confirmed, honest downgrade over fake consensus)
- **Cross-project history retrieval**: init/log/plan/start auto-search similar past tickets and inject by command; references stay in-session, never pollute project artifacts
- **Project-level knowledge base** (`/icode doc`): generates a global project knowledge base (cross-repo/cross-branch shared, module docs generated once and reused), auto-retrieved and injected by phase-zero search — no need to manually tell it which docs to reference
- **Anti-duplicate injection**: history retrieval and project-doc retrieval share a cache for de-duplication, avoiding repeated injection within one dev chain
- **Anti-laziness hardening**: Steps 5/6 enforce a Read confirmation line + file:line evidence + a self-check checklist; Step 2 adversarial enforces Agent ID
- **Two optional entries**: `/icode log` log root-cause analysis (baseline check first, then adversarial analysis; domain-agnostic) → fix requirement; `/icode init` multi-turn requirement draft → `00_init.md`
- **Outputs & state management**: unified under `.icode_output/.icode_output_N/`, `.ico_metadata.json` tracks status/code files, supports cross-session recovery and resumable runs
- **Optional Teambition defect source**: when `/icode log` scattered input contains a Teambition project URL or a `<LIB>-<NUM>`, it can optionally pull the defect's title/description/comments/log attachments as analysis input (multi-project text config; pull & analyze only, never writes back to TB; falls back to the local-log path when no TB reference)
- **Optional visual understanding** (`mcp/vision-bridge`): install-or-skip image/video understanding MCP — **platform-agnostic**, any OpenAI Chat Completions-compatible endpoint works (OpenAI / Claude / Gemini / Chinese vendors / self-hosted / OpenRouter, all supported). When installed, the SKILL workflow routes all image/video handling through `mcp__vision-bridge__analyze_media`; when not installed, session models fall back to their native capabilities and the user bears responsibility for outcomes. See [mcp/vision-bridge/README.md](mcp/vision-bridge/README.md) and the `Optional Enhancement` section of [SKILL.md](SKILL.md)

## Installation

Clone this repository into your Claude Code skills directory:

```bash
git clone <repo-url> ~/.claude/skills/icode
```

## Optional Enhancement: Image/Video Understanding

Visual understanding is optional — **not installing doesn't affect the main workflow**. When installed, all image/video handling flows through `mcp__vision-bridge__analyze_media`, keeping raw media out of session models.

### Install vision-bridge

```bash
cd ~/.claude/skills/icode/mcp/vision-bridge
./install.sh                          # auto: venv + install deps + register to ~/.claude.json
# edit the generated config.json with your base_url / api_key / model
# restart Claude Code to take effect
```

### Platform-agnostic

Any OpenAI Chat Completions-compatible endpoint works — fill in your platform's base_url and model, **no recommended defaults**.

### What if unconfigured?

If vision-bridge is installed but `config.json` doesn't have the three required fields (`base_url` / `api_key` / `model`), the `analyze_media` tool returns a fallback hint string and the session model processes the original image via its native multimodal capability — **behaves the same as when vision-bridge isn't installed**. No error, no blocking.

See [mcp/vision-bridge/README.md](mcp/vision-bridge/README.md).

## Optional Enhancement: Cheap LLM Inference (cheap-research)

To reduce the main session's token consumption, cheap-research offloads long-context compression, history retrieval, template filling, and structured extraction sub-tasks to a cheap model (via `mcp__cheap-research__*` tools). It **does not take over decisions** — 3-skeptic adversarial verification, architecture decisions, final audit, and fix proposals never go through cheap-research.

**Inclusion gate** (single-threshold): value ≥ 3 ★ + low risk = 22 sub-tasks; covers log / doc / readme / init / plan / review / start / fast and other entry points.

### Install cheap-research

```bash
cd ~/.claude/skills/icode/mcp/cheap-research
./install.sh                          # auto: venv + install deps + register to ~/.claude.json
# edit the generated config.json with your base_url / api_key / model
# restart Claude Code to take effect
```

### Platform-agnostic, like vision-bridge

Any OpenAI Chat Completions-compatible endpoint works — fill in your platform's base_url and model, **no recommended defaults**. Local Ollama is also a valid provider (`provider: local_ollama`).

### Fallback when unconfigured

If cheap-research is installed but `config.json` is missing the required fields, the tool returns a fallback hint dict and the session model handles the task — **behaves the same as when cheap-research isn't installed**. No error, no blocking.

See [mcp/cheap-research/README.md](mcp/cheap-research/README.md).

## Quick Start

```bash
# One-shot full flow (uses current session model; switch with /model if needed)
/icode start Implement MCU rain sensor I2C driver

# Or step by step
/icode plan Implement MCU rain sensor I2C driver   # Step 1: Draft plan
/icode review                                       # Step 2: Review plan (soft cap 3 rounds; auto-extends if issues remain)
/icode review 5                                     # Step 2: 5-round review
/icode merge                                        # Step 3: Merge & finalize
/icode code                                         # Step 4: Code implementation
/icode deepcheck                                    # Step 5: Iterative re-review
/icode audit                                        # Step 6: Final audit & fix

# Trimmed full flow (fast mode: single-file/small changes; ~65% of full-flow cost)
/icode fast "Add isqrt function to calc.c"          # plan→review(1 round, no adversarial)→merge→code→deepcheck(Reverse only)→audit

# Project-level knowledge base (standalone step, runs anytime, not part of steps 1–6)
/icode doc                              # No args → scan all project knowledge bases for staleness
/icode doc myproject                    # Check this project for updates (incremental-first: only regen chapters hit by git diff)
/icode doc regenerate myproject         # Full regen (triggers confirmation gate, protects manual edits)
# After generation, later /icode init|log|plan|start|fast auto-retrieves and injects relevant chapters

# When the requirement is unclear: discuss first, then enter the flow
/icode init Record sensor data re-bag    # Step 0: kick-off draft + dialogue
# ... multi-turn discussion; 00_init.md is updated incrementally each round ...
/icode start                                          # No args → detects init entry state, asks "reuse/new"; reuse takes 00_init.md as input → steps 1–6

# From a bug log: analyze root cause first, then fix
/icode log ~/work/log/service-anomaly "no response after startup"   # Entry: analyze log root cause, outputs log_analysis.md + fix requirement 00_init.md
# ... after adversarial analysis converges; if you doubt it, keep talking to re-run the disputed branch ...
/icode start                                          # No args → detects log_done entry state, asks "reuse/new"; reuse takes 00_init.md (fix requirement) as input → steps 1–6
```

## Optional: pull a Teambition defect's logs for analysis

When `/icode log` scattered input contains a Teambition project URL or a `<LIB>-<NUM>` (e.g. `DEMO-26`), it can optionally auto-pull the defect's title/description/comments/log attachments as analysis input; see the `Usage Examples · Mode D2` section of [SKILL.md](SKILL.md). Multi-project config and cookie setup details: see `~/.claude/skills/icode/tools/tb/README.md`.

## Commands

| Command | Description |
| --- | --- |
| `/icode help` | Help: show usage examples |
| `/icode log [scattered info...]` | Optional entry: log root-cause analysis → fix requirement `00_init.md` (domain-agnostic, always fresh) |
| `/icode init [<rough req>]` | Optional Step 0: multi-turn dialogue → `00_init.md` (always creates a fresh directory) |
| `/icode start <req>` | Full flow: create/reuse dir → steps 1–6 |
| `/icode fast <req>` | Trimmed full flow: plan→review(1 round, no adversarial)→merge→code→deepcheck(Reverse only)→audit (~65% of full-flow cost) |
| `/icode plan <req>` | Step 1 only: draft project plan |
| `/icode review [N]` | Step 2 only: review the plan (N=soft cap rounds, default 3) |
| `/icode merge` | Step 3 only: merge reviews & finalize |
| `/icode code` | Step 4 only: implement code |
| `/icode deepcheck` | Step 5 only: three-phase progressive check (Reverse → Fixed → Free; fast mode runs Reverse only) |
| `/icode audit` | Step 6 only: final audit + fix (produces `06_audit.md`) |
| `/icode readme` | Optional Step 7: generate delivery report (self-contained summary, dynamic filename) |
| `/icode doc [natural language]` | Project-level knowledge base (standalone step): scans code features to generate global knowledge-base chapters; auto-retrieved/injected by init/log/plan/start/fast phase-zero |
| `/icode limit [natural language]` | Project constraint red lines (standalone step): define and maintain this project's red lines / constraints / forbidden zones. Main store is global + single-checkout override (auto gitignored), append-only evolution. Referenced as hard baseline by the plan step |
| `/icode status` | Read-only: query current ticket status (no dir/file created) |
| `/icode list [keywords]` | Cross-project ticket search: tabulated view of all indexed tickets. **Pure read-only, no jump** |

> Full commands table (incl. "Creates Dir?" column + reuse rules + `--verdict`/`--scan-verdict` flag details): see the `Commands` section of [SKILL.md](SKILL.md).

## Execution / Directory Structure / Workflow

Execution (main session + no automatic model switching) + Directory Structure (`.icode_output_N/` output layout) + Workflow (Steps 1→6 data-flow diagram + fast-mode branch): see the `General Rules` section of [SKILL.md](SKILL.md).

## License

MIT

## DEMO (for testing the icode workflow)

`demo/` is a minimal C calculator project (`calc.h` / `calc.c` / `main.c` / `Makefile`), **purpose-built for end-to-end testing of the icode workflow** — all five invocation modes (A full-flow / B step-by-step / C init→start / D log→start / E fast trimmed) can be run against it: Step 1 plan, Step 4 code, Step 5 deep-check, Step 6 compile verification all have real code to operate on.

```bash
cd demo && make && ./calc_demo   # confirm the baseline builds and runs
```

Example test requirements:

- **Mode A**: `cd demo && /icode start add modulo and power operations to the calculator, plus integer overflow checks`
- **Mode B (step-by-step)**: `cd demo && /icode plan add isqrt to calc.c` then `/icode review` `/icode merge` `/icode code` `/icode deepcheck` `/icode audit`
- **Mode C (init then start)**: `cd demo && /icode init add new feature to calculator` (multi-turn dialogue to clarify) → `/icode start`
- **Mode D (log then start)**: `cd demo && /icode log <log_path> "symptom"` → outputs root cause + fix requirement → `/icode start`
- **Mode E (fast trimmed)**: `cd demo && /icode fast add isqrt to calc.c` (~65% time cost)
