# tests/test_lock.py
import os
import threading
import time
import lock

def test_acquire_release(tmp_path):
    p = tmp_path / "x.lock"
    with lock.acquire(p, timeout=1):
        assert p.exists()

def test_reentrant_via_different_threads_serialize(tmp_path):
    p = tmp_path / "x.lock"
    order = []
    def worker(name, hold):
        with lock.acquire(p, timeout=5):
            order.append(f"{name}-in")
            time.sleep(hold)
            order.append(f"{name}-out")
    t1 = threading.Thread(target=worker, args=("A", 0.2))
    t2 = threading.Thread(target=worker, args=("B", 0.0))
    t1.start(); time.sleep(0.05); t2.start()
    t1.join(); t2.join()
    # A must complete fully before B begins
    assert order == ["A-in", "A-out", "B-in", "B-out"]

def test_timeout_raises(tmp_path):
    p = tmp_path / "x.lock"
    evt = threading.Event()
    def holder():
        with lock.acquire(p, timeout=5):
            evt.set()
            time.sleep(1.0)
    t = threading.Thread(target=holder); t.start(); evt.wait()
    try:
        with lock.acquire(p, timeout=0.1):
            raise AssertionError("should have timed out")
    except lock.LockTimeout:
        pass
    t.join()
