# Authentication

OQTOPUS Manager supports pluggable authentication providers configured under the `auth` key in `config.yaml`.

For role-based access control, see [Permissions](permissions.md).

## Provider overview

| `provider` | Description |
|------------|-------------|
| `none` | Authentication is disabled. All requests are allowed without a user identity. Suitable for local development only. |
| `header` | Extracts user identity from a JWT carried in an HTTP header injected by a trusted reverse proxy (e.g. AWS ALB + Amazon Cognito via oauth2-proxy, or Cloudflare Access). |

## provider: none

```yaml
auth:
  provider: none
  none:
    default_account: admin_user   # account name shown in /me and debug pages
    default_roles: [admin]        # roles granted when auth is disabled
```

Authentication is disabled. A virtual user with the configured `default_account` and
`default_roles` is used so that permission checks and template flags behave identically
to a real session with those roles. The **My Account** page and user widget are hidden
from the sidebar.

### none.*

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `default_account` | string | **Yes** | — | Account name shown in the `/me` and `/debug` pages when auth is disabled. |
| `default_roles` | list of strings | **Yes** | — | Roles granted to every request when auth is disabled. Must match role names defined in `permissions`. |

## provider: header

Trusts a JWT delivered in an HTTP header set by a reverse proxy that sits in front of OQTOPUS Manager.
The proxy is responsible for authenticating the user and injecting the JWT before forwarding the request.
OQTOPUS Manager decodes the JWT to read the user's email and roles directly from claims.

### Authentication flow

#### Premise: role naming convention

OQTOPUS Manager assumes that roles in the identity provider follow an
`<application-identifier>.<role>` naming pattern, such as `oqtopus-manager.admin` or
`oqtopus-manager.operator`. A user may hold multiple roles simultaneously.

#### How OQTOPUS Manager authenticates each request

1. **JWT extraction** — read the HTTP header named by `jwt_header`. For `authorization`,
   the `Bearer` prefix is stripped automatically; for any other header name
   (e.g. `cf-access-jwt-assertion`), the value is used as-is.
   If no JWT is present, the request is rejected with `403`.

2. **User identity** — navigate the JWT payload to the path specified by `user_claim`
   (e.g. `email`) and treat the value as the user's identifier.

3. **Raw role extraction** — navigate the JWT payload to the path specified by
   `roles_claim` (e.g. `cognito:groups`) and read the value as a list of role strings.
   Both JSON arrays and comma-separated strings are accepted.

4. **`allow_raw_roles` filtering** — if `allow_raw_roles` is configured, only roles
   matching at least one glob pattern are passed to subsequent steps. Roles unrelated
   to this application (e.g. from other systems sharing the same identity provider)
   are discarded here. If no roles remain, the request is rejected with `403`.

5. **`role_mappings`** — each role is looked up in `role_mappings`. If a mapping exists,
   the display name is used (e.g. `oqtopus-manager.admin` → `admin`); otherwise the raw
   value passes through as-is.
   !!! note
       Unmapped roles pass through unchanged. If `role_mappings` is omitted entirely,
       raw role values become the effective roles.

6. **Signature verification** — if `signature_verification.enabled` is `true`, the JWT
   signature is verified against the issuer's JWKS endpoint. If verification fails,
   the request is rejected with `403`.

7. **Result** — the mapped roles and user email are attached to the request as the
   authenticated user. These are the roles OQTOPUS Manager uses for display and access
   control.

### Full configuration reference

```yaml
auth:
  provider: header
  header:
    jwt_header: authorization          # "authorization" → Bearer prefix stripped automatically
    user_claim: email                  # JWT claim for the user's email
    roles_claim: "cognito:groups"      # JWT claim for roles; list = nested path
    allow_raw_roles:                   # glob patterns on raw roles_claim values, applied
      - your-app.*                     # before role_mappings; omit to allow all
    signature_verification:
      enabled: true
      issuer: https://your-issuer-url/
      # jwks_url: https://your-jwks-url/   # omit to derive from issuer automatically
      audience: your-audience
    signout_url: https://your-proxy/oauth2/sign_out
  role_mappings:
    your-app.operator: operator
    your-app.admin: admin
```

