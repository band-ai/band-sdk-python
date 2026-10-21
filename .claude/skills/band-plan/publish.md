# Attaching the plan to the ticket

Used by Phase 8 once the plan file is final, Implementation Prompt included.
The plan attaches as a real markdown file, not a Linear Document.

1. Upload the plan file as an issue attachment via `prepare_attachment_upload`
   → PUT → `create_attachment_from_upload`, with `issue=<identifier>`,
   `filename=plan-<identifier>.md`, `contentType=text/markdown`, and
   `title=Plan: <identifier> — <ticket title>`. Follow each tool's own upload
   instructions rather than re-deriving them here.
2. If Phase 0 found a prior plan attachment, `delete_attachment` it once the
   new one is linked — one plan file per ticket, never a stale duplicate.
3. `save_issue` to add a single `Plan: <attachment URL>` line to the ticket
   description, or update that line in place if a prior run added one. Leave
   the rest of the description unchanged.
