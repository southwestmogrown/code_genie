#!/usr/bin/env python
import argparse
import ast
import json
import os
import re
import shutil
import sys
from pathlib import Path

from pydantic import BaseModel

from crewai.flow import Flow, listen, router, start

from .crews.coder_reviewer_crew.coder_reviewer_crew import CoderReviewerCrew


class CodeReviewState(BaseModel):
    coding_prompt: str = ""
    draft_code: str = ""
    code_review: str = ""
    is_approved: bool = False
    revision_round: int = 0
    max_revision_rounds: int = 10


DEFAULT_CODING_PROMPT = (
    "Write a Python function that verifies whether a given string is a valid "
    "email address. Return only the function code and a short usage example."
)


class CodeReviewFlow(Flow[CodeReviewState]):
    """Flow for writing and reviewing code snippets"""

    def __init__(self):
        super().__init__()
        self._crew = CoderReviewerCrew()

    def _extract_code(self, text: str) -> str:
        stripped = text.strip()

        # If the model returned a fenced code block, keep only its inner code.
        fence_match = re.search(r"```(?:[a-zA-Z0-9_+-]+)?\s*(.*?)\s*```", stripped, re.DOTALL | re.IGNORECASE)
        if fence_match:
            return fence_match.group(1).strip()

        # Defensive cleanup for partial fence markers.
        lines = []
        for line in stripped.splitlines():
            if line.strip().startswith("```"):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    def _normalize_language(self, language: str) -> str:
        lang = language.lower().strip()
        if lang in {"python", "py"}:
            return "python"
        if lang in {"javascript", "js", "node", "nodejs"}:
            return "javascript"
        if lang in {"go", "golang"}:
            return "go"
        if lang in {"sql", "postgres", "postgresql", "mysql", "sqlite"}:
            return "sql"
        return "python"

    def _detect_language(self, raw_text: str, prompt: str, clean_code: str) -> str:
        prompt_lower = prompt.lower()
        prompt_hint = re.search(r"\b(python|py|javascript|js|nodejs|node|golang|go|sql|postgresql|mysql|sqlite)\b", prompt_lower)
        if prompt_hint:
            return self._normalize_language(prompt_hint.group(1))

        fence_hint = re.search(r"```\s*([a-zA-Z0-9_+-]+)", raw_text)
        if fence_hint:
            return self._normalize_language(fence_hint.group(1))

        if re.search(r"^\s*func\s+[A-Za-z_][A-Za-z0-9_]*\s*\(", clean_code, re.MULTILINE):
            return "go"
        if re.search(r"\bfunction\s+[A-Za-z_$][A-Za-z0-9_$]*\s*\(|\b(const|let|var)\s+[A-Za-z_$][A-Za-z0-9_$]*\s*=\s*(async\s*)?\(?", clean_code):
            return "javascript"
        if re.search(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|WITH)\b", clean_code, re.IGNORECASE | re.MULTILINE):
            return "sql"
        return "python"

    def _safe_basename(self, value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
        return cleaned.lower() or "generated_code"

    def _get_artifact_basename(self, code: str, language: str) -> str:
        if language == "python":
            try:
                module = ast.parse(code)
            except SyntaxError:
                return "generated_function"

            for node in module.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return self._safe_basename(node.name)
            return "generated_function"

        if language == "javascript":
            patterns = [
                r"\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(",
                r"\b(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?\(?",
            ]
            for pattern in patterns:
                match = re.search(pattern, code)
                if match:
                    return self._safe_basename(match.group(1))
            return "generated_function"

        if language == "go":
            match = re.search(r"\bfunc\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", code)
            if match:
                return self._safe_basename(match.group(1))
            return "generated_function"

        if language == "sql":
            sql_patterns = [
                (r"\bCREATE\s+(?:TABLE|VIEW)\s+([A-Za-z_][A-Za-z0-9_]*)", "create"),
                (r"\bINSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", "insert_into"),
                (r"\bUPDATE\s+([A-Za-z_][A-Za-z0-9_]*)", "update"),
                (r"\bDELETE\s+FROM\s+([A-Za-z_][A-Za-z0-9_]*)", "delete_from"),
                (r"\bSELECT\b.*?\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)", "select_from"),
            ]
            for pattern, prefix in sql_patterns:
                match = re.search(pattern, code, re.IGNORECASE | re.DOTALL)
                if match:
                    return self._safe_basename(f"{prefix}_{match.group(1)}")
            return "query"

        return "generated_code"

    def _language_extension(self, language: str) -> str:
        return {
            "python": "py",
            "javascript": "js",
            "go": "go",
            "sql": "sql",
        }.get(language, "txt")

    def _is_approved_review(self, review_text: str) -> bool:
        normalized_lines: list[str] = []
        for line in review_text.splitlines():
            normalized = re.sub(r"^[\s#>*`\-_:]+", "", line).strip().upper()
            if normalized:
                normalized_lines.append(normalized)

        if any(line.startswith("REVISIONS NEEDED") for line in normalized_lines):
            return False

        return any(line.startswith("APPROVED") for line in normalized_lines)

    def _get_function_name(self, code: str) -> str:
        # Backward-compatible wrapper retained for existing callers.
        return self._get_artifact_basename(code, "python")

    def _write_artifacts(self) -> tuple[Path, Path]:
        output_dir = Path("outputs")
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        clean_code = self._extract_code(self.state.draft_code)
        language = self._detect_language(self.state.draft_code, self.state.coding_prompt, clean_code)
        artifact_basename = self._get_artifact_basename(clean_code, language)
        extension = self._language_extension(language)

        code_path = output_dir / f"{artifact_basename}.{extension}"
        review_path = output_dir / f"{artifact_basename}_review.md"

        code_path.write_text(clean_code + "\n", encoding="utf-8")
        review_path.write_text(self.state.code_review.strip() + "\n", encoding="utf-8")

        return code_path, review_path

    def _coder_agent(self):
        return self._crew.coder()

    def _reviewer_agent(self):
        return self._crew.reviewer()

    @start()
    def write_code(self, coding_prompt: str = ""):
        print("Writing initial code")
        resolved_prompt = coding_prompt.strip() or self.state.coding_prompt.strip() or DEFAULT_CODING_PROMPT
        self.state.coding_prompt = resolved_prompt
        prompt = (
            "Write code that satisfies the following user request. "
            "Use the same programming language requested by the user. "
            "Return only the code and a short usage example.\n\n"
            f"User request:\n{resolved_prompt}"
        )

        result = self._coder_agent().kickoff(prompt)
        self.state.draft_code = result.raw
        print("Initial code generated")
        return self.state.draft_code

    @listen(write_code)
    def review_and_refactor_loop(self):
        while True:
            print("Generating code review")
            review_prompt = (
                "Review this code for correctness, performance, and best practices.\n"
                "If the code is acceptable, start your response with the heading APPROVED.\n"
                "If it is not acceptable, start your response with the heading REVISIONS NEEDED\n"
                "and include concrete, actionable fixes.\n\n"
                f"Code to review:\n{self.state.draft_code}"
            )
            result = self._reviewer_agent().kickoff(review_prompt)
            self.state.code_review = result.raw
            self.state.is_approved = self._is_approved_review(result.raw)
            print("Code review generated", result.raw)

            if self.state.is_approved:
                print("Code approved.")
                break

            if self.state.revision_round >= self.state.max_revision_rounds:
                print("Maximum revision rounds reached.")
                break

            print("Refactoring code")
            refactor_prompt = (
                "Refactor this code using the review feedback. Keep behavior correct "
                "and improve readability/maintainability. Return only the updated code.\n\n"
                f"Current code:\n{self.state.draft_code}\n\n"
                f"Review feedback:\n{self.state.code_review}"
            )
            result = self._coder_agent().kickoff(refactor_prompt)
            self.state.draft_code = result.raw
            self.state.revision_round += 1
            print("Code refactored")

    @router(review_and_refactor_loop)
    def route_after_loop(self):
        if self.state.is_approved:
            return "approved"
        return "max_rounds_reached"

    @listen("approved")
    def finish_approved(self):
        code_path, review_path = self._write_artifacts()
        print("Code approved. Flow complete.")
        print(f"Saved code to {code_path}")
        print(f"Saved review to {review_path}")
        return self.state.draft_code

    @listen("max_rounds_reached")
    def finish_unapproved(self):
        code_path, review_path = self._write_artifacts()
        print("Maximum revision rounds reached without approval. Stopping flow.")
        print(f"Saved code to {code_path}")
        print(f"Saved review to {review_path}")
        return self.state.code_review


def kickoff():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--prompt",
        type=str,
        default="",
        help="User coding request for the flow.",
    )
    args, _ = parser.parse_known_args()

    prompt = args.prompt.strip() or os.getenv("CODE_GENIE", "").strip()

    code_review_flow = CodeReviewFlow()
    code_review_flow.kickoff(inputs={"coding_prompt": prompt})


def plot():
    code_review_flow = CodeReviewFlow()
    code_review_flow.plot()


def run_with_trigger():
    """Run the flow with trigger payload."""
    if len(sys.argv) < 2:
        raise Exception("No trigger payload provided. Please provide JSON payload as argument.")

    try:
        trigger_payload = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        raise Exception("Invalid JSON payload provided as argument")

    code_review_flow = CodeReviewFlow()

    try:
        result = code_review_flow.kickoff({"crewai_trigger_payload": trigger_payload})
        return result
    except Exception as e:
        raise Exception(f"An error occurred while running the flow with trigger: {e}")


if __name__ == "__main__":
    kickoff()
