"""Unit tests for services/backend.py."""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock

import pytest
import yaml
from pytest_mock import MockerFixture

from oqtopus_manager.models.environment import Environment
from oqtopus_manager.services import backend as backend_service
from oqtopus_manager.services.backend import (
    build_build_sse_runtime_args,
    build_device_status_args,
    build_install_args,
    build_service_args,
    build_uninstall_args,
    build_update_args,
    build_versions_args,
    components_installed,
    config_which_to_filename,
    get_log_file,
    get_topology_json_path,
    load_topology_context,
    read_path_from_yaml,
    resolve_installed_config_path,
)
from oqtopus_manager.services.exceptions import (
    CliNotFoundError,
    CliTimeoutError,
    CommandFailedError,
    InvalidArgumentError,
)
from oqtopus_manager.services.locks import LockRegistry
from oqtopus_manager.util.cli import CommandResult

# ── read_metadata is exercised via services.environment.read_metadata; see
# tests/oqtopus_manager/services/test_environment.py for shared coverage.


def _cfg(tmp_path: pathlib.Path) -> MagicMock:
    return MagicMock(
        oqtopus_cli_read_timeout_sec=10, default_environment_base_path=tmp_path
    )


def _stub_env(mocker: MockerFixture, tmp_path: pathlib.Path, name: str = "demo") -> None:
    env = Environment(name=name, template="backend", root_path=tmp_path / name)
    mocker.patch(
        "oqtopus_manager.services.backend.get_environment_or_404", return_value=env
    )


# ── get_status / get_device_status / get_info / get_component_versions_detailed ──


class TestGetStatus:
    @pytest.mark.anyio
    async def test_returns_parsed_status(
        self, mocker: MockerFixture, tmp_path: pathlib.Path
    ) -> None:
        _stub_env(mocker, tmp_path)
        mocker.patch(
            "oqtopus_manager.services.backend.run_oqtopus_subcommand_output",
            return_value=CommandResult(
                returncode=0, stdout="core: Running (PID 1)\ngateway: Stopped\n", stderr=""
            ),
        )
        data = await backend_service.get_status(_cfg(tmp_path), "demo")
        assert data.template == "backend"
        assert data.environment_name == "demo"
        assert [s.state for s in data.services] == ["running", "stopped"]

    @pytest.mark.anyio
    async def test_command_failure_raises_command_failed_error(
        self, mocker: MockerFixture, tmp_path: pathlib.Path
    ) -> None:
        _stub_env(mocker, tmp_path)
        mocker.patch(
            "oqtopus_manager.services.backend.run_oqtopus_subcommand_output",
            return_value=CommandResult(returncode=1, stdout="", stderr="boom"),
        )
        with pytest.raises(CommandFailedError):
            await backend_service.get_status(_cfg(tmp_path), "demo")

    @pytest.mark.anyio
    async def test_cli_not_found_raises_cli_not_found_error(
        self, mocker: MockerFixture, tmp_path: pathlib.Path
    ) -> None:
        _stub_env(mocker, tmp_path)
        mocker.patch(
            "oqtopus_manager.services.backend.run_oqtopus_subcommand_output",
            return_value=CommandResult(returncode=127, stdout="", stderr="not found"),
        )
        with pytest.raises(CliNotFoundError):
            await backend_service.get_status(_cfg(tmp_path), "demo")

    @pytest.mark.anyio
    async def test_timeout_raises_cli_timeout_error(
        self, mocker: MockerFixture, tmp_path: pathlib.Path
    ) -> None:
        _stub_env(mocker, tmp_path)
        mocker.patch(
            "oqtopus_manager.services.backend.run_oqtopus_subcommand_output",
            return_value=CommandResult(returncode=None, stdout="", stderr="timed out"),
        )
        with pytest.raises(CliTimeoutError):
            await backend_service.get_status(_cfg(tmp_path), "demo")


class TestGetDeviceStatus:
    @pytest.mark.anyio
    async def test_returns_parsed_device_status(
        self, mocker: MockerFixture, tmp_path: pathlib.Path
    ) -> None:
        _stub_env(mocker, tmp_path)
        mocker.patch(
            "oqtopus_manager.services.backend.run_oqtopus_subcommand_output",
            return_value=CommandResult(returncode=0, stdout="active\n", stderr=""),
        )
        data = await backend_service.get_device_status(_cfg(tmp_path), "demo")
        assert data.device_status == "active"


