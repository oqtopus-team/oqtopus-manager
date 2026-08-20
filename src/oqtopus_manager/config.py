"""Application configuration loader."""

import pathlib

import yaml
from oqtopus_auth import (
    AuthConfig,
    parse_auth_config,
    parse_role_permissions,
)
from oqtopus_util.config import load_config
from pydantic import BaseModel

from oqtopus_manager.models.environment import Environment


class SidebarLink(BaseModel):
    """A single external link shown in the sidebar LINKS section."""

    label: str
    url: str


class AppConfig(BaseModel):
    """Top-level application configuration."""

    config_path: pathlib.Path
    default_environment_base_path: pathlib.Path
    environments_file: pathlib.Path
    host: str
    port: int
    log_tail_lines: int
    log_buffer_lines: int
    app_name: str
    app_icon_path: pathlib.Path | None
    favicon_path: pathlib.Path | None
    file_edit_lock_timeout_sec: int
    oqtopus_cli_timeout_sec: int
    environment_templates: list[str]
    sidebar_links: list[SidebarLink] = []
    auth: AuthConfig = AuthConfig()
    enable_debug_endpoint: bool
    role_permissions: dict[str, frozenset[str]] | None = None

    @classmethod
    def load(cls, config_path: pathlib.Path) -> AppConfig:
        """Load AppConfig from a YAML file.

        Relative paths in the config are resolved against the current working
        directory so that paths behave predictably regardless of where the
        config file itself lives.

        Returns:
            The loaded AppConfig instance.

        """
        raw = load_config(str(config_path))

        cwd = pathlib.Path.cwd()
        server = raw.get("server", {})
        behavior = raw.get("behavior", {})
        appearance = raw.get("appearance", {})
        auth = parse_auth_config(raw.get("auth", {}))
        default_base = cwd / pathlib.Path(server["default_environment_base_path"])
        environments_file = cwd / pathlib.Path(server["environments_file"])

        return cls(
            config_path=config_path.resolve(),
            enable_debug_endpoint=bool(raw["enable_debug_endpoint"]),
            default_environment_base_path=default_base.resolve(),
            environments_file=environments_file.resolve(),
            host=server["host"],
            port=server["port"],
            log_tail_lines=behavior["log_tail_lines"],
            log_buffer_lines=behavior["log_buffer_lines"],
            app_name=appearance["app_name"],
            app_icon_path=(
                (cwd / pathlib.Path(appearance["app_icon_path"])).resolve()
                if appearance.get("app_icon_path")
                else None
            ),
            favicon_path=(
                (cwd / pathlib.Path(appearance["favicon_path"])).resolve()
                if appearance.get("favicon_path")
                else None
            ),
            file_edit_lock_timeout_sec=behavior["file_edit_lock_timeout_sec"],
            oqtopus_cli_timeout_sec=behavior.get("oqtopus_cli_timeout_sec", 10),
            environment_templates=appearance["environment_templates"],
            sidebar_links=[
                SidebarLink(**item) for item in (appearance.get("sidebar_links") or [])
            ],
            auth=auth,
            role_permissions=(
                parse_role_permissions(raw["permissions"])
                if "permissions" in raw
                else None
            ),
        )

    def load_environments(self) -> list[Environment]:
        """Load environments from the environments YAML file.

        Returns an empty list if the file does not exist yet.

        Returns:
            List of Environment instances loaded from the YAML file.

        """
        if not self.environments_file.exists():
            return []
        raw = load_config(str(self.environments_file)) or {}
        return [Environment(**e) for e in raw.get("environments", [])]

    def save_environments(self, environments: list[Environment]) -> None:
        """Persist environments to the environments YAML file."""
        data = {
            "environments": [
                {
                    k: str(v) if isinstance(v, pathlib.Path) else v
                    for k, v in e.model_dump(exclude_none=True).items()
                }
                for e in environments
            ]
        }
        self.environments_file.parent.mkdir(parents=True, exist_ok=True)
        with self.environments_file.open("w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