### header.*

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `jwt_header` | string | No | `authorization` | Request header containing the JWT. For `authorization`, the `Bearer` prefix (and the following space) is stripped automatically. For any other header name (e.g. `cf-access-jwt-assertion`), the value is used as-is. |
| `user_claim` | string | No | `email` | JWT claim key for the user's email address. |
| `roles_claim` | string or list of strings | No | `cognito:groups` | JWT claim key for roles. A plain string selects a top-level key; a list of strings selects a nested path (e.g. `["custom", "cognito:groups"]`). The claim value may be a JSON array or a comma-separated string — both are handled. |
| `allow_raw_roles` | list of strings | No | *(allow all)* | Glob patterns (shell-style, using `fnmatch`) applied to each raw value from `roles_claim` **before** `role_mappings`. Values that do not match any pattern are discarded. When omitted or empty, all values are allowed. See [allow_raw_roles](#allow_raw_roles) for details. |
| `signout_url` | string | No | — | URL the **Sign out** sidebar link points to. When omitted, the Sign out link is hidden. Typically the proxy sign-out endpoint. |

### header.signature_verification.*

JWT signature verification prevents token forgery: even if an attacker injects a modified JWT header, it cannot produce a valid signature without the identity provider's private key.

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `enabled` | boolean | No | `false` | Set `true` to enable JWT signature verification. When `true`, `issuer` and `audience` are required. |
| `issuer` | string | Yes (if enabled) | — | Expected `iss` claim. Also used to derive the JWKS endpoint as `{issuer}/.well-known/jwks.json` when `jwks_url` is not set. |
| `jwks_url` | string | No | *(derived from issuer)* | Explicit JWKS endpoint URL. Use when the identity provider's JWKS endpoint is not at the standard path (e.g. Cloudflare Access: `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`). |
| `audience` | string | Yes (if enabled) | — | Expected `aud` claim in the JWT. |

!!! warning "Token expiry"
    The proxy must refresh tokens before they expire. When using oauth2-proxy, set `cookie_refresh` to a value shorter than the identity provider's token lifetime (e.g. `cookie_refresh = "50m"` for a 60-minute token). If the token expires before refresh, requests will be rejected with `JWT verification failed: Signature has expired`.

## allow_raw_roles

`allow_raw_roles` is a list of [fnmatch](https://docs.python.org/3/library/fnmatch.html) glob patterns.

| Pattern character | Meaning |
|-------------------|---------|
| `*` | Matches any string of characters (zero or more) |
| `?` | Matches any single character |
| `[seq]` | Matches any character in `seq` |

The patterns are evaluated with **OR** logic — a value is allowed if it matches **any** pattern in the list.

**Example:**

```yaml
allow_raw_roles:
  - oqtopus-manager.*
```

| Raw value | Matches? |
|-----------|----------|
| `oqtopus-manager.admin` | ✓ |
| `oqtopus-manager.operator` | ✓ |
| `other-app.admin` | ✗ (discarded before role_mappings) |

## role_mappings

Maps raw role values to display names used throughout the UI.

```yaml
auth:
  role_mappings:
    your-app.operator: operator
    your-app.admin: admin
```

| Behaviour | Description |
|-----------|-------------|
| Mapped value | Displayed as the mapped name (e.g. `admin`, `operator`). |
| Unmapped value | Passed through as-is (the raw string). |
| No match at all | The request is rejected with `403 Forbidden`. |

Role display colours in the UI:

| Role name | Colour |
|-----------|--------|
| `admin` | Amber |
| `operator` | Blue |
| anything else | Grey |

## Debug endpoint

See [enable_debug_endpoint](configuration.md#enable_debug_endpoint) in the configuration reference.

## Design notes

### Why not standard FastAPI OAuth2 scopes?

FastAPI provides `Security(dep, scopes=[...])` and `SecurityScopes` for scope-based access control,
which integrates with the OpenAPI/Swagger UI. OQTOPUS Manager deliberately does not use this pattern
for the following reasons:

**Different authorization model.** FastAPI's OAuth2 scopes are designed for flows where the
*client* requests specific scopes at login time (e.g. `scope=items:read`), and the server
verifies the JWT contains those scopes. OQTOPUS Manager uses RBAC: the *server* maps
proxy-injected roles to permissions at request time. The client has no role in choosing scopes.

**Non-standard token injection.** Tokens are injected by a trusted reverse proxy
(e.g. oauth2-proxy, Cloudflare Access), not obtained through an OAuth2 token endpoint.
FastAPI's `OAuth2PasswordBearer` and `HTTPBearer` schemes assume a specific flow (token endpoint
or `Authorization: Bearer`) that does not match custom headers such as `cf-access-jwt-assertion`.

**Consequence.** Permission enforcement uses `Depends(require_permission("scope"))` instead of
`Security(dep, scopes=["scope"])`. The application works correctly but required scopes are not
reflected in the OpenAPI documentation. This trade-off is accepted because the API is not
intended for direct third-party consumption.

## Example: Amazon Cognito with oauth2-proxy

This example shows a complete setup using Amazon Cognito as the identity provider and [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/) as the reverse proxy.

### Architecture

```text
Browser → oauth2-proxy → OQTOPUS Manager
                ↕
          Amazon Cognito
```

oauth2-proxy authenticates the user against Cognito and forwards requests to OQTOPUS Manager with an `Authorization: Bearer <id_token>` header.
OQTOPUS Manager decodes the JWT to read `email` and `cognito:groups` claims directly.

### oauth2-proxy configuration (`oauth2-proxy.cfg`)

```cfg
# Provider
provider          = "oidc"
oidc_issuer_url   = "https://cognito-idp.{region}.amazonaws.com/{user-pool-id}"
oidc_groups_claim = "cognito:groups"
scope             = "openid email"
client_id         = "{app-client-id}"
client_secret     = "{app-client-secret}"

# Network
http_address = "127.0.0.1:4180"
redirect_url = "http://127.0.0.1:4180/oauth2/callback"
upstreams    = ["http://localhost:38000"]

# Cookie
cookie_secret  = "{32-byte-random-secret}"
cookie_secure  = false   # true in production (requires HTTPS)
cookie_refresh = "50m"   # must be shorter than Cognito token expiry (default 1h)

# Headers passed to upstream
pass_authorization_header = true   # Authorization: Bearer <id_token> — required for JWT reading

# Access control
email_domains = ["*"]

# UI
skip_provider_button = true
```

Generate `cookie_secret` with the following one-liner:

```shell
python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

oauth2-proxy injects the following header into upstream requests:

| Header | Example value | Purpose |
|--------|--------------|---------|
| `authorization` | `Bearer eyJ...` | JWT containing `email` and `cognito:groups` claims |

### OQTOPUS Manager configuration (`config.yaml`)

```yaml
auth:
  provider: header
  header:
    jwt_header: authorization          # "authorization" → Bearer prefix stripped automatically
    user_claim: email                  # standard JWT claim for email
    roles_claim: "cognito:groups"      # Cognito group membership claim
    allow_raw_roles:
      - oqtopus-manager.*              # discard groups from other applications
    signature_verification:
      enabled: true                    # verify the JWT to prevent token forgery
      issuer: "https://cognito-idp.{region}.amazonaws.com/{user-pool-id}"
      audience: "{app-client-id}"
    signout_url: "http://localhost:4180/oauth2/sign_out?rd={cognito-login-url}"
  role_mappings:
    oqtopus-manager.operator: operator
    oqtopus-manager.admin: admin
```

### Cognito setup checklist

1. **User Pool** — create a Cognito User Pool.
2. **App client** — create an app client with:
   - OAuth 2.0 grant type: **Authorization code**
   - Scopes: `openid`, `email`
   - Callback URL: `http://localhost:4180/oauth2/callback` (or your production URL)
3. **Groups** — create groups named `oqtopus-manager.operator` and `oqtopus-manager.admin` and assign users.
4. **`iss` claim** — the issuer URL is `https://cognito-idp.{region}.amazonaws.com/{user-pool-id}`.
5. **`aud` claim** — the audience is the App client ID.
6. **Token expiry** — the default ID token lifetime is **1 hour**. Set `cookie_refresh = "50m"` in oauth2-proxy to ensure tokens are refreshed before they expire.

## Example: Cloudflare Access + Amazon Cognito

This example uses [Cloudflare Access](https://www.cloudflare.com/products/zero-trust/access/) as the reverse proxy and Amazon Cognito as the OIDC identity provider.

<!--- pyml disable-next-line md024 --->
### Architecture

```text
Browser → Cloudflare Access → OQTOPUS Manager
                ↕
          Amazon Cognito (OIDC)
```

Cloudflare Access authenticates users via Cognito and injects a signed JWT into every upstream request via the `cf-access-jwt-assertion` header.
OQTOPUS Manager reads `email` and Cognito group claims directly from this JWT.

### JWT claims structure

Cloudflare Access places OIDC claims forwarded from Cognito under a `custom` key in the JWT payload:

```json
{
  "iss": "https://{team}.cloudflareaccess.com",
  "aud": ["{application-aud-tag}"],
  "email": "user@example.com",
  "custom": {
    "cognito:groups": ["oqtopus-manager.admin", "oqtopus-manager.operator"]
  }
}
```

The JWKS endpoint is at a non-standard path (`/cdn-cgi/access/certs`), so `jwks_url` must be set explicitly.

### Header injected by Cloudflare Access

| Header | Example value | Purpose |
|--------|--------------|---------|
| `cf-access-jwt-assertion` | `eyJ...` | Cloudflare-signed JWT (raw value, no `Bearer` prefix) |

<!--- pyml disable-next-line md024 --->
### OQTOPUS Manager configuration (`config.yaml`)

```yaml
auth:
  provider: header
  header:
    jwt_header: cf-access-jwt-assertion       # raw JWT, no Bearer prefix
    user_claim: email
    roles_claim: ["custom", "cognito:groups"] # Cognito groups nested under "custom"
    allow_raw_roles:
      - oqtopus-manager.*
    signature_verification:
      enabled: true
      issuer: "https://{team}.cloudflareaccess.com"
      jwks_url: "https://{team}.cloudflareaccess.com/cdn-cgi/access/certs"
      audience: "{application-aud-tag}"       # shown in Cloudflare Zero Trust dashboard
    signout_url: "https://{team}.cloudflareaccess.com/cdn-cgi/access/logout"
  role_mappings:
    oqtopus-manager.operator: operator
    oqtopus-manager.admin: admin
```

### Setup checklist

1. **Cognito User Pool** — create groups named `oqtopus-manager.operator` and `oqtopus-manager.admin` and assign users.
2. **Cognito App client** — create an app client with:
   - OAuth 2.0 grant type: **Authorization code**
   - Scopes: `openid`, `email`
   - Callback URL: `https://{team}.cloudflareaccess.com/cdn-cgi/access/callback`
3. **Cloudflare Access application** — create a Self-hosted application and add an OIDC identity provider pointing to your Cognito User Pool.
4. **`issuer`** — use your team domain: `https://{team}.cloudflareaccess.com`.
5. **`audience`** — find the AUD tag at: Zero Trust → Access → Applications → *[your app]* → **Application Audience (AUD) Tag**. Alternatively, it appears as the `aud` array value in the `/debug` JWT payload.
6. **`jwks_url`** — Cloudflare Access uses a non-standard JWKS path; set it explicitly to `https://{team}.cloudflareaccess.com/cdn-cgi/access/certs`.
