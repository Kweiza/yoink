"""Tests for lib/task_cache — session-scoped task_summary cache."""
import importlib
from pathlib import Path


def test_task_cache_stamp_roundtrip(tmp_path, monkeypatch):
    import task_cache
    monkeypatch.setenv("YOINK_TASK_CACHE_ROOT", str(tmp_path / "cache"))
    importlib.reload(task_cache)
    assert task_cache.is_set("/wt", "feat/x") is False
    task_cache.mark_set("/wt", "feat/x")
    assert task_cache.is_set("/wt", "feat/x") is True
    # Different branch → different stamp
    assert task_cache.is_set("/wt", "other") is False


def test_task_cache_key_is_stable(tmp_path, monkeypatch):
    import task_cache
    monkeypatch.setenv("YOINK_TASK_CACHE_ROOT", str(tmp_path / "cache"))
    importlib.reload(task_cache)
    p1 = task_cache.stamp_path("/wt", "feat/x")
    p2 = task_cache.stamp_path("/wt", "feat/x")
    assert p1 == p2
    assert p1.parent == Path(str(tmp_path / "cache"))


def test_task_cache_failopen_on_ro_fs(tmp_path, monkeypatch):
    """mark_set must not raise when the cache root is unwritable."""
    import task_cache
    bad = tmp_path / "cache"
    bad.mkdir()
    bad.chmod(0o500)
    monkeypatch.setenv("YOINK_TASK_CACHE_ROOT", str(bad / "sub"))
    importlib.reload(task_cache)
    try:
        task_cache.mark_set("/wt", "b")  # should not raise
        assert task_cache.is_set("/wt", "b") is False
    finally:
        bad.chmod(0o700)
