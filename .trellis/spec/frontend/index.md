# Frontend Development Guidelines

> Project-specific conventions for Jinja templates and dependency-free CSS/JavaScript.

## Guidelines Index

| Guide | Description | Status |
|---|---|---|
| [UI System](./ui-system.md) | Semantic tokens, component hierarchy, navigation, accessibility and responsive contracts | Ready |

## Pre-Development Checklist

- Read `ui-system.md` before changing templates or files under `static/`.
- Search existing template classes and JavaScript hooks before adding a new component.
- Preserve route URLs, form names, element IDs and `data-*` hooks used by existing scripts and tests.
- Confirm the change works in light, dark and system themes without external assets.

## Quality Check

- Run the full unittest suite and `tools/safety/check_public_repo.py`.
- Test critical pages at 1440px and 390px; no primary workflow may require horizontal scrolling.
- Verify keyboard focus, 44px touch targets, `prefers-reduced-motion` and WCAG AA contrast.
- For Portable-facing changes, rebuild and run the Windows Portable smoke test.
