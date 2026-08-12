# Access Control

OQTOPUS Manager supports two approaches to access control.
Choose one based on how much granularity your application needs.

| | Role-based | Permission-based |
|---|---|---|
| `permissions:` in config.yaml | Not required | Required |
| Check granularity | Per role | Per operation |
| Config change to adjust who can do what | Not possible (code change required) | Yes |
| Good for | Simple tools, prototypes | Applications with many operations and multiple roles |

## Design philosophy

### Role-based access control

The route handler checks whether the authenticated user holds a specific role
(e.g. `"admin"`).

```text
JWT / config → user.roles → require_roles("admin") → allow or 403
```

**Advantages:** No configuration needed. Easy to understand and implement.

**Limitation:** Adding a new operation that only some roles should access requires
a code change at every check site.
Redistributing capabilities across roles also requires code changes.

### Permission-based access control

Each route declares a stable permission string (e.g. `"environment.get"`).
Which roles grant that permission is defined in `config.yaml`, not in the code.

```text
config.yaml permissions: → parse_role_permissions() → FastAPIPermissions
JWT / config → user.roles → has_permission("environment.get") → allow or 403
```

**Advantages:** Check sites are stable. Changing who can do what is a config
change only — no code change required.

**Limitation:** Requires a `permissions:` section in `config.yaml`.
Adding a new permission string requires a code change, but redistributing
existing permissions across roles does not.

## How it works

### Authentication (shared by both approaches)

`AuthMiddleware` runs on every request and places the authenticated user in
`request.state.user` as an `AuthUser` instance:

```python
@dataclass
class AuthUser:
    account: str
    roles: list[str]
```

Roles are populated from the JWT claim specified by `roles_claim` in `config.yaml`,
mapped through `role_mappings` if present.
When `provider: none` is used, roles come from `default_roles`.

### Role-based: how `require_roles` works

`require_roles(role)` returns a FastAPI `Depends` that:

1. Reads `user` from `CurrentUser` (i.e. `request.state.user`).
2. Checks `role in user.roles`.
3. Raises `HTTPException(403)` if the check fails.

No application state is involved. `FastAPIRoles.require(role)` is equivalent.

### Permission-based: how `require_permission` works

At startup, `main.py` parses `config.yaml` and stores a `FastAPIPermissions`
instance in `app.state.permissions`:

```python
app.state.permissions = FastAPIPermissions(cfg.role_permissions)
```

`require_permission(permission)` returns a FastAPI `Depends` that:

1. Reads `app.state.permissions` from the application state.
2. Raises `RuntimeError` (→ HTTP 500) if it is `None`
   (i.e. `permissions:` is missing from `config.yaml`).
3. Calls `permissions.has_permission(user, permission)`.
4. Raises `HTTPException(403)` if the check fails.

`has_permission` iterates `user.roles`, looks up each role in the resolved
mapping, and returns `True` if any role grants the permission or holds `"*"`.

## Implementation

### Role-based

Omit `permissions:` from `config.yaml` (or leave it out entirely) and use
`require_roles()` on each route.

#### Complete router example

```python
"""Routes for widget management — role-based access control."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from oqtopus_auth.fastapi import require_roles
from oqtopus_manager.routers._utils import _get_templates

router = APIRouter(prefix="/widgets", tags=["widgets"])


# Any authenticated operator or admin can list widgets
@router.get(
    "",
    response_class=HTMLResponse,
    dependencies=[require_roles("operator")],
)
async def list_widgets(request: Request) -> HTMLResponse:
    user = request.state.user
    # Pass role information to the template for conditional rendering
    can_delete = "admin" in user.roles
    return _get_templates(request).TemplateResponse(
        request,
        "widgets/list.html",
        {"can_delete": can_delete},
    )


# Only admin can create a widget
@router.get(
    "/new",
    response_class=HTMLResponse,
    dependencies=[require_roles("admin")],
)
async def new_widget_form(request: Request) -> HTMLResponse:
    return _get_templates(request).TemplateResponse(
        request, "widgets/new.html", {}
    )


@router.post(
    "",
    response_class=JSONResponse,
    dependencies=[require_roles("admin")],
)
async def create_widget(request: Request, name: str = Form(...)) -> JSONResponse:
    # create logic here
    return JSONResponse({"ok": True})


# Only admin can delete a widget
@router.delete(
    "/{widget_id}",
    response_class=JSONResponse,
    dependencies=[require_roles("admin")],
)
async def delete_widget(request: Request, widget_id: str) -> JSONResponse:
    # delete logic here
    return JSONResponse({"ok": True})
```

