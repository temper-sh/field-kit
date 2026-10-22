"""What an attempt measures. Two kinds share everything else:

    temper  an exact preset lock served by the installed Temper; any machine;
            the only source of catalog evidence
    local   an engine binary, exact arguments and model files on this machine,
            before Temper supports them; research evidence only

Field Kit never builds or installs a local engine. It records its identity,
starts it under the same watch and proves its shutdown itself.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import time
from . import lockview, records, storage
from .adapters import engines, interfaces
from .common import IDENTITY, Refusal, digest, read_regular
from .measurements import watch_spec
from .probe import ProbeError
from .process import file_digest, run_process_progress
from .serving import HTTPResponseError

LOCAL_SCHEMA = "field-kit-local-engine/v1"
REQUIRED = {"schema", "id", "interface", "engine_adapter", "executable", "arguments", "files", "model", "source"}
OPTIONAL = {"environment", "context_window_tokens"}
NAME = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
SHA256_NAME = re.compile(r"^[0-9a-f]{64}$")

def software_digest(executable: Path) -> str:
    """Every regular file beside the executable, so a rebuilt library changes identity too."""
    value = hashlib.sha256()
    for path in sorted(executable.parent.iterdir()):
        if path.is_file() and not path.is_symlink():
            value.update(path.name.encode() + b"\0" + file_digest(path).encode() + b"\n")
    return value.hexdigest()

def file_identity(path: Path, known: dict | None = None) -> dict:
    """Size, modification time and SHA-256. A Hugging Face blob is named by its SHA-256."""
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise Refusal(f"local engine file is missing or not a regular file: {path}")
    stat = resolved.stat()
    record = {"path": str(resolved), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    if known and all(known.get(key) == record[key] for key in record):
        return {**record, "sha256": known["sha256"], "sha256_source": known["sha256_source"]}
    if resolved.parent.name == "blobs" and SHA256_NAME.fullmatch(resolved.name):
        return {**record, "sha256": resolved.name, "sha256_source": "hugging-face-blob-name"}
    return {**record, "sha256": file_digest(resolved), "sha256_source": "computed"}

def local_candidate(path, known=None):
    """Validate one local-engine declaration and record exact identities."""
    raw = read_regular(Path(path), 1024 * 1024)
    try:
        spec = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Refusal(f"invalid local engine declaration {path}: {error}") from error
    if not isinstance(spec, dict) or spec.get("schema") != LOCAL_SCHEMA:
        raise Refusal(f"a local engine declaration needs schema {LOCAL_SCHEMA}")
    if not REQUIRED <= set(spec) or set(spec) - REQUIRED - OPTIONAL:
        raise Refusal("local engine fields: " + ", ".join(sorted(REQUIRED)) + "; optional: " + ", ".join(sorted(OPTIONAL)))
    if not isinstance(spec["id"], str) or not IDENTITY.fullmatch(spec["id"]):
        raise Refusal("local engine id is a plain lowercase ID")
    interface = interfaces.resolve(spec["interface"])
    engine = engines.resolve(spec["engine_adapter"])
    for key in ("model", "source"):
        if not isinstance(spec[key], str) or not spec[key].strip():
            raise Refusal(f"local engine needs a {key}")
    executable = Path(spec["executable"])
    if not executable.is_absolute():
        raise Refusal("local engine executable must be an absolute path")
    executable = executable.resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise Refusal(f"local engine executable is not an executable file: {spec['executable']}")
    files = spec["files"]
    if not isinstance(files, dict) or not all(isinstance(name, str) and NAME.fullmatch(name) and isinstance(value, str)
                                              and Path(value).is_absolute() for name, value in files.items()):
        raise Refusal("local engine files map plain names to absolute paths")
    arguments = spec["arguments"]
    if not isinstance(arguments, list) or not all(isinstance(value, str) for value in arguments):
        raise Refusal("local engine arguments are a list of strings")
    names = [name for value in arguments for name in PLACEHOLDER.findall(value)]
    if "port" not in names:
        raise Refusal("local engine arguments must contain {port}; Field Kit chooses a free loopback port")
    unknown = [name for name in names if name != "port" and not (name.startswith("file:") and name[5:] in files)]
    if unknown:
        raise Refusal("unknown placeholders in local engine arguments: " + ", ".join(sorted(set(unknown))))
    environment = spec.get("environment", {})
    if not isinstance(environment, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in environment.items()):
        raise Refusal("local engine environment maps names to strings")
    window = spec.get("context_window_tokens")
    if window is not None and (type(window) is not int or window <= 0):
        raise Refusal("local engine context_window_tokens is a positive integer")
    known = known or {}
    return {"id": spec["id"], "subject": "local", "spec": raw.decode(), "spec_sha256": digest(raw),
            "interface": interface.IDENTIFIER, "family": engine.family, "engine_adapter": engine.identifier,
            "execution": {"preset": spec["id"], "model": spec["model"]},
            "executable": {"path": str(executable), "sha256": file_digest(executable),
                           "software_sha256": software_digest(executable)},
            "files": {name: file_identity(Path(value), known.get(name)) for name, value in sorted(files.items())}}

def validate_local_variant(base, variant, maximum_context):
    """Arguments, environment and model label may change; the binary and files may not."""
    if variant["subject"] != "local" or variant["executable"]["software_sha256"] != base["executable"]["software_sha256"]:
        raise Refusal("variant changes the approved local engine software; it needs a new study plan")
    if {name: row["sha256"] for name, row in variant["files"].items()} != {name: row["sha256"] for name, row in base["files"].items()}:
        raise Refusal("variant changes approved local engine files; it needs a new study plan")
    if (variant["interface"], variant["engine_adapter"]) != (base["interface"], base["engine_adapter"]):
        raise Refusal("variant changes the local engine interface or adapter; it needs a new study plan")
    window = json.loads(variant["spec"]).get("context_window_tokens")
    if window is not None and window > maximum_context:
        raise Refusal("variant exceeds the approved context ceiling")

def describe_local(row):
    spec = json.loads(row["spec"])
    return {"executable": row["executable"], "arguments": spec["arguments"], "environment": spec.get("environment", {}),
            "files": row["files"], "model": spec["model"], "source": spec["source"],
            "context_window_tokens": spec.get("context_window_tokens"),
            "evidence": "research only: never a catalog profile; a Temper rerun of the same workload is needed"}

class TemperSubject:
    kind = "temper"

    def __init__(self, attempt):
        self.attempt = attempt
        self.model = attempt.run["execution"]["preset"]

    def health_path(self, engine):
        return f"/upstream/{self.model}{engine.health_path}"

    def ready(self, session):
        # Temper's router starts the engine on demand and answers once it is ready.
        session.context.call(session.probe, self.health_path(session.engine), {}, self.attempt.remaining(1800), method="GET")

    def prepare(self):
        """Refuse before Temper downloads: a disconnected cache, a non-APFS root or too little disk."""
        attempt = self.attempt
        run, volume = attempt.run, attempt.context.volume
        storage.require_cache(attempt.cache, attempt.state["weights_cache_explicit"])
        run["storage"] = {"root": volume(attempt.root), "weights_cache": volume(attempt.cache)}
        storage.require_apfs(run["storage"]["root"], "the study root")
        absent = sum(entry["bytes"] for entry in lockview.hf_files(attempt.lock).values()
                     if not storage.present(attempt.cache, entry))
        installed = (attempt.root / "software" / "installations" / run["installation"]).exists()
        run["disk_needs_bytes"] = storage.require_space(run["storage"]["root"], run["storage"]["weights_cache"],
                                                        absent, 0 if installed else lockview.software_bytes([attempt.lock]))
        attempt.save()
        attempt.root.mkdir(parents=True, exist_ok=True)
        def progress(argv, timeout, **kwargs):
            return run_process_progress(argv, timeout, identity_path=attempt.files.prepare_command, **kwargs)
        attempt.temper.progress_runner = progress
        # Only the hub cache moves; HF_HOME, and with it the user's token, stays.
        environment = {**os.environ, "HF_HUB_CACHE": str(attempt.cache), "HF_HUB_DISABLE_TELEMETRY": "1"}
        prepared = attempt.temper.prepare(attempt.files.lock, attempt.root, run["installation"],
                                          attempt.remaining(21600), environment)
        if prepared.get("execution") != run["execution"]:
            raise Refusal("Temper prepared a different execution")
        run["preparation"] = {"generation": prepared["generation"]}

    def probe(self, files, listen):
        attempt, run = self.attempt, self.attempt.run
        probe = attempt.context.probe(temper=attempt.temper.path, root=attempt.root,
            installation=run["installation"], execution_lock=attempt.files.lock,
            generation=run["preparation"]["generation"], listen=listen, log_dir=files.process,
            watch_spec=watch_spec(run["resource_policy"], run["machine"], attempt.engine),
            router_ready_seconds=min(30, attempt.remaining(30)), log_bytes_max=records.LOG_BYTES)
        # Recovery compares this intent with Temper's final status before any cleanup.
        records.write(files.intent, {"root": str(attempt.root), "installation": run["installation"],
                                     "generation": run["preparation"]["generation"], "listen": probe.listen})
        return probe

class LocalSubject:
    kind = "local"

    def __init__(self, attempt):
        self.attempt = attempt
        self.spec = json.loads(attempt.files.engine.read_text())
        self.model = self.spec["model"]

    def health_path(self, engine):
        return engine.health_path

    def ready(self, session):
        """A direct engine refuses connections before it binds and answers 503 while loading."""
        deadline = time.monotonic() + self.attempt.remaining(1800)
        while True:
            try:
                return session.context.call(session.probe, self.health_path(session.engine), {},
                                            min(5, max(0.1, deadline - time.monotonic())), method="GET")
            except HTTPResponseError:
                pass
            except ProbeError:
                raise
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError("local engine did not become ready within the approved time")
            time.sleep(0.5)

    def prepare(self):
        """The approved binary and files are unchanged. Nothing is downloaded or installed."""
        attempt, run = self.attempt, self.attempt.run
        approved = run["local_engine"]
        executable = Path(approved["executable"]["path"])
        if not executable.is_file() or software_digest(executable) != approved["executable"]["software_sha256"]:
            raise Refusal("local engine software changed since approval; it needs a new study plan")
        for name, record in approved["files"].items():
            path = Path(record["path"])
            stat = path.stat() if path.is_file() else None
            if stat is None or (stat.st_size, stat.st_mtime_ns) != (record["bytes"], record["mtime_ns"]):
                raise Refusal(f"local engine file {name} changed since approval; it needs a new study plan")
        run["storage"] = {"files": {name: attempt.context.volume(record["path"]) for name, record in approved["files"].items()}}
        run["preparation"] = {"software_sha256": approved["executable"]["software_sha256"]}

    def probe(self, files, listen):
        attempt, run = self.attempt, self.attempt.run
        approved = run["local_engine"]
        paths = {"port": listen.rsplit(":", 1)[1], **{"file:" + name: record["path"] for name, record in approved["files"].items()}}
        argv = [approved["executable"]["path"], *(PLACEHOLDER.sub(lambda match: paths[match.group(1)], value)
                                                  for value in self.spec["arguments"])]
        environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": os.environ.get("HOME", "/"),
                       **({"TMPDIR": os.environ["TMPDIR"]} if "TMPDIR" in os.environ else {}),
                       **self.spec.get("environment", {})}
        intent = {"subject": "local", "executable": approved["executable"]["path"], "listen": listen}
        probe = attempt.context.local_probe(argv=argv, environment=environment, listen=listen, log_dir=files.process,
            watch_spec=watch_spec(run["resource_policy"], run["machine"], attempt.engine),
            log_bytes_max=records.LOG_BYTES, intent=intent)
        records.write(files.intent, intent)
        return probe

def for_attempt(attempt):
    return LocalSubject(attempt) if attempt.run.get("subject") == "local" else TemperSubject(attempt)
