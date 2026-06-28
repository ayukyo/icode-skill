# ICode — End-to-End Coding Workflow (Step 0 + 1~6, with log root-cause analysis entry)

ICode is a Claude Code Skill that breaks down the journey from requirement to delivery into strict steps. Each step can be invoked independently, allowing you to switch models between steps.

- **Entry commands (optional)**: `/icode log` log root-cause analysis (domain-agnostic) → fix requirement; `/icode init` requirement-draft conversation
- **Step 0 (optional)**: Iterative requirement-draft conversation, output as `00_init.md`
- **Steps 1~6**: Plan → Review → Finalize → Code → Deep Check → Audit

## Features

- **(Optional) Requirement Draft → Plan → Review → Finalize → Code → Deep Check → Audit**, closed-loop delivery
- Each step callable independently; switch models between steps
- Full-flow mode (`/icode start` full 3-round review + adversarial / `/icode fast` trimmed 1-round no-adversarial) auto-chains steps 1→6
- All steps run in the main session — no sub-agent isolation issues
- Outputs saved under `.icode_output/.icode_output_N/` (unified under the `.icode_output/` parent dir), supports cross-session recovery
- Metadata management (`.ico_metadata.json`) for execution status and code file tracking
- Triple-phase deepcheck (Reverse → Fixed → Free) to prevent AI laziness and catch implementation gaps
- Plan step enforces assertion verification (Read/Grep validation, `[verified]`/`[unverified]` tags)
- Architecture Decision Records (ADR) section for centralized decision tracking
- Review supports custom round count (`/icode review [N]`) and incremental review mode
- Structured review issues (affected sections / suggestion / rejection risk / evidence pointer / adversarial verification status)
- Review introduces independent skeptic sub-agents for adversarial verification (three lenses: evidence / alternative-explanation / sufficiency); issues without adversarial verification or sufficient evidence cannot be confirmed; unverifiable assertions are honestly downgraded to `[unverified-insufficient-evidence]` rather than faking consensus
- Cross-project historical retrieval & reuse: `/icode init`/`/icode log`/`/icode plan`/`/icode start` auto-search a global index (`~/.claude/icode_data/index.json`) for similar past tickets and inject references by command (init → requirement points / log → root cause+evidence / plan → ADR+risk); three gates prevent context overflow; references stay in-session only, never pollute project artifacts
- `/icode log` log root-cause analysis entry: baseline check first (git diff / state-link diagram) then log reconnaissance, adversarial analysis (3 skeptics) prevents confirmation bias, honest downgrade rather than fabricating root cause; outputs `log_analysis.md` + auto-converts to fix requirement `00_init.md` feeding Step 1; domain-agnostic (robotics / server / embedded / web all OK)
- `/icode init` produces a `00_init.md` requirement draft via multi-turn dialogue, updated incrementally each round; `/icode start`/`/icode plan` (no args) detects init/log entry state and asks "reuse/new", choosing reuse takes `00_init.md` as requirement input

## Installation

Clone this repository into your Claude Code skills directory:

```bash
git clone <repo-url> ~/.claude/skills/icode
```

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

# When the requirement is unclear: discuss first, then enter the flow
/icode init Record point_cloud / lidar_imu re-bag    # Step 0: kick-off draft + dialogue
# ... multi-turn discussion; 00_init.md is updated incrementally each round ...
/icode start                                          # No args → detects init entry state, asks "reuse/new"; reuse takes 00_init.md as input → steps 1–6

# From a bug log: analyze root cause first, then fix
/icode log ~/work/log/recharge-anomaly "no rotation after undocking"   # Entry: analyze log root cause, outputs log_analysis.md + fix requirement 00_init.md
# ... after adversarial analysis converges; if you doubt it, keep talking to re-run the disputed branch ...
/icode start                                          # No args → detects log_done entry state, asks "reuse/new"; reuse takes 00_init.md (fix requirement) as input → steps 1–6
```

## Commands

| Command | Description | Creates Dir? |
| --- | --- | --- |
| `/icode help` | Help: show usage examples | No |
| `/icode log [scattered info...]` | Optional entry: log root-cause analysis → fix requirement `00_init.md` (domain-agnostic, always fresh) | Yes (always fresh) |
| `/icode init [<rough req>]` | Optional Step 0: multi-turn dialogue → `00_init.md` (always creates a fresh directory) | Yes (always fresh) |
| `/icode start <req>` | Full flow: create/reuse dir → steps 1–6 | Yes / Reuse |
| `/icode fast <req>` | Trimmed full flow: plan→review(1 round, no adversarial)→merge→code→deepcheck(Reverse only)→audit (~65% of full-flow cost) | Yes / Reuse |
| `/icode plan <req>` | Step 1 only: draft project plan | Yes / Reuse |
| `/icode review [N]` | Step 2 only: review the plan (N=soft cap rounds, default 3; auto-extends +2 if issues remain) | No |
| `/icode merge` | Step 3 only: merge reviews & finalize | No |
| `/icode code` | Step 4 only: implement code | No |
| `/icode deepcheck` | Step 5 only: three-phase progressive check (Reverse → Fixed → Free; fast mode runs Reverse only) | No |
| `/icode audit` | Step 6 only: final audit + fix (produces `06_audit.md`) | No |
| `/icode readme` | Optional Step 7: generate delivery report (self-contained summary, dynamic filename, smart feature/bugfix template) | No |
| `/icode status` | Read-only: query current ticket status (no dir/file created) | No |

> When `/icode start` / `/icode plan` / `/icode fast` is launched and the latest `.icode_output/.icode_output_N/` is in entry state (status `init_in_progress` or `log_done`, i.e. init/log produced `00_init.md` but hasn't entered Step 1), it **asks the user "reuse/new"** — reuse takes `00_init.md` as input (from log, also reads `log_analysis.md` as background); non-entry state with args creates fresh.

## Execution

All steps run in the main session with the current model. No automatic model switching — use `/model` manually if needed.

## Directory Structure

```text
.icode_output/                # Unified parent dir, holds all outputs
└── .icode_output_N/          # N auto-increments (new per requirement)
    ├── .ico_metadata.json      # Metadata (status, code file list)
    ├── log_analysis.md         # Entry log (optional): log root-cause analysis report
    ├── 00_init.md              # Step 0 (optional) / Entry log: Requirement draft (incrementally updated)
    ├── 01_plan.md              # Step 1: Project plan
    ├── 02_review.md            # Step 2: Review report
    ├── review_round_*.json     # Step 2: Per-round review details (JSON)
    ├── 03_plan_final.md        # Step 3: Finalized plan (reserves "implementation deviation memo" section at the end, written back by Step 6)
    ├── 05_deepcheck.md          # Step 5: Three-phase deepcheck (Reverse/Fixed/Free merged)
    ├── 06_audit.md             # Step 6: Audit report (incl. fix log section)
    └── {dynamic_name}.md      # Step 7 (optional): delivery report (generated by /icode readme, self-contained summary)
```

## Workflow

```text
[Entry log (optional)] Log root-cause analysis → fix requirement 00_init.md
[Step 0 (optional)] Requirement Draft Dialogue → 00_init.md
       ↓ (start/plan no args → ask reuse/new)
[Step 1] Plan → [Step 2] Review → [Step 3] Finalize
                                          ↓
[Step 6] Audit (incl. deviation writeback) ← [Step 5] Deep Check ← [Step 4] Code
```

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
