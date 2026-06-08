# ICode — Six-Step End-to-End Coding Workflow

ICode is a Claude Code Skill that breaks down the journey from requirement to delivery into 6 strict steps. Each step can be invoked independently, allowing you to switch models between steps.

## Features

- **Plan → Review → Finalize → Code → Deep Check → Audit**, six-step closed loop
- Each step callable independently; switch models between steps
- Full-flow mode (`/icode new`) auto-chains all 6 steps
- All steps run in the main session — no sub-agent isolation issues
- Outputs saved under `.icode_output_N/`, supports cross-session recovery
- Metadata management (`.ico_metadata.json`) for execution status and code file tracking
- Triple-phase deepcheck (Reverse → Fixed → Free) to prevent AI laziness and catch implementation gaps
- Plan step enforces assertion verification (Read/Grep validation, `[verified]`/`[unverified]` tags)
- Architecture Decision Records (ADR) section for centralized decision tracking
- Review supports custom round count (`/icode review [N]`) and incremental review mode
- Structured review issues (affected sections / suggestion / rejection risk)

## Installation

Clone this repository into your Claude Code skills directory:

```bash
git clone <repo-url> ~/.claude/skills/icode
```

## Quick Start

```bash
# One-shot full flow (auto model switching)
/icode new Implement MCU rain sensor I2C driver

# Or step by step
/icode plan Implement MCU rain sensor I2C driver   # Step 1: Draft plan
/icode review                                       # Step 2: Review plan (default 3 rounds)
/icode review 5                                     # Step 2: 5-round review
/icode merge                                        # Step 3: Merge & finalize
/icode code                                         # Step 4: Code implementation
/icode deepcheck                                    # Step 5: Iterative re-review
/icode audit                                        # Step 6: Final audit & fix
```

## Commands

| Command | Description | Creates Dir? |
| --- | --- | --- |
| `/icode help` | Help: show usage examples | No |
| `/icode new <req>` | Full flow: create dir → steps 1–6 | Yes |
| `/icode plan <req>` | Step 1 only: draft project plan | Yes |
| `/icode review [N]` | Step 2 only: review the plan (N=rounds, default 3) | No |
| `/icode merge` | Step 3 only: merge reviews & finalize | No |
| `/icode code` | Step 4 only: implement code | No |
| `/icode deepcheck` | Step 5 only: iterative re-check | No |
| `/icode audit` | Step 6 only: final audit + fix | No |

## Execution

All 6 steps run in the main session with the current model. No automatic model switching — use `/model` manually if needed.

## Directory Structure

```
.icode_output_N/
├── .ico_metadata.json      # Metadata (status, code file list)
├── 01_plan.md              # Step 1: Project plan
├── 02_review.md            # Step 2: Review report
├── review_round_*.json     # Step 2: Per-round review details (JSON)
├── 03_plan_final.md        # Step 3: Finalized plan
├── 05_reverse.json         # Step 5: Reverse-engineered spec (single JSON)
├── 05_review_rounds.json   # Step 5: Review round logs (JSONL)
├── 06_audit.md             # Step 6: Audit report
└── 06_fixes.log            # Step 6: Fix log
```

## Workflow

```
[Step 1] Plan → [Step 2] Review → [Step 3] Finalize
                                          ↓
[Step 6] Audit ← [Step 5] Deep Check ← [Step 4] Code
```

## Version

Current version: v1.3.0

For detailed step descriptions, see [SKILL.md](SKILL.md).

## License

MIT
