"""Unit tests for services/backend.py."""

from __future__ import annotations

import pathlib

import pytest
import yaml

from oqtopus_manager.services.backend import (
    build_stream_args,
    components_installed,
    config_which_to_filename,
    get_log_file,
    get_topology_json_path,
    load_topology_context,
    read_path_from_yaml,
    resolve_installed_config_path,
)
from oqtopus_manager.services.exceptions import InvalidArgumentError

# ── read_metadata is exercised via services.environment.read_metadata; see
# tests/oqtopus_manager/services/test_environment.py for shared coverage.


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


# ── build_stream_args ────────────────────────────────────────────────────────

_DEFAULTS: dict = {
    "service": "all",
    "component": "engine",
    "version": "",
    "foreground": False,
    "status": "",
    "skip_sse_build": False,
}


def _be(cmd: str, **kwargs: object) -> list[str]:
    """Call build_stream_args with defaults overridden by kwargs."""
    params = {**_DEFAULTS, **kwargs}
    return build_stream_args(cmd, **params)  # type: ignore[arg-type]


class TestBuildStreamArgs:
    def test_status(self) -> None:
        assert _be("status") == ["status"]

    def test_info(self) -> None:
        assert _be("info") == ["info"]

    def test_start_valid_service(self) -> None:
        assert _be("start", service="core") == ["start", "core"]

    def test_start_with_foreground(self) -> None:
        assert _be("start", service="all", foreground=True) == [
            "start",
            "all",
            "--foreground",
        ]

    def test_stop_valid_service(self) -> None:
        assert _be("stop", service="gateway") == ["stop", "gateway"]

    def test_restart_valid_service(self) -> None:
        assert _be("restart", service="tranqu") == ["restart", "tranqu"]

    def test_start_invalid_service_raises(self) -> None:
        with pytest.raises(InvalidArgumentError, match="Invalid service"):
            _be("start", service="no-such-service")

    def test_versions_valid_component(self) -> None:
        assert _be("versions", component="engine") == ["versions", "engine"]

    def test_versions_invalid_component_raises(self) -> None:
        with pytest.raises(InvalidArgumentError, match="Invalid component"):
            _be("versions", component="bogus")

    def test_install_all(self) -> None:
        assert _be("install", component="all") == ["install", "all"]

    def test_install_component_with_version(self) -> None:
        assert _be("install", component="engine", version="v1.2") == [
            "install",
            "engine",
            "v1.2",
        ]

    def test_install_skip_sse_build(self) -> None:
        result = _be("install", component="engine", skip_sse_build=True)
        assert "--skip-sse-build" in result

    def test_install_invalid_component_raises(self) -> None:
        with pytest.raises(InvalidArgumentError, match="Invalid component"):
            _be("install", component="unknown")

    def test_update_valid_component(self) -> None:
        assert _be("update", component="tranqu") == ["update", "tranqu"]

    def test_update_invalid_component_raises(self) -> None:
        with pytest.raises(InvalidArgumentError, match="Invalid component"):
            _be("update", component="bogus")

    def test_uninstall_with_version(self) -> None:
        assert _be("uninstall", component="gateway", version="v2.0") == [
            "uninstall",
            "gateway",
            "v2.0",
        ]

    def test_uninstall_missing_version_raises(self) -> None:
        with pytest.raises(InvalidArgumentError, match="version is required"):
            _be("uninstall", component="gateway", version="")

    def test_uninstall_invalid_component_raises(self) -> None:
        with pytest.raises(InvalidArgumentError, match="Invalid component"):
            _be("uninstall", component="bogus", version="v1")

    def test_build(self) -> None:
        assert _be("build") == ["build", "sse-runtime"]

    def test_device_status_show(self) -> None:
        assert _be("device-status-show") == ["device-status", "show"]

    def test_device_status_set_valid(self) -> None:
        assert _be("device-status-set", status="active") == ["device-status", "active"]

    def test_device_status_set_invalid_raises(self) -> None:
        with pytest.raises(InvalidArgumentError, match="Invalid status"):
            _be("device-status-set", status="broken")

    def test_unknown_command_raises(self) -> None:
        with pytest.raises(InvalidArgumentError, match="Unknown command"):
            _be("no-such-cmd")
