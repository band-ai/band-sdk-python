"""Minimal LLM-as-judge for tolerant, behavioural assertions.

Simple but effective, following the consensus of practical LLM-as-judge guides:

- **Binary verdict.** LLMs are poorly calibrated for fine-grained scores, so we
  ask for pass/fail, not a 1-10 scale.
- **Reasoning before the verdict (chain-of-thought).** The single biggest lever
  on judge accuracy: the model must explain its reasoning *before* committing to
  pass/fail, so ``reasoning`` precedes ``passed`` in the schema.
- **Explicit, single-dimension criteria.**
- **Native structured outputs.** ``messages.parse`` constrains the response to
  the ``Verdict`` Pydantic schema and validates it back into the model, so the
  verdict is guaranteed valid and conformant — no parsing heuristics, no
  truncated-JSON failure mode. Requires a modern Anthropic judge model
  (Sonnet 4.6 / Opus 4.8 / Haiku 4.5 / Fable 5); not the older Claude 3 Haiku.

Out of scope (the full judge harness): human-label calibration, multi-sample
voting / pass^k, pairwise comparison, and position-bias controls.

Future option: when the full judge harness is built, consider adopting DeepEval
(https://github.com/confident-ai/deepeval) instead of growing this — its G-Eval
metric is community-supported, pytest-native, and also covers conversational and
tool-correctness evaluation. Deferred here to avoid the heavy dependency (mind
this repo's existing dependency-conflict constraints) for one minimal verdict.
"""

from __future__ import annotations

import logging

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError

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
    "criteria are fully met."
)


class Verdict(BaseModel):
    """The judge's structured output.

    Fields are declared reasoning-then-passed so the model reasons before
    committing to the verdict (chain-of-thought).
    """

    reasoning: str
    passed: bool


async def judge(
    criteria: str,
    transcript: str | list[MessageCreatedPayload],
    *,
    client: AsyncAnthropic,
    model: str,
) -> Verdict:
    """Render a pass/fail verdict on ``transcript`` against ``criteria``.

    ``transcript`` may be a ready string or a list of captured messages, which
    are formatted as ``[sender]: content`` lines. ``model`` must be a modern
    Anthropic model id (the judge runs on Anthropic only, and structured
    outputs need Sonnet 4.6 / Opus 4.8 / Haiku 4.5 / Fable 5 or newer).
    """
    prompt = (
        f"Criteria:\n{criteria}\n\n"
        f"Transcript to evaluate:\n{format_transcript(transcript)}"
    )

    try:
        response = await client.messages.parse(
            model=model,
            max_tokens=1024,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=Verdict,
        )
    except ValidationError as exc:
        # max_tokens / truncated output: parse validates eagerly and raises
        # before returning, so this is where an incomplete verdict surfaces.
        raise ValueError("Judge returned a malformed or truncated verdict") from exc

    verdict = response.parsed_output
    if verdict is None:
        # refusal: no schema-valid text block was produced.
        raise ValueError(
            f"Judge did not produce a verdict (stop={response.stop_reason})"
        )
    logger.info(
        "Judge verdict: passed=%s reasoning=%s", verdict.passed, verdict.reasoning
    )
    return verdict
