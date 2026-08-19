"""Unit tests for services/cloud_local.py."""

from __future__ import annotations

import pytest

from oqtopus_manager.services.cloud_local import (
    build_component_args,
    build_service_args,
    build_stream_args,
    validate_component,
)
from oqtopus_manager.services.exceptions import InvalidArgumentError


class TestValidateComponent:
    def test_valid_components_no_error(self) -> None:
        for comp in ("cloud", "frontend", "admin"):
            validate_component(comp)  # must not raise

    def test_invalid_raises(self) -> None:
        with pytest.raises(InvalidArgumentError, match="Invalid component"):
            validate_component("bogus")

    def test_all_disallowed_by_default(self) -> None:
        with pytest.raises(InvalidArgumentError, match="Invalid component"):
            validate_component("all")

    def test_all_allowed_with_flag(self) -> None:
        validate_component("all", allow_all=True)  # must not raise


class TestBuildServiceArgs:
    def test_start_without_foreground(self) -> None:
        assert build_service_args("start", "all", False) == ["start", "all"]

    def test_start_with_foreground(self) -> None:
        assert build_service_args("start", "worker", True) == [
            "start",
            "worker",
            "--foreground",
        ]

    def test_stop_foreground_flag_ignored(self) -> None:
        # --foreground is only appended for "start"
        assert build_service_args("stop", "db", True) == ["stop", "db"]

    def test_invalid_service_raises(self) -> None:
        with pytest.raises(InvalidArgumentError, match="Invalid service"):
            build_service_args("start", "bogus", False)


class TestBuildComponentArgs:
    def test_versions(self) -> None:
        assert build_component_args("versions", "cloud", "") == ["versions", "cloud"]

    def test_install_all_no_version(self) -> None:
        assert build_component_args("install", "all", "") == ["install", "all"]

    def test_install_component_with_version(self) -> None:
        assert build_component_args("install", "frontend", "v1") == [
            "install",
            "frontend",
            "v1",
        ]

    def test_update(self) -> None:
        assert build_component_args("update", "admin", "") == ["update", "admin"]

    def test_uninstall_with_version(self) -> None:
        assert build_component_args("uninstall", "cloud", "v2") == [
            "uninstall",
            "cloud",
            "v2",
        ]

    def test_uninstall_missing_version_raises(self) -> None:
        with pytest.raises(InvalidArgumentError, match="version is required"):
            build_component_args("uninstall", "cloud", "")


class TestCloudLocalBuildStreamArgs:
    def test_status(self) -> None:
        assert build_stream_args("status", "all", "cloud", "", False) == ["status"]

    def test_info(self) -> None:
        assert build_stream_args("info", "all", "cloud", "", False) == ["info"]

    def test_start_delegates_to_service_args(self) -> None:
        assert build_stream_args("start", "db", "cloud", "", False) == ["start", "db"]

    def test_versions_delegates_to_component_args(self) -> None:
        assert build_stream_args("versions", "all", "cloud", "", False) == [
            "versions",
            "cloud",
        ]

    def test_unknown_command_raises(self) -> None:
        with pytest.raises(InvalidArgumentError, match="Unknown command"):
            build_stream_args("bogus", "all", "cloud", "", False)
