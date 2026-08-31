# Error Handling

## Pydantic ValidationError

Catch `pydantic.ValidationError` separately from generic `Exception` (see
[Coding Standards](../AGENTS.md#coding-standards)). Beyond that:

- Format validation errors for LLM readability: `"Invalid arguments for tool_name: field: message"`
- Handle ValidationError at the lowest common point to avoid duplication
- Log full error details but return concise messages to LLM

Example:
```python notest
from pydantic import ValidationError

try:
    result = Model(**data)
except ValidationError as e:
    # Log full details for debugging
    logger.error(f"Validation failed: {e}")
    # Return concise message for LLM
    errors = "; ".join(f"{err['loc'][0]}: {err['msg']}" for err in e.errors())
    return f"Invalid arguments for {tool_name}: {errors}"
except Exception as e:
    logger.exception(f"Unexpected error: {e}")
    raise
```

## Exception Hierarchy

- Use specific exceptions over generic ones
- Create custom exception classes for domain-specific errors
- Always include context in exception messages

## Error Messages

- Make error messages actionable and clear
- Include relevant context (what failed, why, what to do)
- Avoid exposing internal implementation details to end users

## Required Configuration

`raise ValueError(...)` for missing required config, not `logger.error()` +
`sys.exit()` (see [Coding Standards](../AGENTS.md#coding-standards)) — fail
fast with a clear message:
```python notest
# Good
if not api_key:
    raise ValueError("OPENAI_API_KEY environment variable is required")

# Bad
if not api_key:
    logger.error("Missing API key")
    sys.exit(1)
```
