"""Unit tests for routers/backend/_utils.py."""

from __future__ import annotations

import pathlib

import pytest
import yaml
from fastapi import HTTPException

from oqtopus_manager.routers.backend._utils import (
    _components_installed,
    _config_which_to_filename,
    _get_log_file,
    _get_topology_json_path,
    _load_topology_context,
    _read_metadata,
    _read_path_from_yaml,
    _resolve_installed_config_path,
)


class TestReadMetadata:
    def test_no_file_returns_empty(self, tmp_path: pathlib.Path) -> None:
        assert _read_metadata(tmp_path) == {}

    def test_parses_key_value_pairs(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".metadata").write_text(
            "engine_version=1.2.3\ntranqu_version=2.0\n", encoding="utf-8"
        )
        assert _read_metadata(tmp_path) == {
            "engine_version": "1.2.3",
            "tranqu_version": "2.0",
        }

    def test_ignores_lines_without_equals(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".metadata").write_text(
            "no-equals-here\nkey=value\n", encoding="utf-8"
        )
        assert _read_metadata(tmp_path) == {"key": "value"}

    def test_strips_whitespace(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".metadata").write_text("  key  =  val  \n", encoding="utf-8")
        assert _read_metadata(tmp_path) == {"key": "val"}


class TestReadPathFromYaml:
    def test_no_file_returns_none(self, tmp_path: pathlib.Path) -> None:
        assert _read_path_from_yaml(tmp_path / "absent.yaml", ["a"], tmp_path) is None

    def test_key_missing_returns_none(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "cfg.yaml"
        f.write_text(yaml.dump({"other": "val"}), encoding="utf-8")
        assert _read_path_from_yaml(f, ["missing"], tmp_path) is None

    def test_intermediate_not_dict_returns_none(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "cfg.yaml"
        f.write_text(yaml.dump({"a": "not-a-dict"}), encoding="utf-8")
        assert _read_path_from_yaml(f, ["a", "b"], tmp_path) is None

    def test_absolute_path_returned_as_is(self, tmp_path: pathlib.Path) -> None:
        abs_path = tmp_path / "logs" / "app.log"
        f = tmp_path / "cfg.yaml"
        f.write_text(yaml.dump({"file": str(abs_path)}), encoding="utf-8")
        assert _read_path_from_yaml(f, ["file"], tmp_path) == abs_path

    def test_relative_path_resolved_against_env_root(
        self, tmp_path: pathlib.Path
    ) -> None:
        f = tmp_path / "cfg.yaml"
        f.write_text(yaml.dump({"file": "logs/app.log"}), encoding="utf-8")
        assert _read_path_from_yaml(f, ["file"], tmp_path) == tmp_path / "logs" / "app.log"

    def test_nested_keys(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "cfg.yaml"
        f.write_text(
            yaml.dump({"handlers": {"file": {"filename": "/tmp/x.log"}}}),
            encoding="utf-8",
        )
        assert _read_path_from_yaml(f, ["handlers", "file", "filename"], tmp_path) == pathlib.Path("/tmp/x.log")

    def test_falsy_value_returns_none(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "cfg.yaml"
        f.write_text(yaml.dump({"file": ""}), encoding="utf-8")
        assert _read_path_from_yaml(f, ["file"], tmp_path) is None


class TestGetLogFile:
    def test_no_logging_yaml_returns_none(self, tmp_path: pathlib.Path) -> None:
        assert _get_log_file(tmp_path, "engine") is None

    def test_with_logging_yaml(self, tmp_path: pathlib.Path) -> None:
        log_path = tmp_path / "logs" / "engine.log"
        cfg_dir = tmp_path / "config" / "engine"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "logging.yaml").write_text(
            yaml.dump({"handlers": {"file": {"filename": str(log_path)}}}),
            encoding="utf-8",
        )
        assert _get_log_file(tmp_path, "engine") == log_path


class TestGetTopologyJsonPath:
    def test_no_config_returns_none(self, tmp_path: pathlib.Path) -> None:
        assert _get_topology_json_path(tmp_path) is None

    def test_with_config(self, tmp_path: pathlib.Path) -> None:
        topo_path = tmp_path / "topology.json"
        cfg_dir = tmp_path / "config" / "gateway"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.yaml").write_text(
            yaml.dump({"device_topology_json_path": str(topo_path)}),
            encoding="utf-8",
        )
        assert _get_topology_json_path(tmp_path) == topo_path


class TestLoadTopologyContext:
    def test_non_gateway_service_returns_empty(self, tmp_path: pathlib.Path) -> None:
        ctx = _load_topology_context("engine", tmp_path, 600)
        assert ctx == {
            "topology_json_path": None,
            "topology_content": None,
            "topology_is_locked": False,
            "topology_locked_since": None,
            "topology_locked_since_ts": None,
        }

    def test_gateway_no_config_returns_empty(self, tmp_path: pathlib.Path) -> None:
        ctx = _load_topology_context("gateway", tmp_path, 600)
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
        ctx = _load_topology_context("gateway", tmp_path, 600)
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
        ctx = _load_topology_context("gateway", tmp_path, 600)
        assert ctx["topology_json_path"] == topo_path
        assert ctx["topology_content"] is None


class TestComponentsInstalled:
    def test_no_directories_returns_false(self, tmp_path: pathlib.Path) -> None:
        assert _components_installed(str(tmp_path)) is False

    def test_engine_dir_returns_true(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "engine").mkdir()
        assert _components_installed(str(tmp_path)) is True

    def test_all_dirs_returns_true(self, tmp_path: pathlib.Path) -> None:
        for comp in ("engine", "tranqu", "gateway"):
            (tmp_path / comp).mkdir()
        assert _components_installed(str(tmp_path)) is True


class TestConfigWhichToFilename:
    def test_config_returns_config_yaml(self) -> None:
        assert _config_which_to_filename("config") == "config.yaml"

    def test_logging_returns_logging_yaml(self) -> None:
        assert _config_which_to_filename("logging") == "logging.yaml"

    def test_unknown_raises_http_400(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _config_which_to_filename("other")
        assert exc_info.value.status_code == 400


class TestResolveInstalledConfigPath:
    def _meta(self, **kwargs: str) -> dict[str, str]:
        base = {"install_root": "/releases", "engine_version": "v1.0.0", "tranqu_version": "v2.0.0", "gateway_version": "v3.0.0"}
        base.update(kwargs)
        return base

    def test_engine_core_release(self, tmp_path: pathlib.Path) -> None:
        result = _resolve_installed_config_path("core", "config.yaml", self._meta(), tmp_path)
        assert result == pathlib.Path("/releases/engine-v1.0.0/core/config/config.yaml")

    def test_engine_sse_engine_uses_core_subdir(self, tmp_path: pathlib.Path) -> None:
        result = _resolve_installed_config_path("sse_engine", "config.yaml", self._meta(), tmp_path)
        assert result == pathlib.Path("/releases/engine-v1.0.0/core/config/sse_engine_config.yaml")

    def test_engine_sse_engine_logging_uses_prefixed_filename(self, tmp_path: pathlib.Path) -> None:
        result = _resolve_installed_config_path("sse_engine", "logging.yaml", self._meta(), tmp_path)
        assert result == pathlib.Path("/releases/engine-v1.0.0/core/config/sse_engine_logging.yaml")

    def test_engine_combiner_release(self, tmp_path: pathlib.Path) -> None:
        result = _resolve_installed_config_path("combiner", "logging.yaml", self._meta(), tmp_path)
        assert result == pathlib.Path("/releases/engine-v1.0.0/combiner/config/logging.yaml")

    def test_engine_estimator_release(self, tmp_path: pathlib.Path) -> None:
        result = _resolve_installed_config_path("estimator", "config.yaml", self._meta(), tmp_path)
        assert result == pathlib.Path("/releases/engine-v1.0.0/estimator/config/config.yaml")

    def test_engine_mitigator_release(self, tmp_path: pathlib.Path) -> None:
        result = _resolve_installed_config_path("mitigator", "config.yaml", self._meta(), tmp_path)
        assert result == pathlib.Path("/releases/engine-v1.0.0/mitigator/config/config.yaml")

    def test_tranqu_release(self, tmp_path: pathlib.Path) -> None:
        result = _resolve_installed_config_path("tranqu", "config.yaml", self._meta(), tmp_path)
        assert result == pathlib.Path("/releases/tranqu-v2.0.0/config/config.yaml")

    def test_gateway_release_config_uses_qulacs_filename(self, tmp_path: pathlib.Path) -> None:
        result = _resolve_installed_config_path("gateway", "config.yaml", self._meta(), tmp_path)
        assert result == pathlib.Path("/releases/gateway-v3.0.0/config/config.yaml.qulacs")

    def test_gateway_release_logging_keeps_filename(self, tmp_path: pathlib.Path) -> None:
        result = _resolve_installed_config_path("gateway", "logging.yaml", self._meta(), tmp_path)
        assert result == pathlib.Path("/releases/gateway-v3.0.0/config/logging.yaml")

    def test_gateway_topology_uses_example_subdir(self, tmp_path: pathlib.Path) -> None:
        result = _resolve_installed_config_path(
            "gateway", "device_topology_sim.json", self._meta(), tmp_path
        )
        assert result == pathlib.Path(
            "/releases/gateway-v3.0.0/config/example/device_topology_sim.json"
        )

    def test_engine_branch_uses_env_root(self, tmp_path: pathlib.Path) -> None:
        meta = self._meta(engine_version="branch:main")
        result = _resolve_installed_config_path("core", "config.yaml", meta, tmp_path)
        assert result == tmp_path / "engine" / "core" / "config" / "config.yaml"

    def test_sse_engine_branch_uses_core_subdir(self, tmp_path: pathlib.Path) -> None:
        meta = self._meta(engine_version="branch:develop")
        result = _resolve_installed_config_path("sse_engine", "config.yaml", meta, tmp_path)
        assert result == tmp_path / "engine" / "core" / "config" / "config.yaml"

    def test_tranqu_branch_uses_env_root(self, tmp_path: pathlib.Path) -> None:
        meta = self._meta(tranqu_version="branch:main")
        result = _resolve_installed_config_path("tranqu", "config.yaml", meta, tmp_path)
        assert result == tmp_path / "tranqu" / "config" / "config.yaml"

    def test_gateway_branch_uses_env_root(self, tmp_path: pathlib.Path) -> None:
        meta = self._meta(gateway_version="branch:feature-x")
        result = _resolve_installed_config_path("gateway", "config.yaml", meta, tmp_path)
        assert result == tmp_path / "gateway" / "config" / "config.yaml"

    def test_no_install_root_returns_none_for_release(self, tmp_path: pathlib.Path) -> None:
        meta = {"engine_version": "v1.0.0"}
        assert _resolve_installed_config_path("core", "config.yaml", meta, tmp_path) is None

    def test_unknown_service_returns_none(self, tmp_path: pathlib.Path) -> None:
        assert _resolve_installed_config_path("unknown", "config.yaml", self._meta(), tmp_path) is None
