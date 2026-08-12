"""Tests for the Environment model."""

import pathlib

import pytest
from pydantic import ValidationError

from oqtopus_manager.models.environment import Environment

BASE = pathlib.Path("environments")


def test_resolved_root_path_defaults_to_base_plus_name() -> None:
    env = Environment(name="dev", template="backend")
    assert env.resolved_root_path(BASE) == BASE / "dev"


def test_resolved_root_path_uses_explicit_path() -> None:
    custom = pathlib.Path("custom/dev")
    env = Environment(name="dev", template="backend", root_path=custom)
    assert env.resolved_root_path(BASE) == custom


def test_root_path_is_optional() -> None:
    env = Environment(name="demo", template="backend")
    assert env.root_path is None


def test_environment_requires_name_and_template() -> None:
    with pytest.raises(Exception):
        Environment(template="backend")  # type: ignore[call-arg]


@pytest.mark.parametrize("name", ["my-demo", "oqtopus1", "demo.env", "a1_b2"])
def test_valid_environment_names(name: str) -> None:
    env = Environment(name=name, template="backend")
    assert env.name == name


@pytest.mark.parametrize("name", ["MyEnv", "my env", "-demo", ""])
def test_invalid_environment_names(name: str) -> None:
    with pytest.raises(ValidationError):
        Environment(name=name, template="backend")
