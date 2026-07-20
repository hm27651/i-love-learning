# Directory Structure

> Actual module boundaries for the I Love Learning Flask application.

## Runtime layout

```text
app.py                    Flask application factory and route registration
app_runtime.py            Runtime primitives: SQLite, logging, executor, secret
routes/                   HTTP handlers and request/response orchestration
services/core/            Shared runtime, project, migration, storage services
services/questions/       Question parsing and question-domain operations
services/knowledge/       Knowledge-tree queries and structural migration
services/imports/         Runtime import parsing, validation and background jobs
services/transfer/        ZIP share-package import/export services
templates/                Jinja templates
static/                   Native CSS and JavaScript
tests/                    unittest integration, migration and contract tests
tools/                    Development, release and one-off importer utilities
packaging/windows/        PyInstaller and Portable package metadata
deploy/linux/             Docker Compose deployment configuration
```

## Module boundaries

- Keep `app.py` focused on `create_app(config)`, application-wide hooks and registration. Do not move feature business logic back into it.
- Put request parsing, redirects, flashes and HTTP status decisions in `routes/`. Blueprints must obtain runtime dependencies through `services.core.runtime_service`; route modules must not import the global `app` object.
- Put reusable business rules and persistence operations in the matching `services/` package. A runtime service must never import from `tools/`; Portable and Docker builds intentionally exclude development utilities.
- Keep import and export background work in `services/imports/` and `services/transfer/`. Both queues share the single application executor because SQLite and the current job recovery model assume one worker process.
- Keep Jinja and native browser assets dependency-free. The project does not use Node, a front-end framework or a separate API application.

## Naming and examples

- Python modules and functions use `snake_case`; blueprints are exported as `bp`.
- Feature route modules use domain names such as `routes/practice.py`, `routes/mock.py` and `routes/imports.py`.
- Service modules end in `_service.py` when they expose a domain service; a focused parser may use `_parser.py`, as in `services/imports/pdf_question_parser.py`.
- Prefer the application-factory pattern in `app.py` and provider binding in `services/core/runtime_service.py` when adding isolated runtime state.
- Use `services/transfer/export_service.py` as the reference for a background workflow and `routes/questions.py` as the reference for thin HTTP orchestration.

## Forbidden placements

- Do not add user data, databases, imported documents or generated packages to source directories.
- Do not make runtime code depend on `tools/`, `tests/`, `build/` or `dist/`.
- Do not create a second Flask application, a second SQLite writer process or a second front-end build system for a feature that fits the existing monolith.
