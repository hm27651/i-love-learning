# Backend Development Guidelines

> Project-specific conventions for the Flask, SQLite, Portable and Docker application.

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Directory Structure](./directory-structure.md) | Runtime module boundaries and placement rules | Ready |
| [Database Guidelines](./database-guidelines.md) | SQLite access, migrations and data integrity | Ready |
| [Error Handling](./error-handling.md) | HTTP, service and background-job failures | Ready |
| [Quality Guidelines](./quality-guidelines.md) | Required checks and forbidden patterns | Ready |
| [Logging Guidelines](./logging-guidelines.md) | Local diagnostics and sensitive-data rules | Ready |

## Read first

Before changing code, also read the domain language in `CONTEXT.md` and the architectural decisions in `docs/decisions.md`. Use the guides above according to the affected layer; database and quality rules apply to every persistence change.

The public repository must remain free of real question-bank content and local learning data.
