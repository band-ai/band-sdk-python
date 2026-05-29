from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from .scenario import ScenarioResult, Status


@dataclass
class ExampleReport:
    example_name: str
    framework: str
    agent_id: str
    llm_model: str = ""
    platform: str = "app.band.ai"
    date: str = field(default_factory=lambda: datetime.date.today().isoformat())
    startup_ok: bool = False
    startup_logs: str = ""
    scenario_results: list[ScenarioResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def overall_status(self) -> Status:
        if not self.startup_ok:
            return Status.FAIL
        if not self.scenario_results:
            return Status.PASS
        statuses = [s.status for s in self.scenario_results]
        if all(s == Status.PASS for s in statuses):
            return Status.PASS
        if all(s == Status.FAIL for s in statuses):
            return Status.FAIL
        return Status.PARTIAL


class Reporter:
    @staticmethod
    def render(report: ExampleReport) -> str:
        lines = [
            f"# QA Report: {report.framework} / {report.example_name}",
            "",
            "## Summary",
            f"- **Status:** {report.overall_status.value}",
            f"- **Date:** {report.date}",
            f"- **Platform:** {report.platform}",
            f"- **LLM:** {report.llm_model}",
            f"- **Agent ID:** {report.agent_id}",
            f"- **Startup:** {'OK' if report.startup_ok else 'FAILED'}",
            "",
        ]

        if report.scenario_results:
            lines.append("## Scenario Results")
            lines.append("")
            for sr in report.scenario_results:
                lines.append(f"### {sr.name}")
                lines.append(f"**Status:** {sr.status.value}")
                if sr.description:
                    lines.append(f"*{sr.description}*")
                lines.append("")
                if sr.error:
                    lines.append(f"> Error: {sr.error}")
                    lines.append("")
                if sr.steps:
                    lines.append(
                        "| # | Action | Expected | Actual | Status |"
                    )
                    lines.append(
                        "|---|--------|----------|--------|--------|"
                    )
                    for i, step in enumerate(sr.steps, 1):
                        actual_short = step.actual[:100].replace("\n", " ")
                        lines.append(
                            f"| {i} | {step.action} | {step.expected} | {actual_short} | {step.status.value} |"
                        )
                    lines.append("")

        room_ids = [
            (sr.name, sr.room_id)
            for sr in report.scenario_results
            if sr.room_id
        ]
        if room_ids:
            lines.append("## Chat Rooms")
            for name, rid in room_ids:
                lines.append(f"- **{name}**: `{rid}`")
            lines.append("")

        if report.errors:
            lines.append("## Errors")
            for e in report.errors:
                lines.append(f"- {e}")
            lines.append("")

        if report.warnings:
            lines.append("## Warnings")
            for w in report.warnings:
                lines.append(f"- {w}")
            lines.append("")

        if report.startup_logs:
            lines.append("## Startup Logs (excerpt)")
            lines.append("```")
            log_lines = report.startup_logs.split("\n")
            for line in log_lines[:30]:
                lines.append(line)
            if len(log_lines) > 30:
                lines.append(f"... ({len(log_lines) - 30} more lines)")
            lines.append("```")
            lines.append("")

        return "\n".join(lines)