class TestGetInfo:
    @pytest.mark.anyio
    async def test_returns_all_known_components(
        self, mocker: MockerFixture, tmp_path: pathlib.Path
    ) -> None:
        _stub_env(mocker, tmp_path)
        mocker.patch(
            "oqtopus_manager.services.backend.run_oqtopus_subcommand_output",
            return_value=CommandResult(
                returncode=0, stdout="engine_version=v1.0.0\n", stderr=""
            ),
        )
        data = await backend_service.get_info(_cfg(tmp_path), "demo")
        assert [c.name for c in data.components] == ["engine", "tranqu", "gateway"]
        assert data.all_installed is False


class TestGetComponentVersionsDetailed:
    @pytest.mark.anyio
    async def test_invalid_component_raises(
        self, tmp_path: pathlib.Path
    ) -> None:
        with pytest.raises(InvalidArgumentError, match="Invalid component"):
            await backend_service.get_component_versions_detailed(
                _cfg(tmp_path), "demo", "bogus"
            )

    @pytest.mark.anyio
    async def test_returns_versions_data(
        self, mocker: MockerFixture, tmp_path: pathlib.Path
    ) -> None:
        _stub_env(mocker, tmp_path)
        mocker.patch(
            "oqtopus_manager.services.backend.run_oqtopus_subcommand_output",
            return_value=CommandResult(
                returncode=0, stdout="gateway:\n* v1.1.14 (installed)\n  v1.1.15\n", stderr=""
            ),
        )
        data = await backend_service.get_component_versions_detailed(
            _cfg(tmp_path), "demo", "gateway"
        )
        assert data.current == "v1.1.14"
        assert [v.version for v in data.versions] == ["v1.1.14", "v1.1.15"]


class TestReadPathFromYaml:
    def test_no_file_returns_none(self, tmp_path: pathlib.Path) -> None:
        assert read_path_from_yaml(tmp_path / "absent.yaml", ["a"], tmp_path) is None

    def test_key_missing_returns_none(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "cfg.yaml"
        f.write_text(yaml.dump({"other": "val"}), encoding="utf-8")
        assert read_path_from_yaml(f, ["missing"], tmp_path) is None

    def test_intermediate_not_dict_returns_none(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "cfg.yaml"
        f.write_text(yaml.dump({"a": "not-a-dict"}), encoding="utf-8")
        assert read_path_from_yaml(f, ["a", "b"], tmp_path) is None

    def test_absolute_path_returned_as_is(self, tmp_path: pathlib.Path) -> None:
        abs_path = tmp_path / "logs" / "app.log"
        f = tmp_path / "cfg.yaml"
        f.write_text(yaml.dump({"file": str(abs_path)}), encoding="utf-8")
        assert read_path_from_yaml(f, ["file"], tmp_path) == abs_path

    def test_relative_path_resolved_against_env_root(
        self, tmp_path: pathlib.Path
    ) -> None:
        f = tmp_path / "cfg.yaml"
        f.write_text(yaml.dump({"file": "logs/app.log"}), encoding="utf-8")
        assert read_path_from_yaml(f, ["file"], tmp_path) == tmp_path / "logs" / "app.log"

    def test_nested_keys(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "cfg.yaml"
        f.write_text(
            yaml.dump({"handlers": {"file": {"filename": "/tmp/x.log"}}}),
            encoding="utf-8",
        )
        assert read_path_from_yaml(f, ["handlers", "file", "filename"], tmp_path) == pathlib.Path("/tmp/x.log")

    def test_falsy_value_returns_none(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "cfg.yaml"
        f.write_text(yaml.dump({"file": ""}), encoding="utf-8")
        assert read_path_from_yaml(f, ["file"], tmp_path) is None


class TestGetLogFile:
    def test_no_logging_yaml_returns_none(self, tmp_path: pathlib.Path) -> None:
        assert get_log_file(tmp_path, "engine") is None

    def test_with_logging_yaml(self, tmp_path: pathlib.Path) -> None:
        log_path = tmp_path / "logs" / "engine.log"
        cfg_dir = tmp_path / "config" / "engine"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "logging.yaml").write_text(
            yaml.dump({"handlers": {"file": {"filename": str(log_path)}}}),
            encoding="utf-8",
        )
        assert get_log_file(tmp_path, "engine") == log_path


class TestGetTopologyJsonPath:
    def test_no_config_returns_none(self, tmp_path: pathlib.Path) -> None:
        assert get_topology_json_path(tmp_path) is None

    def test_with_config(self, tmp_path: pathlib.Path) -> None:
        topo_path = tmp_path / "topology.json"
        cfg_dir = tmp_path / "config" / "gateway"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.yaml").write_text(
            yaml.dump({"device_topology_json_path": str(topo_path)}),
            encoding="utf-8",
        )
        assert get_topology_json_path(tmp_path) == topo_path


class TestLoadTopologyContext:
    def test_non_gateway_service_returns_empty(self, tmp_path: pathlib.Path) -> None:
        ctx = load_topology_context("engine", tmp_path, 600)
        assert ctx == {
            "topology_json_path": None,
            "topology_content": None,
            "topology_is_locked": False,
            "topology_locked_since": None,
            "topology_locked_since_ts": None,
        }

    def test_gateway_no_config_returns_empty(self, tmp_path: pathlib.Path) -> None:
        ctx = load_topology_context("gateway", tmp_path, 600)
        assert ctx["topology_json_path"] is None

    def test_gateway_with_existing_topology_file(
        self, tmp_path: pathlib.Path
    ) -> None:
        topo_path = tmp_path / "topology.json"
        topo_path.write_text('{"qubits": 5}', encoding="utf-8")
        cfg_dir = tmp_path / "config" / "gateway"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.yaml").write_text(
            yaml.dump({"device_topology_json_path": str(topo_path)}),
            encoding="utf-8",
        )
        ctx = load_topology_context("gateway", tmp_path, 600)
        assert ctx["topology_json_path"] == topo_path
        assert ctx["topology_content"] == '{"qubits": 5}'
        assert ctx["topology_is_locked"] is False

    def test_gateway_missing_topology_file_content_is_none(
        self, tmp_path: pathlib.Path
    ) -> None:
        topo_path = tmp_path / "topology.json"  # file does NOT exist
        cfg_dir = tmp_path / "config" / "gateway"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.yaml").write_text(
            yaml.dump({"device_topology_json_path": str(topo_path)}),
            encoding="utf-8",
        )
        ctx = load_topology_context("gateway", tmp_path, 600)
        assert ctx["topology_json_path"] == topo_path
        assert ctx["topology_content"] is None