#### Jinja2 templates (optional)

If you use Jinja2 templates, you can check roles directly in the template or
pass pre-computed flags from the route handler.

Directly in the template:

```html+jinja
{% if request.state.user and "admin" in request.state.user.roles %}
  <button>Admin action</button>
{% endif %}
```

Alternatively, pre-compute a boolean in the route handler (e.g. `can_delete`)
and pass it to the template context. This keeps business logic out of the
presentation layer.

---

### Permission-based

Add `permissions:` to `config.yaml` and use `require_permission()` on each route.

#### config.yaml

```yaml
permissions:
  _extends_:
    admin: operator       # admin inherits all operator permissions

  operator:
    - widget.get
    - widget.create

  admin:
    - widget.delete       # admin-only
```

`_extends_` supports single-level inheritance.
The child role receives all of its own permissions plus those of the parent role.

#### Router example

```python
"""Routes for widget management — permission-based access control."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from oqtopus_auth.fastapi import require_permission
from oqtopus_manager.routers._utils import _get_templates

router = APIRouter(prefix="/widgets", tags=["widgets"])


@router.get(
    "",
    response_class=HTMLResponse,
    dependencies=[require_permission("widget.get")],
)
async def list_widgets(request: Request) -> HTMLResponse:
    # Pre-compute UI flags based on permissions — keep logic out of templates
    can_delete = request.app.state.permissions.has_permission(
        request.state.user, "widget.delete"
    )
    return _get_templates(request).TemplateResponse(
        request,
        "widgets/list.html",
        {"can_delete": can_delete},
    )


@router.get(
    "/new",
    response_class=HTMLResponse,
    dependencies=[require_permission("widget.create")],
)
async def new_widget_form(request: Request) -> HTMLResponse:
    return _get_templates(request).TemplateResponse(
        request, "widgets/new.html", {}
    )


@router.post(
    "",
    response_class=JSONResponse,
    dependencies=[require_permission("widget.create")],
)
async def create_widget(request: Request, name: str = Form(...)) -> JSONResponse:
    # create logic here
    return JSONResponse({"ok": True})


@router.delete(
    "/{widget_id}",
    response_class=JSONResponse,
    dependencies=[require_permission("widget.delete")],
)
async def delete_widget(request: Request, widget_id: str) -> JSONResponse:
    # delete logic here
    return JSONResponse({"ok": True})
```

`require_permission` reads `app.state.permissions` at request time, so it works
correctly even when the route decorator is evaluated before `FastAPIPermissions`
is constructed (which is always the case in an application factory pattern).

<!--- pyml disable-next-line md024 --->
#### Jinja2 templates (optional)

If you use Jinja2 templates, pass pre-computed flags from the route handler
and check them in the template:

```html+jinja
{% if can_delete %}
  <button>Delete</button>
{% endif %}
```

You can also call `has_permission` directly in the template via the request
object, but pre-computing in the route handler is cleaner.

#### Permission naming conventions

Follow the `<resource>.<action>` or `<resource>.<sub-resource>.<action>` format.

| Verb | Meaning |
|---|---|
| `get` | Read (list and detail combined) |
| `create` | Create a new resource |
| `update` | Modify an existing resource |
| `delete` | Remove a resource |
| `manage` | Operational control of a sub-resource |

**Examples:** `environment.get`, `environment.config.update`, `environment.service.manage`

## Choosing between the two approaches

Use **role-based** when:

- The application has few operations and a simple role model.
- You are prototyping and want to defer access control design.
- Fine-grained per-operation control is not required.

Use **permission-based** when:

- Different roles need different subsets of a large operation set.
- You want to change access rules without a code deployment
  (only a config change).
- You are building on top of OQTOPUS Manager's existing route structure,
  which already uses `require_permission()` throughout.

If you expect to need permission-based control later, start with it from the
beginning. Migrating from role-based to permission-based requires touching every
check site.

## API reference

See [Permissions API](permissions.md) for the full class and function reference.
