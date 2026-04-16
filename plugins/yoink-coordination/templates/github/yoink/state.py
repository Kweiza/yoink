"""Self-contained copy of yoink-coordination state parser for the
GitHub Action release script. Kept minimal — only parse_body / render_body
and the data shapes the script needs.

Sync this file when lib/state.py changes its on-disk schema. Marker
strings, JSON shape, and Session field set are the contract.
"""
from __future__ import annotations
import dataclasses
import json
from dataclasses import dataclass, field, asdict
from typing import Optional, Tuple, List

STATE_MARKER_BEGIN = "<!-- yoink:state-json-v1:begin"
STATE_MARKER_END = "yoink:state-json-v1:end -->"


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
    task_summary: Optional[str] = None
    # Legacy `last_heartbeat` key from pre-v0.3.28 bodies is preserved
    # via _extra and round-trips intact.
    _extra: dict = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self):
        for name in ("session_id", "worktree_path", "branch", "started_at"):
            v = getattr(self, name)
            if not isinstance(v, str) or not v:
                raise ValueError(
                    f"Session.{name} must be a non-empty string (got {v!r})"
                )


@dataclass
class State:
    updated_at: str
    sessions: List[Session] = field(default_factory=list)


def _build_session(sd: dict, known: set) -> Optional[Session]:
    try:
        s = Session(**{k: v for k, v in sd.items() if k in known})
        s._extra = {k: sd[k] for k in sd.keys() if k not in known and k != "_extra"}
        return s
    except (TypeError, ValueError):
        return None


def parse_body(body: str) -> Tuple[State, bool]:
    if not body or STATE_MARKER_BEGIN not in body:
        return State(updated_at=""), False
    try:
        start = body.index(STATE_MARKER_BEGIN) + len(STATE_MARKER_BEGIN)
        end = body.index(STATE_MARKER_END, start)
        raw = body[start:end].strip()
        data = json.loads(raw)
        known = {f.name for f in dataclasses.fields(Session) if f.name != "_extra"}
        sessions = [
            s for sd in data.get("sessions", []) if isinstance(sd, dict)
            for s in [_build_session(sd, known)] if s is not None
        ]
        return State(updated_at=data.get("updated_at", ""), sessions=sessions), False
    except (ValueError, TypeError, KeyError):
        return State(updated_at=""), True


def _session_to_dict(s: Session) -> dict:
    d = asdict(s)
    d.pop("_extra", None)
    d.update(s._extra)
    return d


_TASK_SUMMARY_MAX = 60


def format_task_cell(task_issue, task_summary):
    issue_short = None
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


def format_files_cell(declared_files):
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


def _cell(value):
    return str(value).replace("|", "\\|") if value is not None else "—"


def _basename(path):
    return path.rstrip("/").rsplit("/", 1)[-1] or path


def _render_summary(state, login):
    n = len(state.sessions)
    if n == 0:
        return f"**@{login}** — no active sessions"
    return f"**@{login}** — {n} active session{'s' if n != 1 else ''}"


def _render_table(state):
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


def render_body(state, login, preserve_tail_from=None):
    summary = _render_summary(state, login)
    table = _render_table(state)
    state_dict = {
        "updated_at": state.updated_at,
        "sessions": [_session_to_dict(s) for s in state.sessions],
    }
    json_block = (
        f"{STATE_MARKER_BEGIN}\n"
        f"{json.dumps(state_dict, indent=2)}\n"
        f"{STATE_MARKER_END}"
    )
    plugin_region = f"{summary}\n\n{table}\n\n{json_block}"
    tail = ""
    if preserve_tail_from and STATE_MARKER_END in preserve_tail_from:
        idx = preserve_tail_from.index(STATE_MARKER_END) + len(STATE_MARKER_END)
        tail = preserve_tail_from[idx:]
    return plugin_region + tail
