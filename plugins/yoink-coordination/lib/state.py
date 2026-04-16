"""State model + body serialization for yoink-coordination.
See spec §3.3 and §3.4."""
from __future__ import annotations
import dataclasses
import json
from dataclasses import dataclass, field, asdict
from typing import Optional, Tuple, List
import constants

@dataclass
class Session:
    session_id: str
    worktree_path: str
    branch: str
    task_issue: Optional[str]
    started_at: str
    declared_files: list
    driven_by: str
    claude_session_id: Optional[str]
    # v0.3.8+: human-entered 1-2 sentence summary of what this session is
    # doing. Set via /yoink-coordination:task. None until recorded.
    task_summary: Optional[str] = None
    # v0.3.28: legacy issue bodies may carry a `last_heartbeat` key that
    # parse_body routes through _extra, preserving round-trip. The field
    # itself was dropped because it only powered a display column and a
    # stale-session heuristic that Actions made obsolete.
    _extra: dict = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self):
        for name in ("session_id", "worktree_path", "branch", "started_at"):
            v = getattr(self, name)
            if not isinstance(v, str) or not v:
                raise ValueError(f"Session.{name} must be a non-empty string (got {v!r})")

@dataclass
class State:
    updated_at: str
    sessions: List[Session] = field(default_factory=list)

def dedup_key(s: Session) -> tuple:
    """Return a tuple that uniquely identifies this session within a user's issue.
    Shape is ('ccs', id) when claude_session_id is set, else ('wb', worktree, branch)."""
    if s.claude_session_id:
        return ("ccs", s.claude_session_id)
    return ("wb", s.worktree_path, s.branch)

def _build_session(sd: dict, known: set) -> Optional[Session]:
    try:
        s = Session(**{k: v for k, v in sd.items() if k in known})
        s._extra = {k: sd[k] for k in sd.keys() if k not in known and k != "_extra"}
        return s
    except (TypeError, ValueError):
        return None

def parse_body(body: str) -> Tuple[State, bool]:
    """Return (state, corrupt_flag). Corrupt flag True if markers exist but JSON broken."""
    if not body or constants.STATE_MARKER_BEGIN not in body:
        return State(updated_at=""), False
    try:
        start = body.index(constants.STATE_MARKER_BEGIN) + len(constants.STATE_MARKER_BEGIN)
        end = body.index(constants.STATE_MARKER_END, start)
        raw = body[start:end].strip()
        data = json.loads(raw)
        known = {f.name for f in dataclasses.fields(Session) if f.name != "_extra"}
        # Phase 3+ fields are preserved verbatim through _extra (spec §10.2)
        sessions = [s for sd in data.get("sessions", []) if isinstance(sd, dict)
                    for s in [_build_session(sd, known)] if s is not None]
        return State(updated_at=data.get("updated_at", ""), sessions=sessions), False
    except (ValueError, TypeError, KeyError):
        return State(updated_at=""), True

def body_exceeds_limit(body: str) -> bool:
    return len(body.encode("utf-8")) > constants.BODY_SIZE_LIMIT

def _session_to_dict(s: Session) -> dict:
    d = asdict(s)
    d.pop("_extra", None)
    d.update(s._extra)
    return d

def render_body(state: State, login: str, preserve_tail_from: Optional[str] = None) -> str:
    """Generate body. Plugin-owned region (top → end marker) is regenerated.
    preserve_tail_from: existing body; text below the end marker is appended."""
    summary = _render_summary(state, login)
    table = _render_table(state)
    state_dict = {
        "updated_at": state.updated_at,
        "sessions": [_session_to_dict(s) for s in state.sessions],
    }
    json_block = (
        f"{constants.STATE_MARKER_BEGIN}\n"
        f"{json.dumps(state_dict, indent=2)}\n"
        f"{constants.STATE_MARKER_END}"
    )
    plugin_region = f"{summary}\n\n{table}\n\n{json_block}"

    tail = ""
    if preserve_tail_from and constants.STATE_MARKER_END in preserve_tail_from:
        idx = preserve_tail_from.index(constants.STATE_MARKER_END) + len(constants.STATE_MARKER_END)
        tail = preserve_tail_from[idx:]
    return plugin_region + tail

def _render_summary(state: State, login: str) -> str:
    n = len(state.sessions)
    if n == 0:
        return f"**@{login}** — no active sessions"
    return f"**@{login}** — {n} active session{'s' if n != 1 else ''}"

def _cell(value) -> str:
    return str(value).replace("|", "\\|") if value is not None else "—"

_TASK_SUMMARY_MAX = 60


def format_task_cell(task_issue: Optional[str], task_summary: Optional[str]) -> str:
    """Compose the Task column cell from issue ref and summary text.

    Formats (precedence):
      issue + summary  → `#123 · <summary>` (summary truncated)
      issue only       → `#123`
      summary only     → `<summary>` (truncated)
      neither          → `—`

    The issue prefix strips the `repo/owner` leading part so the cell stays
    readable (`#123` rather than `kweiza/yoink#123` which is already clear
    from context).
    """
    issue_short: Optional[str] = None
    if task_issue:
        idx = task_issue.find("#")
        issue_short = task_issue[idx:] if idx != -1 else task_issue
    summary = (task_summary or "").strip()
    if len(summary) > _TASK_SUMMARY_MAX:
        summary = summary[: _TASK_SUMMARY_MAX - 1].rstrip() + "…"
    if issue_short and summary:
        return f"{issue_short} · {summary}"
    if issue_short:
        return issue_short
    if summary:
        return summary
    return "—"


def format_files_cell(declared_files: list) -> str:
    """Render declared_files for the human-facing table.

    0 entries → `—`
    1~3       → `foo.py, bar.py[, baz.py]`
    4+        → `foo.py, bar.py, baz.py (+N)`

    Order follows the list as stored (declaration order).
    """
    if not declared_files:
        return "—"
    paths = [str(e.get("path", "")) for e in declared_files if isinstance(e, dict)]
    paths = [p for p in paths if p]
    if not paths:
        return "—"
    if len(paths) <= 3:
        return ", ".join(paths)
    shown = ", ".join(paths[:3])
    return f"{shown} (+{len(paths) - 3})"


def _render_table(state: State) -> str:
    header = (
        "| Worktree | Branch | Task | Files | Started |\n"
        "|---|---|---|---|---|"
    )
    if not state.sessions:
        return header + "\n| _(none)_ | | | | |"
    rows = [
        f"| {_cell(_basename(s.worktree_path))} | {_cell(s.branch)} | "
        f"{_cell(format_task_cell(s.task_issue, s.task_summary))} | "
        f"{_cell(format_files_cell(s.declared_files or []))} | "
        f"{_cell(s.started_at)} |"
        for s in state.sessions
    ]
    return header + "\n" + "\n".join(rows)

def _basename(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1] or path
