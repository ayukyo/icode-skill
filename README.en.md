# ICode — End-to-End Coding Workflow (Step 0 + 1~6)

ICode is a Claude Code Skill that breaks down the journey from requirement to delivery into strict steps. Each step can be invoked independently, allowing you to switch models between steps.

- **Step 0 (optional)**: Iterative requirement-draft conversation, output as `00_init.md`
- **Steps 1~6**: Plan → Review → Finalize → Code → Deep Check → Audit

## Features

- **(Optional) Requirement Draft → Plan → Review → Finalize → Code → Deep Check → Audit**, closed-loop delivery
- Each step callable independently; switch models between steps
- Full-flow mode (`/icode start`) auto-chains steps 1→6
- All steps run in the main session — no sub-agent isolation issues
- Outputs saved under `.icode_output/.icode_output_N/` (unified under the `.icode_output/` parent dir), supports cross-session recovery
- Metadata management (`.ico_metadata.json`) for execution status and code file tracking
- Triple-phase deepcheck (Reverse → Fixed → Free) to prevent AI laziness and catch implementation gaps
- Plan step enforces assertion verification (Read/Grep validation, `[verified]`/`[unverified]` tags)
- Architecture Decision Records (ADR) section for centralized decision tracking
- Review supports custom round count (`/icode review [N]`) and incremental review mode
- Structured review issues (affected sections / suggestion / rejection risk / evidence pointer / adversarial verification status)
- Review introduces independent skeptic sub-agents for adversarial verification (three lenses: evidence / alternative-explanation / sufficiency); issues without adversarial verification or sufficient evidence cannot be confirmed; unverifiable assertions are honestly downgraded to `[unverified-insufficient-evidence]` rather than faking consensus
- Cross-project historical retrieval & reuse: `/icode init`/`/icode plan`/`/icode start` auto-search a global index (`~/.claude/icode_data/index.json`) for similar past tickets and inject references by command (init → requirement points / plan → ADR+risk); three gates prevent context overflow; references stay in-session only, never pollute project artifacts
- `/icode init` produces a `00_init.md` requirement draft via multi-turn dialogue, updated incrementally each round; `/icode start`/`/icode plan` auto-detects and reuses the directory as requirement input

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

# When the requirement is unclear: discuss first, then enter the flow
/icode init Record point_cloud / lidar_imu re-bag    # Step 0: kick-off draft + dialogue
# ... multi-turn discussion; 00_init.md is updated incrementally each round ...
/icode start                                          # Reuses the same directory; uses 00_init.md as input
```

## Commands

| Command | Description | Creates Dir? |
| --- | --- | --- |
| `/icode help` | Help: show usage examples | No |
| `/icode init [<rough req>]` | Optional Step 0: multi-turn dialogue → `00_init.md` (always creates a fresh directory) | Yes (always fresh) |
| `/icode start <req>` | Full flow: create/reuse dir → steps 1–6 | Yes / Reuse |
| `/icode plan <req>` | Step 1 only: draft project plan | Yes / Reuse |
| `/icode review [N]` | Step 2 only: review the plan (N=soft cap rounds, default 3; auto-extends +2 if issues remain) | No |
| `/icode merge` | Step 3 only: merge reviews & finalize | No |
| `/icode code` | Step 4 only: implement code | No |
| `/icode deepcheck` | Step 5 only: iterative re-check | No |
| `/icode audit` | Step 6 only: final audit + fix + documentation (produces `README.md`) | No |

> When `/icode start` / `/icode plan` is launched and the latest `.icode_output/.icode_output_N/` contains only `00_init.md` (Step 0 output), it **reuses that directory** with `00_init.md` as the requirement input; otherwise it creates a fresh directory as usual.

## Execution

All steps run in the main session with the current model. No automatic model switching — use `/model` manually if needed.

## Directory Structure

```text
.icode_output/                # Unified parent dir, holds all outputs
└── .icode_output_N/          # N auto-increments (new per requirement)
    ├── .ico_metadata.json      # Metadata (status, code file list)
    ├── 00_init.md              # Step 0 (optional): Requirement draft (incrementally updated)
    ├── 01_plan.md              # Step 1: Project plan
    ├── 02_review.md            # Step 2: Review report
    ├── review_round_*.json     # Step 2: Per-round review details (JSON)
    ├── 03_plan_final.md        # Step 3: Finalized plan
    ├── 05_reverse.json         # Step 5: Reverse-engineered spec (single JSON)
    ├── 05_review_rounds.json   # Step 5: Review round logs (JSONL)
    ├── 06_audit.md             # Step 6: Audit report
    ├── 06_fixes.log            # Step 6: Fix log
    └── README.md               # Step 6.4 documentation: change summary
```

## Workflow

```text
[Step 0 (optional)] Requirement Draft Dialogue
       ↓
[Step 1] Plan → [Step 2] Review → [Step 3] Finalize
                                          ↓
[Step 6] Audit ← [Step 5] Deep Check ← [Step 4] Code
```

## License

MIT
