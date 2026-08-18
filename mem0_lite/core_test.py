"""Keep-open + cooperative yield. Uses temp MEM0_DIR; never ~/.mem0.

Subprocess workers: python -m mem0_lite.core_test <mode> ...
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import mem0_lite.core as m

ROOT = Path(__file__).resolve().parents[1]


class FakeMemory:
    def close(self) -> None:
        pass


def _prep_env(mem0_dir: str, timeout: str) -> None:
    os.environ["MEM0_DIR"] = mem0_dir
    os.environ["MEM0_TELEMETRY"] = "False"
    os.environ["MEM0_LITE_LOCK_TIMEOUT"] = timeout
    os.environ.pop("MEM0_LITE_DATA_DIR", None)


def _install_fake() -> None:
    m._open_memory = lambda: FakeMemory()  # type: ignore[method-assign]


def _touch(path: Path) -> None:
    path.write_text("1", encoding="utf-8")


def _wait_file(path: Path, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.02)
    return False


def _stop_proc(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


def _spawn(mode: str, *args: str) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["MEM0_TELEMETRY"] = "False"
    return subprocess.Popen(
        [sys.executable, "-m", "mem0_lite.core_test", mode, *args],
        cwd=str(ROOT),
        env=env,
    )


def _work_hold_idle(mem0_dir: str, timeout: str, ready: Path, yielded: Path, stop: Path) -> None:
    _prep_env(mem0_dir, timeout)
    _install_fake()
    with m.memory_session():
        pass
    _touch(ready)
    while not stop.exists():
        if m._memory is None:
            _touch(yielded)
            return
        time.sleep(0.01)


def _work_take(mem0_dir: str, timeout: str, result: Path) -> None:
    _prep_env(mem0_dir, timeout)
    _install_fake()
    try:
        with m.memory_session() as memory:
            result.write_text(f"ok {memory is not None}", encoding="utf-8")
    except m.StoreBusy as exc:
        result.write_text(f"busy {exc.timeout}", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        result.write_text(f"err {type(exc).__name__} {exc}", encoding="utf-8")


def _work_hold_session(mem0_dir: str, timeout: str, ready: Path, release: Path) -> None:
    _prep_env(mem0_dir, timeout)
    _install_fake()
    with m.memory_session():
        _touch(ready)
        while not release.exists():
            time.sleep(0.01)


def _work_reacquire(
    mem0_dir: str,
    timeout: str,
    holding: Path,
    yielded: Path,
    reacquired: Path,
    stop: Path,
) -> None:
    _prep_env(mem0_dir, timeout)
    _install_fake()
    with m.memory_session() as first:
        first_id = id(first)
    _touch(holding)
    while m._memory is not None and not stop.exists():
        time.sleep(0.01)
    if m._memory is not None:
        return
    _touch(yielded)
    with m.memory_session() as second:
        reacquired.write_text("1" if id(second) != first_id else "0", encoding="utf-8")


_WORKERS = {
    "hold_idle": lambda a: _work_hold_idle(a[0], a[1], Path(a[2]), Path(a[3]), Path(a[4])),
    "take": lambda a: _work_take(a[0], a[1], Path(a[2])),
    "hold_session": lambda a: _work_hold_session(a[0], a[1], Path(a[2]), Path(a[3])),
    "reacquire": lambda a: _work_reacquire(a[0], a[1], Path(a[2]), Path(a[3]), Path(a[4]), Path(a[5])),
}


@pytest.fixture
def fake_store(mem0_home, monkeypatch):
    """Open Memory() as FakeMemory; count opens. Restores holder after the test."""
    opens = {"n": 0}

    def fake_open() -> FakeMemory:
        opens["n"] += 1
        return FakeMemory()

    monkeypatch.setattr(m, "_open_memory", fake_open)
    m._reset_holder()
    yield mem0_home, opens
    m._reset_holder()


def test_sequential_sessions_reuse_memory(fake_store) -> None:
    home, opens = fake_store
    with m.memory_session() as first:
        first_id = id(first)
    with m.memory_session() as second:
        second_id = id(second)
    assert first_id == second_id
    assert opens["n"] == 1
    assert m._memory is second
    assert home.is_dir()


def test_second_session_is_cheap(fake_store, monkeypatch) -> None:
    slow_opens = {"n": 0}

    def slow_open() -> FakeMemory:
        slow_opens["n"] += 1
        time.sleep(0.05)
        return FakeMemory()

    monkeypatch.setattr(m, "_open_memory", slow_open)
    with m.memory_session():
        pass
    start = time.perf_counter()
    with m.memory_session():
        pass
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert slow_opens["n"] == 1
    assert elapsed_ms < 20


def test_in_process_threads_reuse_holder(fake_store) -> None:
    _, opens = fake_store
    ids: list[int] = []

    def worker() -> None:
        with m.memory_session() as memory:
            ids.append(id(memory))
            time.sleep(0.05)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()
    assert ids == [ids[0], ids[0]]
    assert opens["n"] == 1


def test_stale_want_does_not_yield(fake_store) -> None:
    home, opens = fake_store
    with m.memory_session() as memory:
        held = memory
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    (home / "lite.want").write_text(f"{proc.pid}\n", encoding="utf-8")
    time.sleep(0.2)
    assert m._memory is held
    with m.memory_session() as again:
        assert again is held
    assert opens["n"] == 1
    assert not (home / "lite.want").exists()


def test_idle_yield_to_other_process(fake_store) -> None:
    home, _ = fake_store
    ready, yielded, stop, result = (home / n for n in ("ready", "yielded", "stop", "result"))
    holder = _spawn("hold_idle", str(home), "5", str(ready), str(yielded), str(stop))
    waiter = None
    try:
        assert _wait_file(ready, 5), "holder never became idle"
        waiter = _spawn("take", str(home), "5", str(result))
        assert _wait_file(result, 5), "waiter never finished"
        assert result.read_text(encoding="utf-8").startswith("ok"), result.read_text()
        assert _wait_file(yielded, 5), "holder did not yield while idle"
    finally:
        _touch(stop)
        if waiter is not None:
            _stop_proc(waiter)
        _stop_proc(holder)


def test_timeout_while_op_in_progress(fake_store) -> None:
    home, _ = fake_store
    ready, release, result = (home / n for n in ("ready", "release", "result"))
    holder = _spawn("hold_session", str(home), "5", str(ready), str(release))
    waiter = None
    try:
        assert _wait_file(ready, 5), "holder never entered session"
        waiter = _spawn("take", str(home), "0.4", str(result))
        assert _wait_file(result, 5), "waiter never finished"
        assert result.read_text(encoding="utf-8").startswith("busy"), result.read_text()
    finally:
        _touch(release)
        if waiter is not None:
            _stop_proc(waiter)
        _stop_proc(holder)


def test_previous_holder_reopens_after_yield(fake_store) -> None:
    home, _ = fake_store
    holding, yielded, reacquired, stop_a = (home / n for n in ("holding", "yielded", "reacquired", "stop_a"))
    ready_b, yielded_b, stop_b = (home / n for n in ("ready_b", "yielded_b", "stop_b"))
    process_a = _spawn(
        "reacquire",
        str(home),
        "5",
        str(holding),
        str(yielded),
        str(reacquired),
        str(stop_a),
    )
    process_b = None
    try:
        assert _wait_file(holding, 5), "A never became holder"
        process_b = _spawn("hold_idle", str(home), "5", str(ready_b), str(yielded_b), str(stop_b))
        assert _wait_file(yielded, 5), "A did not yield to B"
        assert _wait_file(ready_b, 5), "B never became holder"
        assert _wait_file(reacquired, 5), "A did not reacquire"
        assert reacquired.read_text(encoding="utf-8").strip() == "1"
    finally:
        _touch(stop_a)
        _touch(stop_b)
        if process_b is not None:
            _stop_proc(process_b)
        _stop_proc(process_a)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in _WORKERS:
        _WORKERS[sys.argv[1]](sys.argv[2:])
    else:
        raise SystemExit("run via pytest (or pass a worker mode)")