class TestComponentsInstalled:
    def test_no_directories_returns_false(self, tmp_path: pathlib.Path) -> None:
        assert components_installed(str(tmp_path)) is False

    def test_engine_dir_returns_true(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "engine").mkdir()
        assert components_installed(str(tmp_path)) is True

    def test_all_dirs_returns_true(self, tmp_path: pathlib.Path) -> None:
        for comp in ("engine", "tranqu", "gateway"):
            (tmp_path / comp).mkdir()
        assert components_installed(str(tmp_path)) is True


class TestConfigWhichToFilename:
    def test_config_returns_config_yaml(self) -> None:
        assert config_which_to_filename("config") == "config.yaml"

    def test_logging_returns_logging_yaml(self) -> None:
        assert config_which_to_filename("logging") == "logging.yaml"

    def test_unknown_raises(self) -> None:
        with pytest.raises(InvalidArgumentError):
            config_which_to_filename("other")


class TestResolveInstalledConfigPath:
    def _meta(self, **kwargs: str) -> dict[str, str]:
        base = {"install_root": "/releases", "engine_version": "v1.0.0", "tranqu_version": "v2.0.0", "gateway_version": "v3.0.0"}
        base.update(kwargs)
        return base

    def test_engine_core_release(self, tmp_path: pathlib.Path) -> None:
        result = resolve_installed_config_path("core", "config.yaml", self._meta(), tmp_path)
        assert result == pathlib.Path("/releases/engine-v1.0.0/core/config/config.yaml")

    def test_engine_sse_engine_uses_core_subdir(self, tmp_path: pathlib.Path) -> None:
        result = resolve_installed_config_path("sse_engine", "config.yaml", self._meta(), tmp_path)
        assert result == pathlib.Path("/releases/engine-v1.0.0/core/config/sse_engine_config.yaml")

    def test_engine_sse_engine_logging_uses_prefixed_filename(self, tmp_path: pathlib.Path) -> None:
        result = resolve_installed_config_path("sse_engine", "logging.yaml", self._meta(), tmp_path)
        assert result == pathlib.Path("/releases/engine-v1.0.0/core/config/sse_engine_logging.yaml")

    def test_engine_combiner_release(self, tmp_path: pathlib.Path) -> None:
        result = resolve_installed_config_path("combiner", "logging.yaml", self._meta(), tmp_path)
        assert result == pathlib.Path("/releases/engine-v1.0.0/combiner/config/logging.yaml")

    def test_engine_estimator_release(self, tmp_path: pathlib.Path) -> None:
        result = resolve_installed_config_path("estimator", "config.yaml", self._meta(), tmp_path)
        assert result == pathlib.Path("/releases/engine-v1.0.0/estimator/config/config.yaml")

    def test_engine_mitigator_release(self, tmp_path: pathlib.Path) -> None:
        result = resolve_installed_config_path("mitigator", "config.yaml", self._meta(), tmp_path)
        assert result == pathlib.Path("/releases/engine-v1.0.0/mitigator/config/config.yaml")

    def test_tranqu_release(self, tmp_path: pathlib.Path) -> None:
        result = resolve_installed_config_path("tranqu", "config.yaml", self._meta(), tmp_path)
        assert result == pathlib.Path("/releases/tranqu-v2.0.0/config/config.yaml")

    def test_gateway_release_config_uses_qulacs_filename(self, tmp_path: pathlib.Path) -> None:
        result = resolve_installed_config_path("gateway", "config.yaml", self._meta(), tmp_path)
        assert result == pathlib.Path("/releases/gateway-v3.0.0/config/config.yaml.qulacs")

    def test_gateway_release_logging_keeps_filename(self, tmp_path: pathlib.Path) -> None:
        result = resolve_installed_config_path("gateway", "logging.yaml", self._meta(), tmp_path)
        assert result == pathlib.Path("/releases/gateway-v3.0.0/config/logging.yaml")

    def test_gateway_topology_uses_example_subdir(self, tmp_path: pathlib.Path) -> None:
        result = resolve_installed_config_path(
            "gateway", "device_topology_sim.json", self._meta(), tmp_path
        )
        assert result == pathlib.Path(
            "/releases/gateway-v3.0.0/config/example/device_topology_sim.json"
        )

    def test_engine_branch_uses_env_root(self, tmp_path: pathlib.Path) -> None:
        meta = self._meta(engine_version="branch:main")
        result = resolve_installed_config_path("core", "config.yaml", meta, tmp_path)
        assert result == tmp_path / "engine" / "core" / "config" / "config.yaml"

    def test_sse_engine_branch_uses_core_subdir(self, tmp_path: pathlib.Path) -> None:
        meta = self._meta(engine_version="branch:develop")
        result = resolve_installed_config_path("sse_engine", "config.yaml", meta, tmp_path)
        assert result == tmp_path / "engine" / "core" / "config" / "config.yaml"

    def test_tranqu_branch_uses_env_root(self, tmp_path: pathlib.Path) -> None:
        meta = self._meta(tranqu_version="branch:main")
        result = resolve_installed_config_path("tranqu", "config.yaml", meta, tmp_path)
        assert result == tmp_path / "tranqu" / "config" / "config.yaml"

    def test_gateway_branch_uses_env_root(self, tmp_path: pathlib.Path) -> None:
        meta = self._meta(gateway_version="branch:feature-x")
        result = resolve_installed_config_path("gateway", "config.yaml", meta, tmp_path)
        assert result == tmp_path / "gateway" / "config" / "config.yaml"

    def test_no_install_root_returns_none_for_release(self, tmp_path: pathlib.Path) -> None:
        meta = {"engine_version": "v1.0.0"}
        assert resolve_installed_config_path("core", "config.yaml", meta, tmp_path) is None

    def test_unknown_service_returns_none(self, tmp_path: pathlib.Path) -> None:
        assert resolve_installed_config_path("unknown", "config.yaml", self._meta(), tmp_path) is None


