# Configuration Reference

OQTOPUS Manager is configured via `config/config.yaml`.
On first run, copy the example file and edit it:

```shell
cp config/config.yaml.example config/config.yaml
```

!!! note
    `config/config.yaml` is excluded from version control (`.gitignore`).
    `config/config.yaml.example` is the template committed to the repository.

The following sections describe the configuration options for each section.

---

## server

Controls the HTTP server binding and the locations of environment data files.

```yaml
server:
  host: localhost
  port: 38000
  default_environment_base_path: ./environments
  environments_file: ./config/environments.yaml
```

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `host` | string | **Yes** | — | Address the HTTP server listens on. Use `0.0.0.0` to accept remote connections. |
| `port` | integer | **Yes** | — | TCP port the HTTP server listens on. |
| `default_environment_base_path` | path | **Yes** | — | Directory where environment folders are stored when `root_path` is not specified per-environment. Relative paths are resolved from the working directory. |
| `environments_file` | path | **Yes** | — | YAML file that stores the list of registered environments. Created automatically on first use. |

---

## behavior

Tuning knobs for buffer sizes, timeouts, and feature behaviour.

```yaml
behavior:
  log_tail_lines: 100
  log_buffer_lines: 1000
  file_edit_lock_timeout_sec: 600
```

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `log_tail_lines` | integer | **Yes** | — | Number of lines fetched from the end of a log file when the log page first loads. |
| `log_buffer_lines` | integer | **Yes** | — | Maximum number of lines kept in the browser-side log buffer during live streaming. Older lines are discarded as new ones arrive. |
| `file_edit_lock_timeout_sec` | integer | **Yes** | — | Seconds before an idle edit lock expires and is released automatically. Prevents files from remaining locked when a browser tab is closed mid-edit. |

---

## appearance

Branding, sidebar content, and which environment template types are shown.

```yaml
appearance:
  app_name: OQTOPUS Manager
  app_icon_path: ./assets/favicon.svg
  favicon_path: ./assets/favicon.svg
  environment_templates:
    - cloud-local
    - backend
  sidebar_links:
    - label: Documentation
      url: https://your-docs-url/
    - label: OQTOPUS Cloud
      url: https://your-cloud-url/
```

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `app_name` | string | **Yes** | — | Application name displayed in the browser title and sidebar header. |
| `app_icon_path` | path | No | — | Path to the SVG or image used as the sidebar logo. Served at `/app-icon`. |
| `favicon_path` | path | No | — | Path to the favicon image. Served at `/favicon.ico`. |
| `environment_templates` | list | **Yes** | — | Ordered list of template types to show in the sidebar. The first entry is the default redirect target from `/`. Supported values: `backend`, `cloud-local`. |
| `sidebar_links` | list | No | `[]` | External links shown at the bottom of the sidebar. Each entry has `label` (string) and `url` (string). |

---

## auth

Authentication configuration. See [Authentication](authentication.md) for the full reference and provider-specific examples.

```yaml
auth:
  provider: none   # disable authentication (default)
```

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `provider` | string | No | `none` | Authentication provider. `none` disables auth; `header` reads identity from HTTP headers set by a reverse proxy. |

### auth.none

Active when `provider: none`. Configures the virtual user used for permission checks when authentication is disabled.

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `default_account` | string | **Yes** | — | Account name shown in `/me` and `/debug` pages. |
| `default_roles` | list of strings | **Yes** | — | Roles granted to every request. Must match names defined in `permissions`. |

---

## permissions

Role-to-permission mapping. See [Permissions](permissions.md) for the full reference.

```yaml
permissions:
  _extends_:
    admin: operator        # admin inherits all operator permissions

  operator:
    - environment.get
    - app_settings.get
    # ... (full list in permissions.md)
  admin:
    - app_settings.update  # additions only
```

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `_extends_` | mapping | No | Single-level role inheritance. `admin: operator` means admin automatically receives all of operator's permissions in addition to its own, avoiding duplicate entries. Each role lists only the permissions it adds beyond its parent. |
| `<role>` | list of strings | **Yes** | Permission strings granted to this role. When `_extends_` is set, list only the additions; inherited permissions are merged automatically. |

---

## enable_debug_endpoint

Enables the `/debug` route, which displays request headers, decoded JWT payload, and mapped roles.

```yaml
enable_debug_endpoint: false
```

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `enable_debug_endpoint` | boolean | **Yes** | — | Set `true` to enable the `/debug` page. **Never enable in production.** |

!!! warning
    `/debug` exposes all request headers, including authentication tokens.
    Only enable it in trusted, development environments.
