"""Load or initialize the local Manager's node identity."""

from __future__ import annotations

import os
import re
import socket
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from oqtopus_manager.models.node import NODE_NAME_PATTERN, SelfNode

if TYPE_CHECKING:
    from oqtopus_manager.config import AppConfig


def normalize_node_name(raw: str) -> str:
    """Turn a hostname into a valid node name while preserving case.

    Returns:
        The normalized name, or ``node`` if it is empty.

    """
    normalized = re.sub(r"[^A-Za-z0-9_.:-]", "-", raw)
    return re.sub(r"^[^A-Za-z0-9]+", "", normalized) or "node"


def default_node_name() -> str:
    """Use the short hostname as the default node name.

    Returns:
        The normalized short hostname.

    """
    return normalize_node_name(socket.gethostname().split(".", 1)[0])


def _parse_self_node(data: object) -> SelfNode:
    if not isinstance(data, dict):
        msg = "the top level must be a mapping"
        raise TypeError(msg)
    entries = data.get("nodes")
    if not isinstance(entries, list) or not all(
        isinstance(item, dict) for item in entries
    ):
        msg = "nodes must be a list of mappings"
        raise TypeError(msg)
    self_entries = [item for item in entries if item.get("self") is True]
    if len(self_entries) != 1:
        msg = "expected exactly one entry with self: true"
        raise ValueError(msg)

    entry = self_entries[0]
    if "address" in entry:
        msg = "the self entry must not have an address"
        raise ValueError(msg)
    name = entry.get("name")
    if not isinstance(name, str) or not NODE_NAME_PATTERN.fullmatch(name):
        msg = "the self entry must have a valid name"
        raise ValueError(msg)
    node_id = entry.get("node_id")
    if not isinstance(node_id, str):
        msg = "the self entry must have a UUID string as its node_id"
        raise TypeError(msg)
    uuid.UUID(node_id)
    return SelfNode(name=name, node_id=node_id)


def _write_atomically(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def ensure_self_node(cfg: AppConfig) -> SelfNode:
    """Read the self entry or create it on first startup.

    Returns:
        The identity stored in the self entry.

    Raises:
        ValueError: If the identity file is invalid, unreadable, or unwritable.

    """
    path = cfg.nodes_file
    try:
        return _parse_self_node(yaml.safe_load(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        pass
    except OSError as exc:
        msg = f"Cannot read nodes.yaml: {path} ({exc})"
        raise ValueError(msg) from exc
    except (TypeError, ValueError, yaml.YAMLError) as exc:
        msg = (
            f"nodes.yaml has no valid self entry: {path} ({exc})\n"
            "This file holds this node's identity and is never regenerated "
            "automatically.\n"
            "If this host is a clone of another node, delete the file and restart\n"
            "(registrations stored in it will be lost)."
        )
        raise ValueError(msg) from exc

    node = SelfNode(name=default_node_name(), node_id=str(uuid.uuid7()))
    text = yaml.safe_dump(
        {"nodes": [{"name": node.name, "self": True, "node_id": node.node_id}]},
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomically(path, text)
    except OSError as exc:
        msg = f"Cannot create nodes.yaml: {path} ({exc})"
        raise ValueError(msg) from exc
    return node
