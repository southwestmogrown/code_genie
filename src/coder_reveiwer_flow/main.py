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

from crewai.flow import Flow, listen, router, start, or_

from .crews.coder_reviewer_crew.coder_reviewer_crew import CoderReviewerCrew


class CodeReviewState(BaseModel):
    coding_prompt: str = ""
    draft_code: str = ""
    code_review: str = ""
    is_approved: bool = False
    revision_round: int = 0
    max_revision_rounds: int = 5


DEFAULT_CODING_PROMPT = (
    "Write a Python function that verifies whether a given string is a valid "
    "email address. Return only the function code and a short usage example."
)


class CodeReviewFlow(Flow[CodeReviewState]):
    """Flow for writing and reviewing code snippets"""

    def __init__(self):
        super().__init__()
        self._crew = CoderReviewerCrew()

    def _extract_python_code(self, text: str) -> str:
        stripped = text.strip()

        # If the model returned a fenced code block, keep only its inner code.
        fence_match = re.search(r"```(?:python)?\s*(.*?)\s*```", stripped, re.DOTALL | re.IGNORECASE)
        if fence_match:
            return fence_match.group(1).strip()

        # Defensive cleanup for partial fence markers.
        lines = []
        for line in stripped.splitlines():
            if line.strip().startswith("```"):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    def _get_function_name(self, code: str) -> str:
        try:
            module = ast.parse(code)
        except SyntaxError:
            return "generated_function"

        for node in module.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name
        return "generated_function"

    def _write_artifacts(self) -> tuple[Path, Path]:
        output_dir = Path("outputs")
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        clean_code = self._extract_python_code(self.state.draft_code)
        function_name = self._get_function_name(clean_code)

        code_path = output_dir / f"{function_name}.py"
        review_path = output_dir / f"{function_name}_review.md"

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
            "Return only the code and a short usage example.\n\n"
            f"User request:\n{resolved_prompt}"
        )

        result = self._coder_agent().kickoff(prompt)
        self.state.draft_code = result.raw
        print("Initial code generated")
        return self.state.draft_code

    @listen("needs_revision")
    def refactor_code(self):
        print("Refactoring code")
        prompt = (
            "Refactor this Python code using the review feedback. Keep behavior correct "
            "and improve readability/maintainability. Return only the updated code.\n\n"
            f"Current code:\n{self.state.draft_code}\n\n"
            f"Review feedback:\n{self.state.code_review}"
        )

        result = self._coder_agent().kickoff(prompt)
        print("Code refactored", result.raw)
        self.state.draft_code = result.raw
        self.state.revision_round += 1
        return self.state.draft_code

    @listen(or_(write_code, refactor_code))
    def write_code_review(self):
        print("Generating code review")
        prompt = (
            "Review this Python code for correctness, performance, and best practices.\n"
            "If the code is acceptable, start your response with the heading APPROVED.\n"
            "If it is not acceptable, start your response with the heading REVISIONS NEEDED\n"
            "and include concrete, actionable fixes.\n\n"
            f"Code to review:\n{self.state.draft_code}"
        )

        result = self._reviewer_agent().kickoff(prompt)
        print("Code review generated", result.raw)
        self.state.code_review = result.raw
        first_line = result.raw.strip().splitlines()[0].upper()
        self.state.is_approved = first_line.startswith("APPROVED")
        return self.state.code_review

    @router(write_code_review)
    def route_after_review(self):
        if self.state.is_approved:
            return "approved"

        if self.state.revision_round >= self.state.max_revision_rounds:
            return "max_rounds_reached"

        return "needs_revision"

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

    prompt = args.prompt.strip() or os.getenv("CODER_REVIEW_PROMPT", "").strip()

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
