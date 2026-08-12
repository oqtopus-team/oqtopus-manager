"""Environment data model."""

from __future__ import annotations

import pathlib  # noqa: TC003
import re

from pydantic import BaseModel, field_validator

# Same pattern enforced by oqtopus-cli for environment names.
_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.:-]*$")


class Environment(BaseModel):
    """Represents a single OQTOPUS backend environment."""

    name: str
    template: str
    root_path: pathlib.Path | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate that the name matches the allowed pattern.

        Returns:
            The validated name string.

        Raises:
            ValueError: If the name does not match the required pattern.

        """
        if not _NAME_PATTERN.match(v):
            msg = (
                f"Invalid environment name '{v}'. "
                "Must match ^[a-z0-9][a-z0-9_.:-]*$ "
                "(lowercase letters, digits, hyphens, underscores, dots, colons only)."
            )
            raise ValueError(msg)
        return v

    def resolved_root_path(self, base_path: pathlib.Path) -> pathlib.Path:
        """Return the effective root path, falling back to base_path/name.

        Returns:
            The resolved root path for this environment.

        """
        return self.root_path or base_path / self.name
