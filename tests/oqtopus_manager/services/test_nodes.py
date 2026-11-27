"""Tests for local node identity persistence and validation."""

import os
import pathlib
import stat
import uuid

import pytest
import yaml

from oqtopus_manager.config import AppConfig
from oqtopus_manager.services import nodes


@pytest.fixture
def cfg(tmp_path: pathlib.Path) -> AppConfig:
    return AppConfig.model_construct(nodes_file=tmp_path / "config/nodes.yaml")


def test_first_start_creates_self_entry_and_restart_preserves_file(
    cfg: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nodes.socket, "gethostname", lambda: "deviceA.lab.example.jp")

    first = nodes.ensure_self_node(cfg)

    assert first.name == "deviceA"
    assert uuid.UUID(first.node_id).version == 7
    original = cfg.nodes_file.read_bytes()
    (entry,) = yaml.safe_load(original)["nodes"]
    assert list(entry) == ["name", "self", "node_id"]
    assert entry == {"name": "deviceA", "self": True, "node_id": first.node_id}
    assert entry["self"] is True
    original_stat = cfg.nodes_file.stat()
    assert stat.S_IMODE(original_stat.st_mode) == 0o600

    monkeypatch.setattr(nodes.socket, "gethostname", lambda: "other.example.jp")
    assert nodes.ensure_self_node(cfg) == first
    assert cfg.nodes_file.read_bytes() == original
    assert cfg.nodes_file.stat().st_mtime_ns == original_stat.st_mtime_ns


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("._:- Device_A:1-2", "Device_A:1-2"),
        ("No de/名", "No-de--"),
        ("_--名", "node"),
        ("", "node"),
    ],
)
def test_normalize_node_name(raw: str, expected: str) -> None:
    assert nodes.normalize_node_name(raw) == expected


def test_read_renamed_node_with_non_v7_uuid_and_unknown_data(cfg: AppConfig) -> None:
    first = nodes.ensure_self_node(cfg)
    node_id = str(uuid.uuid4())
    self_entry = {
        "name": first.name, "self": True, "node_id": node_id, "note": "preserve"
    }
    data = {
        "nodes": [
            self_entry,
            {"name": "remote", "address": "https://remote.example.jp", "tls": True},
        ],
        "extra": {"keep": "this"},
    }
    cfg.nodes_file.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert nodes.ensure_self_node(cfg).node_id == node_id

    self_entry["name"] = "Renamed:node"
    original = "# Operator comment\n" + yaml.safe_dump(data)
    cfg.nodes_file.write_text(original, encoding="utf-8")

    renamed = nodes.ensure_self_node(cfg)
    assert renamed.name == "Renamed:node"
    assert renamed.node_id == node_id
    assert cfg.nodes_file.read_text(encoding="utf-8") == original


_NODE_ID = "0193f2a7-8c31-7b4e-9f02-6d1a5c8e4b77"
_SELF_ENTRY = f"- {{name: deviceA, self: true, node_id: {_NODE_ID}}}\n"


@pytest.mark.parametrize(
    "content",
    [
        "nodes: [",
        "[]",
        "{}",
        "nodes: {}",
        "nodes: [remote]",
        "nodes: []",
        f"nodes: [{{name: deviceA, self: 1, node_id: {_NODE_ID}}}]",
        f"nodes:\n{_SELF_ENTRY}{_SELF_ENTRY}",
        "nodes: [{name: deviceA, self: true}]",
        "nodes: [{name: deviceA, self: true, node_id: invalid}]",
        "nodes: [{name: deviceA, self: true, node_id: 123}]",
        f"nodes: [{{self: true, node_id: {_NODE_ID}}}]",
        f"nodes: [{{name: _invalid, self: true, node_id: {_NODE_ID}}}]",
        f"nodes: [{{name: deviceA, self: true, node_id: {_NODE_ID}, address: null}}]",
    ],
)
def test_invalid_nodes_file_is_preserved(cfg: AppConfig, content: str) -> None:
    cfg.nodes_file.parent.mkdir()
    original = content.encode("utf-8")
    cfg.nodes_file.write_bytes(original)

    with pytest.raises(ValueError, match="nodes.yaml has no valid self entry") as exc:
        nodes.ensure_self_node(cfg)

    assert str(cfg.nodes_file) in str(exc.value)
    assert "never regenerated automatically" in str(exc.value)
    assert cfg.nodes_file.read_bytes() == original


@pytest.mark.skipif(os.geteuid() == 0, reason="Root can write to a read-only directory")
def test_unwritable_directory_reports_path(cfg: AppConfig) -> None:
    parent = cfg.nodes_file.parent
    parent.mkdir(mode=0o500)
    try:
        with pytest.raises(ValueError, match="Permission denied") as exc:
            nodes.ensure_self_node(cfg)
        assert str(cfg.nodes_file) in str(exc.value)
    finally:
        parent.chmod(0o700)


@pytest.fixture
def fail_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    def replace_error(source: pathlib.Path, destination: pathlib.Path) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(nodes.os, "replace", replace_error)


def test_failed_first_write_cleans_up(cfg: AppConfig, fail_replace: None) -> None:
    with pytest.raises(ValueError, match="injected replace failure") as exc:
        nodes.ensure_self_node(cfg)

    assert str(cfg.nodes_file) in str(exc.value)
    assert list(cfg.nodes_file.parent.iterdir()) == []


def test_failed_atomic_replace_preserves_existing_file(
    cfg: AppConfig, fail_replace: None
) -> None:
    cfg.nodes_file.parent.mkdir()
    cfg.nodes_file.write_bytes(b"original\n")

    with pytest.raises(OSError, match="injected replace failure"):
        nodes._write_atomically(cfg.nodes_file, "replacement\n")

    assert cfg.nodes_file.read_bytes() == b"original\n"
    assert list(cfg.nodes_file.parent.iterdir()) == [cfg.nodes_file]
