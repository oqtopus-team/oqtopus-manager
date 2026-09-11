# Health Check

`GET /health` reports liveness and version information for a single OQTOPUS Manager
instance. It is intended for automated reachability checks.

## Response

```http
HTTP/1.1 200 OK
Content-Type: application/json
```

```json
{
  "status": "pass",
  "version": "0.1.0",
  "description": "OQTOPUS Manager"
}
```

| Field | Type | Description |
|---|---|---|
| `status` | string | Always `"pass"`. OQTOPUS Manager does not currently self-report degraded states (`warn`/`fail`); a non-`200` response or a connection failure is the signal for "not reachable". |
| `version` | string | The installed `oqtopus-manager` package version (`importlib.metadata.version("oqtopus-manager")`, exposed as `request.app.version`). |
| `description` | string | The configured `appearance.app_name` (`request.app.title`). |

`GET /health` always returns `200`. There is no permission check and no way to
disable it short of removing the route; it is intended to be always reachable
whenever the process is up.

## Authentication

`/health` is listed under `auth.public_paths` in `config/config.yaml.example`, so it
is reachable without credentials even when `auth.provider` requires them. See
[Authentication: public_paths](authentication.md#public_paths) for how that
exclusion works and why `/favicon.ico` and `/app-icon` are excluded the same way.

## Design notes

### Why draft-inadarei-api-health-check-06?

The response field names and their meaning (`status`, `version`, `description`)
follow [draft-inadarei-api-health-check-06](https://datatracker.ietf.org/doc/html/draft-inadarei-api-health-check-06),
an IETF Individual Internet-Draft for health check responses. The draft expired
without becoming an RFC, but its vocabulary has been adopted informally by a number
of health-check implementations, so reusing it costs nothing and avoids inventing
yet another ad hoc shape.

Only the vocabulary is borrowed; this endpoint does not aim for full draft
compliance. In particular:

- The media type stays `application/json`, not the draft's `application/health+json`.
  Nothing in the OQTOPUS Manager ecosystem does content negotiation on it, and
  common infrastructure (load balancers, Kubernetes probes) checks the HTTP status
  code, not the response body's content type.
- `output` and `checks` (both optional in the draft) are omitted entirely; there is
  currently nothing to report through them.
- `releaseId` (present in earlier drafts of this spec) is not used. `version` alone
  is sufficient while OQTOPUS Manager does not version its API surface separately
  from the package.
