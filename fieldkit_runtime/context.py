"""Collaborators that touch the machine. Production uses DEFAULT; tests pass fakes."""
from __future__ import annotations
from dataclasses import dataclass
import sys
from typing import Any, Callable
from . import storage, watcher
from .probe import ManagedProbe
from .serving import monitored_call
from .temper import Temper

@dataclass(frozen=True)
class Context:
    temper: Callable[[Any], Any] = Temper              # Temper(path): the installed CLI
    probe: Callable[..., Any] = ManagedProbe            # one owned `temper execution serve`
    call: Callable[..., Any] = monitored_call           # bounded HTTP through that probe
    sample: Callable[..., Any] = watcher.sample_macos   # one resource sample of bound processes
    volume: Callable[[Any], Any] = storage.describe     # volume facts for a path
    keep_awake: bool = sys.platform == "darwin"         # caffeinate while an attempt runs

DEFAULT = Context()
