"""Minimal LLM-as-judge for tolerant, behavioural assertions.

Simple but effective, following the consensus of practical LLM-as-judge guides:

- **Binary verdict.** LLMs are poorly calibrated for fine-grained scores, so we
  ask for pass/fail, not a 1-10 scale.
- **Reasoning before the verdict (chain-of-thought).** The single biggest lever
  on judge accuracy: the model must explain its reasoning *before* committing to
  pass/fail, so ``reasoning`` precedes ``passed`` in the required output.
- **Explicit, single-dimension criteria.**
- **Temperature 0** for repeatable verdicts.
- **Structured JSON output**, parsed defensively.

Out of scope (the full judge harness): human-label calibration, multi-sample
voting / pass^k, pairwise comparison, and position-bias controls.

Future option: when the full judge harness is built, consider adopting DeepEval
(https://github.com/confident-ai/deepeval) instead of growing this — its G-Eval
metric is community-supported, pytest-native, and also covers conversational and
tool-correctness evaluation. Deferred here to avoid the heavy dependency (mind
this repo's existing dependency-conflict constraints) for one minimal verdict.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from band.client.streaming import MessageCreatedPayload

logger = logging.getLogger(__name__)


def format_transcript(transcript: str | list[MessageCreatedPayload]) -> str:
    """Accept a ready string or a list of captured messages and return text.

    Messages are rendered as ``[sender]: content`` lines.
    """
    if isinstance(transcript, str):
        return transcript
    return "\n".join(
        f"[{m.sender_name or m.sender_id}]: {m.content}" for m in transcript
    )


_SYSTEM = (
    "You are a meticulous, impartial test evaluator. Judge the transcript ONLY "
    "against the given criteria — not your own preferences. Reason step by step "
    "BEFORE deciding, but keep your reasoning brief (a few sentences; do not "
    "transcribe ids). Then commit to a strict pass/fail: pass only if the "
    "criteria are fully met. Respond with ONLY a JSON object of the form "
    '{"reasoning": "<brief reasoning>", "passed": <true|false>}.'
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
    transcript: str | list[MessageCreatedPayload],
    *,
    model: str,
    api_key: str,
) -> Verdict:
    """Render a pass/fail verdict on ``transcript`` against ``criteria``.

    ``transcript`` may be a ready string or a list of captured messages, which
    are formatted as ``[sender]: content`` lines.
    """
    prompt = (
        f"Criteria:\n{criteria}\n\n"
        f"Transcript to evaluate:\n{format_transcript(transcript)}"
    )

    client = AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model=model,
        max_tokens=1024,
        temperature=0.0,
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
