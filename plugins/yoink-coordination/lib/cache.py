"""Phase 3 fetch wrapper — passthrough stub. Phase 4 will replace this with a
disk-backed TTL cache (see spec §5.3 and journal 16). All other-team-member
fetches MUST go through `fetch_others` so the Phase 4 upgrade is a single-file
change."""
from __future__ import annotations
from typing import Callable, List, Dict

FetcherFn = Callable[[str, str], List[Dict]]


def fetch_others(login: str, label: str, fetcher: FetcherFn) -> List[Dict]:
    """Return the list of other team members' open status issues.

    Phase 3: always calls `fetcher(login, label)` and returns its result unchanged.
    Phase 4: will read from ~/.cache/yoink/others-<slug>.json with mtime TTL,
    falling back to `fetcher` on miss.
    """
    return fetcher(login, label)
