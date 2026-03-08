# Code Review: `coder_reveiwer_flow`

Overall this is a solid first crewAI flow. The architecture is coherent, the state model is clean, and the routing logic maps well to the problem. Notes below go from most important to stylistic.

---

## Issues

### 1. Dead code — crew tasks are never used

`CoderReviewerCrew` defines three `@task` methods (`write_code`, `review_code`, `refactor_code`) and a `@crew` method, but the flow never calls `CoderReviewerCrew().crew().kickoff(...)`. It only calls:

```python
CoderReviewerCrew().coder()
CoderReviewerCrew().reviewer()
```

This means the entire `tasks.yaml` and all `@task` methods are dead code. The `output_file='report.md'` on `review_code` also explains the orphaned `report.md` in the repo root.

**Option A — Lean into the direct-agent pattern (matches current code):**
Delete the `@task` methods, `@crew` method, and `tasks.yaml` entirely. Rename the class to something like `CoderReviewerAgents` to signal it's an agent factory.

**Option B — Lean into the crew pattern:**
Replace the flow's `agent.kickoff()` calls with a proper `crew.kickoff(inputs={...})` call per step, and let the crew's sequential process handle the coder→reviewer handoff. The flow then handles routing/looping around crew results.

Either is valid; mixing both creates confusion about which layer is authoritative.

---

### 2. `CoderReviewerCrew` is re-instantiated on every agent call

```python
def _coder_agent(self):
    return CoderReviewerCrew().coder()   # new CoderReviewerCrew each call

def _reviewer_agent(self):
    return CoderReviewerCrew().reviewer()  # new CoderReviewerCrew each call
```

`CoderReviewerCrew()` reads and parses YAML on every instantiation. Cache it:

```python
def __init__(self):
    super().__init__()
    self._crew = CoderReviewerCrew()

def _coder_agent(self):
    return self._crew.coder()

def _reviewer_agent(self):
    return self._crew.reviewer()
```

---

### 3. Approval detection is fragile

```python
self.state.IS_APPROVED = "APPROVED" in result.raw.upper()
```

A reviewer responding "NOT APPROVED" or "The code is APPROVED with caveats, but..." will still set `IS_APPROVED = True`. Anchor the match to the beginning of the response:

```python
first_line = result.raw.strip().splitlines()[0].upper()
self.state.IS_APPROVED = first_line.startswith("APPROVED")
```

---

### 4. `or_()` should use method references, not strings

From the crewAI docs and `AGENTS.md`:

```python
# Current (strings) — works but fragile and non-idiomatic
@listen(or_("write_code", "refactor_code"))

# Preferred (method references)
@listen(or_(write_code, refactor_code))
```

String labels in `@listen` are meant for **router outputs** (e.g., `@listen("approved")`). Method references are for method completion events. Using method references gives you IDE navigation and refactor-safety.

The forward-reference problem (referencing `refactor_code` before it's defined) can be resolved by reordering the methods so `refactor_code` appears before `write_code_review`:

```python
@listen("needs_revision")
def refactor_code(self): ...   # defined first

@listen(or_(write_code, refactor_code))  # now refactor_code is in scope
def write_code_review(self): ...
```

---

### 5. Imports inside function body

In `run_with_trigger()`:

```python
def run_with_trigger():
    import json
    import sys
```

Move `import json` and `import sys` to the top of the file with the other stdlib imports.

---

## Style / Convention

### 6. `IS_APPROVED` field name

Pydantic model fields use `snake_case`. `IS_APPROVED` looks like a constant:

```python
# current
IS_APPROVED: bool = False

# conventional
is_approved: bool = False
```

Update the two references in `main.py` (`self.state.IS_APPROVED = ...` and `if self.state.IS_APPROVED:`).

---

### 7. Leftover scaffold comments in the crew class

`coder_reviewer_crew.py` still has several boilerplate comments from `crewai create`:

```python
# If you want to run a snippet of code before or after the crew starts,
# you can use the @before_kickoff and @after_kickoff decorators
# https://docs.crewai.com/concepts/crews#example-crew-class-with-decorators
```

These are fine while learning but should be removed before treating this as production code.

---

## What's Working Well

- **`CodeReviewState` as a Pydantic model** — exactly the right pattern for typed flow state. Much better than unstructured `self.state["key"]` dict access.
- **`_extract_python_code` + `_get_function_name`** — clean, practical utilities. Using `ast.parse` for function name extraction is the right approach (handles `AsyncFunctionDef` too).
- **`max_revision_rounds` guard** — prevents infinite loops. Good defensive design.
- **`or_("write_code", "refactor_code")`** — the intent is correct: the reviewer runs after both initial coding and refactoring. This is the right use of `or_()`.
- **Prompt via `--prompt` arg, env var, and default** — the three-tier fallback in `kickoff()` and `write_code()` is clean.
- **YAML-defined LLM per agent** — setting `llm: ollama/llama3.1:latest` in `agents.yaml` rather than hardcoding it in Python is the right separation of concerns.
- **`_write_artifacts()` clearing the output dir** — explicit and predictable; documented in README.

---

## Summary Table

| # | Severity | File | Finding |
|---|----------|------|---------|
| 1 | High | `coder_reviewer_crew.py` / `tasks.yaml` | Tasks defined but never used — dead code |
| 2 | Medium | `main.py` | Crew re-instantiated on every agent call |
| 3 | Medium | `main.py:113` | Approval check matches substrings; anchoring needed |
| 4 | Low | `main.py:99` | `or_()` should use method refs not strings |
| 5 | Low | `main.py` | Stdlib imports inside function body |
| 6 | Style | `main.py:21` | `IS_APPROVED` → `is_approved` |
| 7 | Style | `coder_reviewer_crew.py` | Scaffold comments left in |
