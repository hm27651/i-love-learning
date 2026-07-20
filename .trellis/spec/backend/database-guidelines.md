# Database Guidelines

> SQLite conventions used by the personal, single-process application.

## Connection and transaction model

- Use the standard `sqlite3` module; there is no ORM.
- Obtain connections from the injected `db()` provider. The context manager commits on success, rolls back on exceptions and always closes the connection.
- All connections must enable foreign keys, set `busy_timeout=30000` and return `sqlite3.Row`, as configured in `app_runtime.py`.
- Keep normal writes inside one database context. Explicitly call `conn.commit()` only when a background task must publish progress before continuing or before dispatching asynchronous work.
- Do not share a connection across threads. Background queues open their own connections from the configured database path.

## Queries and project isolation

- Use parameterized SQL for every user-supplied value. Dynamic SQL is allowed only for controlled fragments assembled from internal allowlists.
- Every project-scoped query must prove ownership through `learning_projects`, `subjects` or the complete knowledge hierarchy. Direct-ID routes must return `404` for cross-project objects.
- Preserve question IDs and learning progress during knowledge-tree moves. Moving is not delete-and-recreate.
- Prefer database constraints and partial unique indexes for invariants such as one active session of each kind per project.
- JSON payloads are stored as UTF-8 JSON with `ensure_ascii=False` and decoded at the service boundary.

## Schema migrations

- Schema changes belong in the versioned migration service under `services/core/migration_service.py`; update the schema version and migration tests together.
- Migrations must be idempotent, preserve IDs and content, and run inside the startup migration flow.
- Before upgrading a non-empty database, create a complete data snapshot. After migration, verify `PRAGMA integrity_check` and foreign keys.
- A fresh clone must create schema version 4 or later with zero questions, attempts, imports and exports.

## Data and file consistency

- Database rows store paths relative to the configured data directory; resolve and validate them before reading or deleting files.
- For imports and exports, validate the whole batch before committing. Do not leave partially imported questions after a validation or database failure.
- When a task writes files and database rows, clean up newly created unreferenced files if the database transaction fails.

## Common mistakes

- Never open or overwrite the real `data/h3cse.db` in tests; use `create_app(config)` with an isolated temporary data directory.
- Never run multiple application instances against the same SQLite file.
- Never place SQLite on NFS/SMB or copy a live database as a backup; stop the service or use the built-in snapshot flow.
- Never disable foreign keys to make a migration pass.
