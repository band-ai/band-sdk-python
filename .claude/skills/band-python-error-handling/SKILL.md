---
name: band-python-error-handling
description: Band Python SDK error handling. Use when raising or handling exceptions or formatting validation errors.
paths:
  - "**/*.py"
---

# Error Handling

Beyond the `ValidationError`/`ValueError` rules already in the
`band-python-conventions` skill, format tool validation errors with `format_tool_validation_error`
(`src/band/runtime/tools/schema.py`) or `format_validation_error`
(`src/band/runtime/custom_tools.py`) rather than hand-rolling the message. Who
acts on each exception class is in `src/band/core/exceptions.py`.
