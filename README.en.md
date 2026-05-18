# ICode — Six-Step End-to-End Coding Workflow

ICode is a Claude Code Skill that breaks down the journey from requirement to delivery into 6 strict steps. Each step can be invoked independently, allowing you to switch models between steps.

## Features

- **Plan → Review → Finalize → Code → Deep Check → Audit**, six-step closed loop
- Each step callable independently; switch models between steps
- Outputs saved under `.icode_output_N/`, supports cross-session recovery
- Metadata management (`.ico_metadata.json`) for execution status and code file tracking

## Installation

Clone this repository into your Claude Code skills directory:

```bash
git clone <repo-url> ~/.claude/skills/icode
```

## Quick Start

```bash
# One-shot full flow
/icode new Implement MCU rain sensor I2C driver

# Or step by step
/icode plan Implement MCU rain sensor I2C driver   # Step 1: Draft plan
/icode review                                       # Step 2: Review plan
/icode merge                                        # Step 3: Merge & finalize
/icode code                                         # Step 4: Code implementation
/icode deepcheck                                    # Step 5: Iterative re-review
/icode audit                                        # Step 6: Final audit & fix
```

## Commands

| Command | Description | Creates Dir? |
|---------|-------------|-------------|
| `/icode new <req>` | Full flow: create dir → steps 1–6 | Yes |
| `/icode plan <req>` | Step 1 only: draft project plan | Yes |
| `/icode review` | Step 2 only: review the plan | No |
| `/icode merge` | Step 3 only: merge reviews & finalize | No |
| `/icode code` | Step 4 only: implement code | No |
| `/icode deepcheck` | Step 5 only: iterative re-check | No |
| `/icode audit` | Step 6 only: final audit + fix | No |

## Directory Structure

```
.icode_output_N/
├── .ico_metadata.json      # Metadata (status, code file list)
├── 01_plan.md              # Step 1: Project plan
├── 02_review.md            # Step 2: Review report
├── 03_plan_final.md        # Step 3: Finalized plan
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

For detailed step descriptions, see [SKILL.md](SKILL.md).

## License

MIT