# ── per-operation argv builders ──────────────────────────────────────────────


class TestBuildServiceArgs:
    def test_start_valid_service(self) -> None:
        assert build_service_args("start", "core", False) == ["start", "core"]

    def test_start_with_foreground(self) -> None:
        assert build_service_args("start", "all", True) == [
            "start",
            "all",
            "--foreground",
        ]

    def test_stop_valid_service(self) -> None:
        assert build_service_args("stop", "gateway", False) == ["stop", "gateway"]

    def test_restart_valid_service(self) -> None:
        assert build_service_args("restart", "tranqu", False) == ["restart", "tranqu"]

    def test_invalid_service_raises(self) -> None:
        with pytest.raises(InvalidArgumentError, match="Invalid service"):
            build_service_args("start", "no-such-service", False)


class TestBuildVersionsArgs:
    def test_valid_component(self) -> None:
        assert build_versions_args("engine") == ["versions", "engine"]

    def test_invalid_component_raises(self) -> None:
        with pytest.raises(InvalidArgumentError, match="Invalid component"):
            build_versions_args("bogus")


class TestBuildInstallArgs:
    def test_install_all(self) -> None:
        assert build_install_args("all", "", False) == ["install", "all"]

    def test_component_with_version(self) -> None:
        assert build_install_args("engine", "v1.2", False) == [
            "install",
            "engine",
            "v1.2",
        ]

    def test_skip_sse_build(self) -> None:
        result = build_install_args("engine", "", True)
        assert "--skip-sse-build" in result

    def test_invalid_component_raises(self) -> None:
        with pytest.raises(InvalidArgumentError, match="Invalid component"):
            build_install_args("unknown", "", False)


