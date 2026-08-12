# Permissions

## Overview

OQTOPUS Manager supports two access control approaches.
The default setup uses **permission-based access control (PBAC)**, but the `permissions:` section
in `config.yaml` is optional — applications can use **role-based access control (RBAC)** instead.

| Approach | `permissions:` in config.yaml | Control granularity |
|---|---|---|
| Permission-based (default) | Required | Per operation (`"environment.get"`) |
| Role-based | Not required | Per role (`"admin"`) |

### Permission-based access control

1. Each user holds one or more **roles** (e.g. `operator`, `admin`).
2. Each role is mapped to a set of **permissions** (e.g. `environment.get`, `app_settings.update`).
3. Each API endpoint declares which permission it requires.

This two-level indirection (role → permission) decouples "who the user is" from "what they can do".
Changing which roles have which permissions requires only a config change, not a code change.

### Role-based access control

When `permissions:` is omitted from `config.yaml`, each route checks role membership directly.
This is simpler but less flexible: redistributing capabilities across roles requires code changes.

For implementation details and a full comparison, see
[Developer Guidelines — Access Control](../developer_guidelines/access_control.md).

## Design conventions

### Verb vocabulary

| Verb | Meaning | Examples |
|------|---------|---------|
| `get` | Read (list and detail combined) | view list, view detail page |
| `create` | Create a new resource | new form, submit create, init stream |
| `update` | Modify an existing resource | save edited file, change settings |
| `delete` | Remove a resource | delete environment |
| `manage` | Operational control of a sub-resource | start/stop services, install components |

### Format

```text
<resource>.<action>
<resource>.<sub-resource>.<action>
```

**Examples:** `environment.get`, `environment.config.update`, `environment.service.manage`

## Permission list

Each permission string follows the `<resource>.<action>` format described above.
The table below lists all permissions **hardcoded in the application**.
Adding or removing a permission requires a code change.

| Permission | Description |
|---|---|
| `environment.get` | View environment list, detail, settings, and component versions |
| `environment.create` | Create a new environment (form, submit, init stream) |
| `environment.delete` | Delete an environment |
| `environment.config.get` | View `.env`, service config, and topology JSON files |
| `environment.config.update` | Edit and save configuration files |
| `environment.log.get` | View, stream, and download service logs |
| `environment.component.manage` | Manage components: install, uninstall, update, versions, build |
| `environment.service.manage` | Control services: start, stop, restart, status, info |
| `app_settings.get` | View OQTOPUS Manager configuration |
| `app_settings.update` | Edit OQTOPUS Manager configuration *(admin only)* |

## Role mapping

The table below shows the **recommended default mapping** provided in `config/config.yaml.example`.
This is not fixed — you can freely change which roles exist and which permissions each role holds
by editing the `permissions:` section of your `config.yaml`
(see [Configuration — permissions](configuration.md#permissions)).

`admin` is configured as a strict superset of `operator`:
every permission `operator` holds is also held by `admin`, plus `admin` gains
`app_settings.update` to manage the application itself.

| Permission | operator | admin |
|---|:---:|:---:|
| `environment.get` | ✓ | ✓ |
| `environment.create` | ✓ | ✓ |
| `environment.delete` | ✓ | ✓ |
| `environment.config.get` | ✓ | ✓ |
| `environment.config.update` | ✓ | ✓ |
| `environment.log.get` | ✓ | ✓ |
| `environment.service.manage` | ✓ | ✓ |
| `environment.component.manage` | ✓ | ✓ |
| `app_settings.get` | ✓ | ✓ |
| `app_settings.update` | | ✓ |

`admin` is a superset of `operator` — every permission held by `operator` is also held by `admin`.

## Endpoints without permissions

Two endpoints intentionally have no `require_permission()` dependency:

| Endpoint | Reason |
|---|---|
| `GET /me` | Shows the authenticated user's own account information. Any authenticated user may view their own identity — no additional permission is needed. The route is only registered when `provider != "none"`, so unauthenticated access is already prevented. |
| `GET /debug` | Already gated by the `enable_debug_endpoint: true` configuration flag. Enabling this flag is a conscious administrative decision, so adding a permission check would create redundant double management. It is a development-only tool and is never registered in production unless explicitly configured. |

## Implementation

### Permission-based

Role-to-permission mappings are defined in `config.yaml` under the `permissions:` key
(see [Configuration — permissions](configuration.md#permissions)).

The application reads this configuration at startup and uses the following components to enforce it:

- `auth/permissions.py` — provides `parse_role_permissions()` to parse the config and `Permissions` to check them
- `auth/fastapi/depends.py` — provides `FastAPIPermissions` and `require_permission()` to enforce permissions on each route

### Role-based

When `permissions:` is omitted from `config.yaml`, `require_roles()` is used instead of
`require_permission()` on each route. No mapping configuration is needed — roles come directly
from the authenticated user.

### Authentication disabled

When authentication is disabled (`provider: none`), a virtual user is created with the roles
defined in `default_roles`. If `permissions:` is configured, those roles are resolved against
the mapping as usual.

For full implementation guidance, see
[Developer Guidelines — Access Control](../developer_guidelines/access_control.md) and
[Developer Guidelines — Permissions API](../developer_guidelines/permissions.md).
