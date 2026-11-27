"""Local node identity model."""

import re
from dataclasses import dataclass

NODE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


@dataclass(frozen=True)
class SelfNode:
    """Identity of the local Manager node."""

    name: str
    node_id: str
