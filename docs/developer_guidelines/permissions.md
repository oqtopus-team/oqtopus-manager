# Permissions API

This page is the API reference for the permission and role classes in the `auth` package.
For design philosophy, mechanics, and implementation guidance, see
[Access Control](access_control.md).

## Overview

The auth package provides two independent sets of classes and functions:

| Class / Function | Package | Approach | Requires `permissions:` config |
|---|---|---|---|
| `Permissions` | `auth` | Permission-based | Yes |
| `FastAPIPermissions` | `auth.fastapi` | Permission-based | Yes |
| `require_permission()` | `auth.fastapi` | Permission-based | Yes |
| `FastAPIRoles` | `auth.fastapi` | Role-based | No |
| `require_roles()` | `auth.fastapi` | Role-based | No |

## Permissions

Defined in `auth/permissions.py`. No FastAPI dependency.

```python
from oqtopus_auth import Permissions

permissions = Permissions(role_permissions)

# Check a permission in a route handler or template context
can_edit = permissions.has_permission(user, "app_settings.update")
```

### Constructor

```python
Permissions(role_permissions: dict[str, frozenset[str]])
```

Pass the resolved role-to-permission mapping produced by `parse_role_permissions()`.

### Methods

| Method | Description |
|---|---|
| `has_permission(user, permission)` | Returns `True` if the user holds any role that grants the permission. |

## FastAPIPermissions

Defined in `auth/fastapi/depends.py`. Inherits `Permissions` and adds
the `require()` dependency factory for FastAPI routes.

```python
from oqtopus_auth.fastapi import FastAPIPermissions

permissions = FastAPIPermissions(role_permissions)

# Use as a route dependency
@router.get("/settings", dependencies=[permissions.require("app_settings.get")])
async def settings_page(request: Request) -> HTMLResponse:
    can_edit = permissions.has_permission(request.state.user, "app_settings.update")
```

### Additional method

| Method | Description |
|---|---|
| `require(permission)` | Returns a `Depends` instance that raises `403` if the user lacks the permission. |

## Usage in OQTOPUS Manager

`FastAPIPermissions` is instantiated once in `main.py` and stored in
`app.state.permissions`:

```python
app.state.permissions = FastAPIPermissions(cfg.role_permissions)
```

Route files use `require_permission()` (a convenience function that reads from
`app.state.permissions`) because route decorators run at import time — before
the `FastAPIPermissions` instance is constructed.

```python
from oqtopus_auth.fastapi import require_permission

@router.get("", dependencies=[require_permission("environment.get")])
async def list_environments(request: Request) -> HTMLResponse:
    ...
```

## FastAPIRoles

Defined in `auth/fastapi/depends.py`. No configuration required — roles are read
directly from the authenticated user.

```python
from oqtopus_auth.fastapi import FastAPIRoles

roles = FastAPIRoles()

@router.get("/admin", dependencies=[roles.require("admin")])
async def admin_page(request: Request) -> HTMLResponse:
    ...

# Multiple roles: pass if the user holds ANY of the specified roles (OR logic)
@router.get("/ops", dependencies=[roles.require("admin", "operator")])
async def ops_page(request: Request) -> HTMLResponse:
    ...
```

### Method

| Method | Description |
|---|---|
| `require(*roles)` | Returns a `Depends` instance that raises `403` if the user holds none of the roles. OR logic: access granted when the user holds **at least one** of the specified roles. |

## require_roles()

Standalone convenience function equivalent to `FastAPIRoles.require()`.
Preferred for route decorators because it reads naturally.

```python
from oqtopus_auth.fastapi import require_roles

@router.get("/admin", dependencies=[require_roles("admin")])
async def admin_page(request: Request) -> HTMLResponse:
    ...

# Multiple roles with OR logic
@router.get("/ops", dependencies=[require_roles("admin", "operator")])
async def ops_page(request: Request) -> HTMLResponse:
    ...
```

## Producing role_permissions

Use `parse_role_permissions()` from `auth.permissions` to convert the raw
`permissions:` section of `config.yaml` into the resolved mapping:

```python
from oqtopus_auth import parse_role_permissions

role_permissions = parse_role_permissions(raw["permissions"])
# → dict[str, frozenset[str]]
```
