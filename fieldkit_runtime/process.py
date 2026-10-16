"""Run exact commands without a shell and write files atomically."""

from __future__ import annotations

import fcntl
import hashlib
import os
import platform
import signal
import stat
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from . import __version__
from .common import Refusal


@dataclass(frozen=True)
class CommandResult:
    stdout: bytes
    stderr: bytes
    returncode: int


class CommandFailure(RuntimeError):
    def __init__(self, arguments: Sequence[str], result: CommandResult):
        name = Path(arguments[0]).name if arguments else "command"
        detail = result.stderr.decode(errors="replace").strip()[-1000:]
        super().__init__(f"{name} exited {result.returncode}" + (f": {detail}" if detail else ""))
        self.result = result


def _copy_stream(source: Any, destination: Any | None, capture: bytearray) -> None:
    while True:
        chunk = source.read1(8192) if hasattr(source, "read1") else source.read(8192)
        if not chunk:
            break
        capture.extend(chunk)
        if destination is not None:
            destination.buffer.write(chunk)
            destination.flush()


def _stop_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def _run_process(
    arguments: Sequence[str],
    timeout_seconds: float,
    destinations: tuple[Any | None, Any | None],
    *, environment: dict[str, str] | None = None,
) -> CommandResult:
    # Temper needs the caller's HOME, HF cache settings and PATH to locate its
    # download client. Temper owns its runtime isolation.
    process = subprocess.Popen(
        list(arguments), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, env=environment,
    )
    assert process.stdout is not None and process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    threads = [
        threading.Thread(target=_copy_stream, args=(process.stdout, destinations[0], stdout), daemon=True),
        threading.Thread(target=_copy_stream, args=(process.stderr, destinations[1], stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    interrupted = False
    try:
        returncode = process.wait(timeout=timeout_seconds if timeout_seconds > 0 else None)
    except subprocess.TimeoutExpired:
        timed_out = True
        _stop_group(process)
        returncode = 124
    except KeyboardInterrupt:
        interrupted = True
        _stop_group(process)
        returncode = 130
    for thread in threads:
        thread.join()
    process.stdout.close()
    process.stderr.close()
    if timed_out:
        stderr.extend(f"Field Kit stopped the command after {timeout_seconds:.0f} seconds\n".encode())
    if interrupted:
        stderr.extend(b"Field Kit stopped the command after interruption\n")
    return CommandResult(bytes(stdout), bytes(stderr), returncode)


def run_process(arguments: Sequence[str], timeout_seconds: float = 0) -> CommandResult:
    """Run one exact argv without a shell, teeing and retaining its output."""
    return _run_process(arguments, timeout_seconds, (sys.stdout, sys.stderr))


def run_process_silent(arguments: Sequence[str], timeout_seconds: float = 0, *, environment=None) -> CommandResult:
    """Run one exact argv while reserving stdout for a canonical CLI response."""
    return _run_process(arguments, timeout_seconds, (None, None), environment=environment)


Runner = Callable[[Sequence[str], float], CommandResult]


def capture_process(arguments: Sequence[str], timeout_seconds: float = 0) -> CommandResult:
    """Run one exact read-only identity command without writing to the terminal."""
    try:
        completed = subprocess.run(
            list(arguments),
            capture_output=True,
            check=False,
            timeout=timeout_seconds if timeout_seconds > 0 else None,
        )
    except subprocess.TimeoutExpired as error:
        return CommandResult(
            error.stdout or b"",
            (error.stderr or b"") + f"Field Kit stopped the command after {timeout_seconds:.0f} seconds\n".encode(),
            124,
        )
    return CommandResult(completed.stdout, completed.stderr, completed.returncode)


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    staged = Path(staged_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
    finally:
        if staged.exists():
            staged.unlink()


def write_new_or_same(path: Path, data: bytes, mode: int = 0o600) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
            raise Refusal(f"refusing to replace different existing file: {path}")
        return
    atomic_write(path, data, mode)


@contextmanager
def exclusive_lock(path: Path):
    if path.is_symlink() or not path.is_file():
        raise Refusal(f"lock is not a regular file: {path}")
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise Refusal(f"lock is not a regular file: {path}")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Refusal("another Field Kit command is using this run") from error
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def file_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def runtime_digest() -> str:
    value = hashlib.sha256()
    root = Path(__file__).resolve().parent
    for path in sorted(root.rglob("*.py")):
        data = path.read_bytes()
        value.update(path.relative_to(root).as_posix().encode() + b"\0" + len(data).to_bytes(8, "big") + data)
    return value.hexdigest()


def runtime_identity() -> dict[str, str]:
    python_path = Path(sys.executable).resolve()
    if python_path.is_symlink() or not python_path.is_file():
        raise Refusal(f"Python runtime is not a regular executable: {python_path}")
    return {
        "version": __version__,
        "sha256": runtime_digest(),
        "python_path": str(python_path),
        "python_version": platform.python_version(),
        "python_sha256": file_digest(python_path),
    }
