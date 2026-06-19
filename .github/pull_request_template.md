## PR Title Format

> **Required:** PR titles must follow Conventional Commits format:
> `type(scope): description`
>
> **Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`
>
> **Examples:**
> - `feat(agent): add event streaming support`
> - `fix(api): resolve timeout handling`
> - `docs: update integration examples`

---

## Summary

<!-- Brief description of what this PR does -->

## Changes

<!-- List the main changes in this PR -->
-

## Related Issues

<!-- Link any related issues using "Fixes #123" or "Relates to #123" -->

## Testing

<!-- Describe how you tested these changes -->
- [ ] Unit tests pass (`uv run pytest tests/ --ignore=tests/integration --ignore=tests/e2e`)
- [ ] Pre-commit checks pass (`uv run pre-commit run --all-files`)
- [ ] Integration tests pass (if applicable)

## Checklist

- [ ] PR title follows Conventional Commits format
- [ ] Code follows project style guidelines
- [ ] Tests added/updated as needed
- [ ] Documentation updated as needed
