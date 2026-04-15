"""Conflict policy — conflict_mode branch + block_paths stub.

Phase 3 surface:
- `decide(mode, conflicting_owners)` — pure function returning a Decision.
- `is_phase4_block_path(path)` — always returns False in Phase 3.

Per spec §2 "yoink internal errors → fail-open": unknown mode values never block.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict


@dataclass
class Decision:
    should_block: bool
    should_warn: bool


def decide(mode: str, conflicting_owners: List[Dict]) -> Decision:
    if not conflicting_owners:
        return Decision(should_block=False, should_warn=False)
    if mode == "block":
        return Decision(should_block=True, should_warn=True)
    # advisory — and unknown modes fail-open as advisory
    return Decision(should_block=False, should_warn=True)


def is_phase4_block_path(path: str) -> bool:
    """Phase 4 stub. Always False in Phase 3 regardless of config."""
    return False
