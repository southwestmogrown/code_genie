# Code Genie

A CrewAI Flow that:

- generates code from a user prompt,
- reviews it for quality,
- optionally refactors through review cycles,
- writes final artifacts to `outputs/` on completion.

## How It Works

The flow (`CodeReviewFlow`) uses two agents:

- `coder`: generates and refactors code
- `reviewer`: approves or requests revisions

Execution path:

1. `write_code`
2. `write_code_review`
3. Router decides:

- `approved` -> finish
- `needs_revision` -> `refactor_code` -> back to review
- `max_rounds_reached` -> finish

On finish, artifacts are written to `outputs/`:

- `<artifact_name>.<ext>`
- `<artifact_name>_review.md`

Supported code artifact languages/extensions:

- Python: `.py`
- JavaScript: `.js`
- Go: `.go`
- SQL: `.sql`

`outputs/` is cleared before writing new artifacts.

## Requirements

- Python `>=3.10,<3.14`
- `uv`
- Dependencies from `pyproject.toml`
- Model/provider access configured for the agents in:
  - `src/coder_reveiwer_flow/crews/coder_reviewer_crew/config/agents.yaml`

## Setup

```bash
uv sync
```

## Run

### Option 1: Pass prompt directly to project script

```bash
uv run kickoff --prompt "Write a Python function named add_one that adds 1 to an integer."
```

### Option 2: Use CrewAI CLI with env var prompt

```bash
CODE_GENIE="Write a Python function named add_one that adds 1 to an integer." crewai flow kickoff
```

### Option 3: Use default prompt

```bash
crewai flow kickoff
```

If no prompt is supplied, `DEFAULT_CODING_PROMPT` from `src/coder_reveiwer_flow/main.py` is used.

## Output

After a successful run:

- `outputs/<artifact_name>.<ext>` contains cleaned code (no markdown code fences)
- `outputs/<artifact_name>_review.md` contains reviewer output

## Project Layout

- `src/coder_reveiwer_flow/main.py`: flow orchestration and artifact writing
- `src/coder_reveiwer_flow/crews/coder_reviewer_crew/config/agents.yaml`: agent configs and model settings
- `src/coder_reveiwer_flow/crews/coder_reviewer_crew/config/tasks.yaml`: task definitions
- `outputs/`: generated artifacts

## Notes

- This repository includes a local `.git` folder.
- If you plan to commit, keep secrets only in `.env` and never commit that file.
