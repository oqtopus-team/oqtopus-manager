"""Unit tests for services/cloud_local.py."""

from __future__ import annotations

import pytest

from oqtopus_manager.services.cloud_local import (
    build_component_args,
    build_service_args,
    component_lock_for,
    install_lock_for,
    service_lock_for,
    validate_component,
)
from oqtopus_manager.services.exceptions import InvalidArgumentError
from oqtopus_manager.services.locks import LockRegistry


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


class TestServiceLockFor:
    @pytest.mark.anyio
    async def test_start_db_locks_container_group(self) -> None:
        registry = LockRegistry()
        async with service_lock_for(
            registry, "cloud", "db", operation="op", held_by="u"
        ):
            [info] = registry.snapshot()
            assert info.scope == "cloud/containers"

    @pytest.mark.anyio
    async def test_start_worker_locks_own_service_name(self) -> None:
        registry = LockRegistry()
        async with service_lock_for(
            registry, "cloud", "worker", operation="op", held_by="u"
        ):
            [info] = registry.snapshot()
            assert info.scope == "cloud/worker"

    @pytest.mark.anyio
    async def test_all_locks_env_wide(self) -> None:
        registry = LockRegistry()
        async with service_lock_for(
            registry, "cloud", "all", operation="op", held_by="u"
        ):
            [info] = registry.snapshot()
            assert info.scope == "cloud/*"


class TestInstallLockFor:
    @pytest.mark.anyio
    async def test_all_locks_every_component_and_env(self) -> None:
        registry = LockRegistry()
        async with install_lock_for(
            registry, "cloud", "all", operation="op", held_by="u"
        ):
            scopes = {info.scope for info in registry.snapshot()}
            assert scopes == {
                "component:cloud",
                "component:frontend",
                "component:admin",
                "environment:cloud",
            }


class TestComponentLockFor:
    @pytest.mark.anyio
    async def test_locks_component_and_env(self) -> None:
        registry = LockRegistry()
        async with component_lock_for(
            registry, "cloud", "admin", operation="op", held_by="u"
        ):
            scopes = {info.scope for info in registry.snapshot()}
            assert scopes == {"component:admin", "environment:cloud"}
