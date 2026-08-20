"""FastAPI application factory and entry point."""

import argparse
import importlib.metadata
import logging
import pathlib

import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from oqtopus_auth.fastapi import AuthMiddleware, FastAPIPermissions
from oqtopus_util.config import load_config, setup_logging

from oqtopus_manager.config import AppConfig
from oqtopus_manager.routers import app_settings, browse, debug, me, meta
from oqtopus_manager.routers import backend as backend_pkg
from oqtopus_manager.routers import cloud_local as cloud_local_pkg
from oqtopus_manager.services.environment import check_reserved_environment_names

logger = logging.getLogger(__name__)

# Jinja2 templates directory bundled with the package
_TEMPLATES_DIR = pathlib.Path(__file__).parent / "templates"

# Maps each template type to its list of HTML-page routers (kept at
# /{template}) and its list of JSON/Server-Sent Events/download routers
# (under /api/{template}).
_TEMPLATE_ROUTERS = {
    "backend": backend_pkg.routers,
    "cloud-local": cloud_local_pkg.routers,
}
_TEMPLATE_API_ROUTERS = {
    "backend": backend_pkg.api_routers,
    "cloud-local": cloud_local_pkg.api_routers,
}


def create_app(config_path: pathlib.Path) -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        The configured FastAPI application instance.

    Raises:
        ValueError: If no environment_templates are defined in config, or if
            an unsupported template name is listed.

    """
    cfg = AppConfig.load(config_path)

    if not cfg.environment_templates:
        msg = "No environment_templates defined in config."
        raise ValueError(msg)

    unknown = set(cfg.environment_templates) - _TEMPLATE_ROUTERS.keys()
    if unknown:
        msg = f"Unsupported environment_templates: {sorted(unknown)}"
        raise ValueError(msg)

    reserved_collisions = check_reserved_environment_names(cfg)
    if reserved_collisions:
        logger.warning(
            "Environment name(s) %s collide with a reserved route segment "
            "and are unreachable through the UI. Rename them to restore "
            "access.",
            reserved_collisions,
        )

    # Initialize FastAPI and attach config/templates to app state
    app = FastAPI(
        title=cfg.app_name,
        version=importlib.metadata.version("oqtopus-manager"),
    )
    app.add_middleware(AuthMiddleware, auth_cfg=cfg.auth)
    app.state.config = cfg
    templates = Jinja2Templates(directory=_TEMPLATES_DIR)
    templates.env.globals["app_name"] = cfg.app_name
    templates.env.globals["has_app_icon"] = cfg.app_icon_path is not None
    templates.env.globals["has_favicon"] = cfg.favicon_path is not None
    templates.env.globals["environment_templates"] = cfg.environment_templates
    templates.env.globals["sidebar_links"] = cfg.sidebar_links
    app.state.templates = templates
    app.state.permissions = (
        FastAPIPermissions(cfg.role_permissions)
        if cfg.role_permissions is not None
        else None
    )

    # Serve operator-supplied assets (icons, images) from the runtime working directory
    assets_dir = pathlib.Path.cwd() / "assets"
    assets_dir.mkdir(exist_ok=True)
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    # Register only the routers for enabled template types
    for tmpl in cfg.environment_templates:
        for router in _TEMPLATE_ROUTERS.get(tmpl, []):
            app.include_router(router)
        for router in _TEMPLATE_API_ROUTERS.get(tmpl, []):
            app.include_router(router)
    app.include_router(meta.router)
    app.include_router(browse.router)
    app.include_router(app_settings.router)
    app.include_router(app_settings.api_router)
    if cfg.auth.provider != "none":
        app.include_router(me.router)
    if cfg.enable_debug_endpoint:
        app.include_router(debug.router)

    # Redirect / to the first configured template
    default_url = "/" + cfg.environment_templates[0]
    _register_routes(app, default_url)

    return app


def _register_routes(app: FastAPI, default_url: str) -> None:
    """Register the root redirect (needs default_url closure)."""

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse(url=default_url)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OQTOPUS Manager")
    parser.add_argument("-c", "--config", default="config/config.yaml")
    parser.add_argument("-l", "--logging", default="config/logging.yaml")
    args, _ = parser.parse_known_args()
    return args


if __name__ == "__main__":
    args = _parse_args()

    # Load logging config first so all subsequent output is properly formatted
    log_config_dict = load_config(args.logging)
    setup_logging(log_config_dict)

    cfg_path = pathlib.Path(args.config)
    app = create_app(cfg_path)
    cfg = app.state.config
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_config=log_config_dict)
