import cache  # noqa


def test_fetch_others_delegates_to_provided_fetcher():
    calls = []
    def fake_fetcher(login, label):
        calls.append((login, label))
        return [{"number": 7, "assignees": [{"login": "alice"}], "body": "x"}]
    result = cache.fetch_others("me", "yoink:status", fake_fetcher)
    assert calls == [("me", "yoink:status")]
    assert result[0]["number"] == 7


def test_fetch_others_no_memoization_in_phase3():
    calls = []
    def fake_fetcher(login, label):
        calls.append((login, label))
        return []
    cache.fetch_others("me", "yoink:status", fake_fetcher)
    cache.fetch_others("me", "yoink:status", fake_fetcher)
    assert len(calls) == 2  # no caching in Phase 3
