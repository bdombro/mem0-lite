"""Keep-open + cooperative yield. Uses temp MEM0_DIR; never ~/.mem0."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mem0_lite.core as m  # noqa: E402


class FakeMemory:
    def close(self) -> None:
        pass


def _prep_env(mem0_dir: str, timeout: str) -> None:
    os.environ["MEM0_DIR"] = mem0_dir
    os.environ["MEM0_TELEMETRY"] = "False"
    os.environ["MEM0_LITE_LOCK_TIMEOUT"] = timeout
    os.environ.pop("MEM0_LITE_DATA_DIR", None)


def _install_fake() -> None:
    import mem0_lite.core as mod

    mod._open_memory = lambda: FakeMemory()  # type: ignore[method-assign]


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
        [sys.executable, str(Path(__file__).resolve()), mode, *args],
        cwd=str(ROOT),
        env=env,
    )


def _work_hold_idle(mem0_dir: str, timeout: str, ready: Path, yielded: Path, stop: Path) -> None:
    _prep_env(mem0_dir, timeout)
    _install_fake()
    import mem0_lite.core as mod

    with mod.memory_session():
        pass
    _touch(ready)
    while not stop.exists():
        if mod._memory is None:
            _touch(yielded)
            return
        time.sleep(0.01)


def _work_take(mem0_dir: str, timeout: str, result: Path) -> None:
    _prep_env(mem0_dir, timeout)
    _install_fake()
    import mem0_lite.core as mod

    try:
        with mod.memory_session() as memory:
            result.write_text(f"ok {memory is not None}", encoding="utf-8")
    except mod.StoreBusy as exc:
        result.write_text(f"busy {exc.timeout}", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        result.write_text(f"err {type(exc).__name__} {exc}", encoding="utf-8")


def _work_hold_session(mem0_dir: str, timeout: str, ready: Path, release: Path) -> None:
    _prep_env(mem0_dir, timeout)
    _install_fake()
    import mem0_lite.core as mod

    with mod.memory_session():
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
    import mem0_lite.core as mod

    with mod.memory_session() as first:
        first_id = id(first)
    _touch(holding)
    while mod._memory is not None and not stop.exists():
        time.sleep(0.01)
    if mod._memory is not None:
        return
    _touch(yielded)
    with mod.memory_session() as second:
        reacquired.write_text("1" if id(second) != first_id else "0", encoding="utf-8")


_WORKERS = {
    "hold_idle": lambda a: _work_hold_idle(a[0], a[1], Path(a[2]), Path(a[3]), Path(a[4])),
    "take": lambda a: _work_take(a[0], a[1], Path(a[2])),
    "hold_session": lambda a: _work_hold_session(a[0], a[1], Path(a[2]), Path(a[3])),
    "reacquire": lambda a: _work_reacquire(a[0], a[1], Path(a[2]), Path(a[3]), Path(a[4]), Path(a[5])),
}


class KeepOpenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = {
            key: os.environ.get(key)
            for key in (
                "MEM0_DIR",
                "MEM0_LITE_DATA_DIR",
                "MEM0_TELEMETRY",
                "MEM0_LITE_LOCK_TIMEOUT",
            )
        }
        os.environ["MEM0_DIR"] = self.tmp.name
        os.environ["MEM0_TELEMETRY"] = "False"
        os.environ["MEM0_LITE_LOCK_TIMEOUT"] = "5"
        os.environ.pop("MEM0_LITE_DATA_DIR", None)
        self._orig_open = m._open_memory
        self.opens = 0

        def fake_open() -> FakeMemory:
            self.opens += 1
            return FakeMemory()

        m._open_memory = fake_open  # type: ignore[method-assign]
        m._reset_holder()

    def tearDown(self) -> None:
        m._reset_holder()
        m._open_memory = self._orig_open
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def _path(self, name: str) -> Path:
        return Path(self.tmp.name) / name

    def test_sequential_sessions_reuse_memory(self) -> None:
        with m.memory_session() as first:
            first_id = id(first)
        with m.memory_session() as second:
            second_id = id(second)
        self.assertEqual(first_id, second_id)
        self.assertEqual(self.opens, 1)
        self.assertIs(m._memory, second)

    def test_second_session_is_cheap(self) -> None:
        slow_opens = {"n": 0}

        def slow_open() -> FakeMemory:
            slow_opens["n"] += 1
            time.sleep(0.05)
            return FakeMemory()

        m._open_memory = slow_open  # type: ignore[method-assign]
        with m.memory_session():
            pass
        start = time.perf_counter()
        with m.memory_session():
            pass
        elapsed_ms = (time.perf_counter() - start) * 1000
        self.assertEqual(slow_opens["n"], 1)
        self.assertLess(elapsed_ms, 20)

    def test_in_process_threads_reuse_holder(self) -> None:
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
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(ids), 2)
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(self.opens, 1)

    def test_stale_want_does_not_yield(self) -> None:
        with m.memory_session() as memory:
            held = memory
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        (Path(self.tmp.name) / "lite.want").write_text(f"{proc.pid}\n", encoding="utf-8")
        time.sleep(0.2)
        self.assertIs(m._memory, held)
        with m.memory_session() as again:
            self.assertIs(again, held)
        self.assertEqual(self.opens, 1)
        self.assertFalse((Path(self.tmp.name) / "lite.want").exists())

    def test_idle_yield_to_other_process(self) -> None:
        ready, yielded, stop, result = (self._path(n) for n in ("ready", "yielded", "stop", "result"))
        holder = _spawn("hold_idle", self.tmp.name, "5", str(ready), str(yielded), str(stop))
        waiter = None
        try:
            self.assertTrue(_wait_file(ready, 5), "holder never became idle")
            waiter = _spawn("take", self.tmp.name, "5", str(result))
            self.assertTrue(_wait_file(result, 5), "waiter never finished")
            self.assertTrue(result.read_text(encoding="utf-8").startswith("ok"), result.read_text())
            self.assertTrue(_wait_file(yielded, 5), "holder did not yield while idle")
        finally:
            _touch(stop)
            if waiter is not None:
                _stop_proc(waiter)
            _stop_proc(holder)

    def test_timeout_while_op_in_progress(self) -> None:
        ready, release, result = (self._path(n) for n in ("ready", "release", "result"))
        holder = _spawn("hold_session", self.tmp.name, "5", str(ready), str(release))
        waiter = None
        try:
            self.assertTrue(_wait_file(ready, 5), "holder never entered session")
            waiter = _spawn("take", self.tmp.name, "0.4", str(result))
            self.assertTrue(_wait_file(result, 5), "waiter never finished")
            self.assertTrue(result.read_text(encoding="utf-8").startswith("busy"), result.read_text())
        finally:
            _touch(release)
            if waiter is not None:
                _stop_proc(waiter)
            _stop_proc(holder)

    def test_previous_holder_reopens_after_yield(self) -> None:
        holding, yielded, reacquired, stop_a = (
            self._path(n) for n in ("holding", "yielded", "reacquired", "stop_a")
        )
        ready_b, yielded_b, stop_b = (self._path(n) for n in ("ready_b", "yielded_b", "stop_b"))
        process_a = _spawn(
            "reacquire",
            self.tmp.name,
            "5",
            str(holding),
            str(yielded),
            str(reacquired),
            str(stop_a),
        )
        process_b = None
        try:
            self.assertTrue(_wait_file(holding, 5), "A never became holder")
            process_b = _spawn("hold_idle", self.tmp.name, "5", str(ready_b), str(yielded_b), str(stop_b))
            self.assertTrue(_wait_file(yielded, 5), "A did not yield to B")
            self.assertTrue(_wait_file(ready_b, 5), "B never became holder")
            self.assertTrue(_wait_file(reacquired, 5), "A did not reacquire")
            self.assertEqual(reacquired.read_text(encoding="utf-8").strip(), "1")
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
        unittest.main()
