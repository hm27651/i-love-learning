# Logging Guidelines

> Runtime diagnostics use Python logging with small rotating local files.

## Destinations and format

- The Flask application writes `data/logs/app.log` through `RotatingFileHandler`, 1 MB per file with five backups.
- The Windows launcher writes `data/logs/launcher.log` with the same bounded retention model.
- Use the existing timestamp, level and message format. Do not add a second logging framework or remote telemetry service.
- Docker logs also go to stdout/stderr and are capped by Compose rotation settings.

## Levels

- `INFO`: startup, schema migration summary, service health transitions and major job lifecycle events.
- `WARNING`: recoverable degradation, interrupted jobs, missing optional files and retryable conditions.
- `ERROR`: failed operations, failed health checks and HTTP 5xx responses.
- Include `exc_info=True` or `logger.exception` only at trusted diagnostic boundaries where the local log needs a traceback.

## Required diagnostic context

- Log job or session identifiers, stage and outcome rather than full payloads.
- Portable startup logs should include runtime path, selected host/mode, port, bridge readiness and child-process health.
- Migration logs should include schema version before/after, integrity result and foreign-key error count.

## Sensitive content

- Never log question stems, options, answers, explanations or imported document text.
- Never log `SECRET_KEY`, `.secret_key`, cookies, CSRF tokens, GitHub credentials or environment secrets.
- Avoid absolute personal paths in routine messages when a data-relative path or job ID is sufficient.

## Lifecycle

- Tests and temporary application factories must call `close_file_logging` before deleting their temporary data directory on Windows.
- Keep logging non-blocking and bounded; logging failure must not corrupt learning data or fail a valid answer submission.
