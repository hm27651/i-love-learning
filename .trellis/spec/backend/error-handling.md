# Error Handling

> User-facing failures are explicit, recoverable and do not expose internals.

## HTTP handling

- Use `abort(400)` for malformed requests, `abort(404)` for missing or cross-project resources and `409` for stale device-control tokens.
- For normal form validation failures, flash a concise Chinese message and redirect back to the relevant page. Preserve the current project and safe return location.
- JSON APIs return a small JSON object plus the appropriate HTTP status. Do not return HTML tracebacks to API clients.
- The global upload-size handler redirects to the import center with an actionable message.

## Service boundaries

- Raise `ValueError` for expected validation failures such as an invalid import package, unsupported mapping or unsafe image. Routes catch it, roll back and show the message.
- Let unexpected exceptions propagate to the background job boundary. Import/export workers catch them, roll back, mark the job `failed` and store a safe error summary for retry.
- Check invariants before mutation. Knowledge deletion, project deletion and bulk movement must validate ownership and conflicts before creating backups or writing rows.
- Preserve resumability: failed or interrupted background jobs retain their source file and can be retried without partial question writes.

## Security and privacy

- Do not include database paths, stack traces, secrets, cookies, answers or question text in browser error responses.
- Reject unsafe archive paths, symlinks, unknown files, duplicate paths, excessive expansion and invalid hashes before extracting ZIP content.
- Do not silently fall back from a failed persistent-data path to a temporary or repository path.

## Examples

- `routes/imports.py` catches `ValueError` from `commit_job`, rolls back and returns the user to the import detail page.
- `services/transfer/share_package_service.py` rejects malformed or modified packages before any database commit.
- `routes/practice.py` and `routes/mock.py` return conflict responses when another device has taken control.

## Common mistakes

- Do not catch `Exception` in a route and pretend the operation succeeded.
- Do not use a flash message as the only record of a background failure; update the job state as well.
- Do not partially commit a batch and report only the rejected rows afterward.
