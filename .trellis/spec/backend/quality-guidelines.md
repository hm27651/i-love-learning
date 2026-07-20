# Quality Guidelines

> Changes must preserve local data, project isolation and both deployment modes.

## Required practices

- Target Python 3.11 and keep code compatible with Flask, Waitress and SQLite on Windows and Linux.
- Use `create_app(config)` for tests and isolated checks. Bind data, database and backup paths explicitly.
- Preserve Windows Portable and Linux Docker behavior unless the change is intentionally platform-specific.
- Keep runtime imports within deployable source modules. PyInstaller must be able to discover every runtime dependency without copying `tools/`.
- Preserve existing user changes in a dirty worktree and never use destructive Git cleanup commands.

## Verification levels

- Small backend change: compile affected modules, run Ruff on changed Python, and run focused tests.
- Domain or persistence change: run the complete unittest suite and add migration/integration coverage.
- Import/export change: test parse/validate/commit plus a real or representative round trip, duplicate handling and package safety.
- Portable change: rebuild on Windows, run `tools/release/windows/smoke_portable_windows.ps1`, verify `/health` and inspect version metadata.
- Release/public-repository change: run `tools/release/check_release_ready.py` or at minimum `tools/safety/check_public_repo.py` before staging.

## Test conventions

- Tests use `unittest`, temporary directories and Flask test clients. They must not require internet access or the user's browser.
- Assert persisted database state, not only response codes or flash text.
- Add regression tests for every fixed bug. The Portable PDF regression in `tests/test_portable_config.py` prevents runtime code from importing development tools.
- Mobile and navigation changes must preserve the 390 px UI contract and avoid horizontal scrolling.

## Forbidden patterns

- No real databases, question banks, imported documents, images, secrets or built artifacts in Git.
- No unparameterized SQL with user input.
- No runtime dependency on `tools/`, `tests/`, `build/` or `dist/`.
- No cloud sync, authentication, external CDN or Node build dependency without an explicit product decision.
- No multi-process application server or multiple background executors against one SQLite database.

## Review checklist

- Does every query and direct URL stay inside the current project?
- Are database IDs, progress and files preserved across migration or movement?
- Are background operations atomic, retryable and restart-safe?
- Does the change work with an empty database and an upgraded existing database?
- Are Portable, Docker, README and release instructions still accurate?
- Are logs and error messages useful without exposing learning content or secrets?
