"""Unit tests for routers/cloud_local/detail.py — pure business-logic functions."""

from __future__ import annotations

import pytest

from oqtopus_manager.routers.cloud_local.detail import (
    _build_args as _cl_build_args,
    _build_component_args,
    _build_service_args,
    _validate_component,
)


# ── cloud-local helpers ───────────────────────────────────────────────────────


class TestValidateComponent:
    def test_valid_components_no_error(self) -> None:
        for comp in ("cloud", "frontend", "admin"):
            _validate_component(comp)  # must not raise

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid component"):
            _validate_component("bogus")

    def test_all_disallowed_by_default(self) -> None:
        with pytest.raises(ValueError, match="Invalid component"):
            _validate_component("all")

    def test_all_allowed_with_flag(self) -> None:
        _validate_component("all", allow_all=True)  # must not raise


class TestBuildServiceArgs:
    def test_start_without_foreground(self) -> None:
        assert _build_service_args("start", "all", False) == ["start", "all"]

    def test_start_with_foreground(self) -> None:
        assert _build_service_args("start", "worker", True) == [
            "start",
            "worker",
            "--foreground",
        ]

    def test_stop_foreground_flag_ignored(self) -> None:
        # --foreground is only appended for "start"
        assert _build_service_args("stop", "db", True) == ["stop", "db"]

    def test_invalid_service_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid service"):
            _build_service_args("start", "bogus", False)


class TestBuildComponentArgs:
    def test_versions(self) -> None:
        assert _build_component_args("versions", "cloud", "") == ["versions", "cloud"]

    def test_install_all_no_version(self) -> None:
        assert _build_component_args("install", "all", "") == ["install", "all"]

    def test_install_component_with_version(self) -> None:
        assert _build_component_args("install", "frontend", "v1") == [
            "install",
            "frontend",
            "v1",
        ]

    def test_update(self) -> None:
        assert _build_component_args("update", "admin", "") == ["update", "admin"]

    def test_uninstall_with_version(self) -> None:
        assert _build_component_args("uninstall", "cloud", "v2") == [
            "uninstall",
            "cloud",
            "v2",
        ]

    def test_uninstall_missing_version_raises(self) -> None:
        with pytest.raises(ValueError, match="version is required"):
            _build_component_args("uninstall", "cloud", "")


class TestCloudLocalBuildArgs:
    def test_status(self) -> None:
        assert _cl_build_args("status", "all", "cloud", "", False) == ["status"]

    def test_info(self) -> None:
        assert _cl_build_args("info", "all", "cloud", "", False) == ["info"]

    def test_start_delegates_to_service_args(self) -> None:
        assert _cl_build_args("start", "db", "cloud", "", False) == ["start", "db"]

    def test_versions_delegates_to_component_args(self) -> None:
        assert _cl_build_args("versions", "all", "cloud", "", False) == [
            "versions",
            "cloud",
        ]

    def test_unknown_command_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown command"):
            _cl_build_args("bogus", "all", "cloud", "", False)
