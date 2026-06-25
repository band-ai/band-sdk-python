"""Minimal LLM-as-judge for tolerant, behavioural assertions.

Simple but effective, following the consensus of practical LLM-as-judge guides:

- **Binary verdict.** LLMs are poorly calibrated for fine-grained scores, so we
  ask for pass/fail, not a 1-10 scale.
- **Reasoning before the verdict (chain-of-thought).** The single biggest lever
  on judge accuracy: the model must explain its reasoning *before* committing to
  pass/fail, so ``reasoning`` precedes ``passed`` in the required output.
- **Explicit, single-dimension criteria**, with optional ordered evaluation
  steps (G-Eval style) for harder rubrics.
- **Temperature 0** for repeatable verdicts.
- **Structured JSON output**, parsed defensively.

Out of scope (the full judge harness): human-label calibration, multi-sample
voting / pass^k, pairwise comparison, and position-bias controls.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a meticulous, impartial test evaluator. Judge the transcript ONLY "
    "against the given criteria — not your own preferences. Reason step by step "
    "BEFORE deciding, then commit to a strict pass/fail: pass only if the "
    "criteria are fully met. Respond with ONLY a JSON object of the form "
    '{"reasoning": "<your step-by-step reasoning>", "passed": <true|false>}.'
)


@dataclass(frozen=True)
class Verdict:
    passed: bool
    reasoning: str


def _parse(text: str) -> Verdict:
    """Extract the verdict JSON, tolerating prose or code fences around it."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        raise ValueError(f"Judge returned no JSON object: {text!r}")
    try:
        data = json.loads(match.group(0))
        return Verdict(passed=bool(data["passed"]), reasoning=str(data["reasoning"]))
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"Judge returned unparseable verdict: {text!r}") from exc


async def judge(
    criteria: str,
    transcript: str,
    *,
    model: str,
    api_key: str,
    evaluation_steps: list[str] | None = None,
    temperature: float = 0.0,
) -> Verdict:
    """Render a pass/fail verdict on ``transcript`` against ``criteria``.

    ``evaluation_steps`` optionally supplies an ordered checklist the judge
    should work through (G-Eval style) for criteria that benefit from one.
    """
    steps_block = ""
    if evaluation_steps:
        numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(evaluation_steps, 1))
        steps_block = f"\n\nEvaluation steps (work through these in order):\n{numbered}"

    prompt = (
        f"Criteria:\n{criteria}{steps_block}\n\nTranscript to evaluate:\n{transcript}"
    )

    client = AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model=model,
        max_tokens=512,
        temperature=temperature,
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()
    verdict = _parse(text)
    logger.info(
        "Judge verdict: passed=%s reasoning=%s", verdict.passed, verdict.reasoning
    )
    return verdict