class TestBuildUpdateArgs:
    def test_valid_component(self) -> None:
        assert build_update_args("tranqu") == ["update", "tranqu"]

    def test_invalid_component_raises(self) -> None:
        with pytest.raises(InvalidArgumentError, match="Invalid component"):
            build_update_args("bogus")


class TestBuildUninstallArgs:
    def test_with_version(self) -> None:
        assert build_uninstall_args("gateway", "v2.0") == [
            "uninstall",
            "gateway",
            "v2.0",
        ]

    def test_missing_version_raises(self) -> None:
        with pytest.raises(InvalidArgumentError, match="version is required"):
            build_uninstall_args("gateway", "")

    def test_invalid_component_raises(self) -> None:
        with pytest.raises(InvalidArgumentError, match="Invalid component"):
            build_uninstall_args("bogus", "v1")


class TestBuildBuildSseRuntimeArgs:
    def test_build(self) -> None:
        assert build_build_sse_runtime_args() == ["build", "sse-runtime"]


class TestBuildDeviceStatusArgs:
    def test_valid(self) -> None:
        assert build_device_status_args("active") == ["device-status", "active"]

    def test_invalid_raises(self) -> None:
        with pytest.raises(InvalidArgumentError, match="Invalid status"):
            build_device_status_args("broken")


# ── per-operation lock helpers ───────────────────────────────────────────────


class TestServiceLockFor:
    @pytest.mark.anyio
    async def test_specific_service_locks_env_service(self) -> None:
        registry = LockRegistry()
        async with backend_service.service_lock_for(
            registry, "qulacs", "core", operation="op", held_by="u"
        ):
            [info] = registry.snapshot()
            assert info.scope == "qulacs/core"
        assert registry.snapshot() == []

    @pytest.mark.anyio
    async def test_all_locks_env_wide(self) -> None:
        registry = LockRegistry()
        async with backend_service.service_lock_for(
            registry, "qulacs", "all", operation="op", held_by="u"
        ):
            [info] = registry.snapshot()
            assert info.scope == "qulacs/*"


class TestInstallLockFor:
    @pytest.mark.anyio
    async def test_specific_component_locks_component_and_env(self) -> None:
        registry = LockRegistry()
        async with backend_service.install_lock_for(
            registry, "qulacs", "gateway", operation="op", held_by="u"
        ):
            scopes = {info.scope for info in registry.snapshot()}
            assert scopes == {"component:gateway", "environment:qulacs"}

    @pytest.mark.anyio
    async def test_all_locks_every_component_and_env(self) -> None:
        registry = LockRegistry()
        async with backend_service.install_lock_for(
            registry, "qulacs", "all", operation="op", held_by="u"
        ):
            scopes = {info.scope for info in registry.snapshot()}
            assert scopes == {
                "component:engine",
                "component:tranqu",
                "component:gateway",
                "environment:qulacs",
            }


class TestComponentLockFor:
    @pytest.mark.anyio
    async def test_locks_component_and_env(self) -> None:
        registry = LockRegistry()
        async with backend_service.component_lock_for(
            registry, "qulacs", "engine", operation="op", held_by="u"
        ):
            scopes = {info.scope for info in registry.snapshot()}
            assert scopes == {"component:engine", "environment:qulacs"}


class TestBuildSseRuntimeLock:
    @pytest.mark.anyio
    async def test_locks_engine_component_only(self) -> None:
        registry = LockRegistry()
        async with backend_service.build_sse_runtime_lock(
            registry, operation="op", held_by="u"
        ):
            [info] = registry.snapshot()
            assert info.scope == "component:engine"
