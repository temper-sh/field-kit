"""Apply one scoped coding proposal; no evaluation or model policy lives here."""
from __future__ import annotations

import difflib
import json
from pathlib import Path, PurePosixPath
import shutil


TOOLS = [{"type": "function", "function": {
    "name": "submit_patch",
    "description": "Submit focused source edits and regression tests for independent evaluation.",
    "parameters": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "explanation": {"type": "string"},
            "edits": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"}},
                "required": ["path", "old_text", "new_text"]}},
            "new_files": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"]}}},
        "required": ["explanation", "edits", "new_files"]}}}]


class InvalidPatch(ValueError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidPatch("Duplicate JSON field: " + key)
        result[key] = value
    return result


def submission(response):
    calls = response.get("message", {}).get("tool_calls") or []
    if len(calls) != 1:
        raise InvalidPatch("Submit exactly one submit_patch tool call.")
    function = calls[0].get("function", {})
    if function.get("name") != "submit_patch":
        raise InvalidPatch("The tool must be submit_patch.")
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments, object_pairs_hook=unique_object)
        except ValueError as error:
            raise InvalidPatch("Tool arguments are not valid unambiguous JSON: " + str(error)) from error
    if not isinstance(arguments, dict) or set(arguments) != {"explanation", "edits", "new_files"}:
        raise InvalidPatch("Expected explanation, edits, and new_files fields.")
    if not isinstance(arguments["explanation"], str):
        raise InvalidPatch("explanation must be text.")
    edits, new = arguments["edits"], arguments["new_files"]
    if not isinstance(edits, list) or not isinstance(new, list) or not edits and not new:
        raise InvalidPatch("Submit at least one edit or new file.")
    if len(edits) > 64 or len(new) > 16:
        raise InvalidPatch("The proposal exceeds the bounded task edit set.")
    return arguments


def safe_path(value, source):
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise InvalidPatch("Use a relative POSIX file path.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise InvalidPatch("Path is not a clean relative file path: " + value)
    current = source
    for part in path.parts:
        current /= part
        if current.is_symlink():
            raise InvalidPatch("Editing through symlinks is not allowed: " + value)
    if any(part.startswith(".") for part in path.parts):
        raise InvalidPatch("Hidden paths are outside this task: " + value)
    if path.name in {"conftest.py", "pytest.ini", "pyproject.toml", "setup.py", "setup.cfg", "tox.ini"}:
        raise InvalidPatch("Test/build configuration is outside this task: " + value)
    return value


def in_new_scope(path, scope):
    return any(path.startswith(prefix) for prefix in scope["new_file_prefixes"])


def staged_texts(arguments, source, scope):
    """Validate the whole proposal and compute its resulting file contents."""
    source = Path(source).resolve()
    texts, originals = {}, {}
    for edit in arguments["edits"]:
        if not isinstance(edit, dict) or set(edit) != {"path", "old_text", "new_text"}:
            raise InvalidPatch("Every edit requires path, old_text, and new_text.")
        name = safe_path(edit["path"], source)
        if name not in scope["existing_files"] and not in_new_scope(name, scope):
            raise InvalidPatch("Edit is outside the task scope: " + name)
        path = source / name
        if not path.is_file():
            raise InvalidPatch("Use new_files to create a file: " + name)
        old, new = edit["old_text"], edit["new_text"]
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise InvalidPatch("old_text must be nonempty text and new_text must be text: " + name)
        if name not in texts:
            originals[name] = texts[name] = path.read_text()
        count = texts[name].count(old)
        if count != 1:
            raise InvalidPatch(f"old_text occurs {count} times in {name}; require exactly one.")
        texts[name] = texts[name].replace(old, new, 1)
    for new in arguments["new_files"]:
        if not isinstance(new, dict) or set(new) != {"path", "content"}:
            raise InvalidPatch("Every new file requires path and content.")
        name = safe_path(new["path"], source)
        if not in_new_scope(name, scope):
            raise InvalidPatch("New file is outside the task scope: " + name)
        if (source / name).exists() or name in texts:
            raise InvalidPatch("New file already exists or was proposed twice: " + name)
        if not isinstance(new["content"], str):
            raise InvalidPatch("New file content must be text: " + name)
        originals[name], texts[name] = "", new["content"]
    if any(len(text.encode()) > 512 * 1024 for text in texts.values()):
        raise InvalidPatch("An edited file exceeds the bounded task size.")
    changed = {name: value for name, value in texts.items() if value != originals[name]}
    if not changed:
        raise InvalidPatch("The proposal makes no file change.")
    return changed, originals


def copy_source(source, target):
    shutil.copytree(source, target, symlinks=True, ignore=shutil.ignore_patterns(
        ".git", ".pytest_cache", "__pycache__", ".mypy_cache", ".venv", "*.pyc"))


def apply(arguments, source, target, scope):
    source, target = Path(source).resolve(), Path(target)
    texts, originals = staged_texts(arguments, source, scope)
    if target.exists():
        raise FileExistsError("Candidate destination already exists: " + str(target))
    staging = target.with_name(target.name + ".staging")
    if staging.exists():
        raise FileExistsError("Candidate staging destination already exists: " + str(staging))
    copy_source(source, staging)
    for name, value in texts.items():
        path = staging / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    staging.rename(target)
    diff = "".join("".join(difflib.unified_diff(
        originals[name].splitlines(keepends=True), value.splitlines(keepends=True),
        fromfile="a/" + name, tofile="b/" + name)) for name, value in sorted(texts.items()))
    return {"changed_files": sorted(texts), "diff": diff}
