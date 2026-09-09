from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    return Path(os.environ.get("ENTIRE_REPO_ROOT") or os.getcwd()).resolve()


def marker_path(agent: str) -> Path:
    return repo_root() / ".entire" / f"{agent}-adapter-hooks-installed.json"


def session_sidecar(session_ref: str) -> Path:
    return Path(session_ref).with_suffix(Path(session_ref).suffix + ".session.json")


def write_json(payload: Any) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")))
    sys.stdout.write("\n")


def read_json() -> dict[str, Any]:
    data = sys.stdin.buffer.read()
    if not data.strip():
        return {}
    return json.loads(data.decode("utf-8"))


def decode_native(value: Any) -> bytes:
    if not isinstance(value, str):
        raise ValueError("native_data must be a base64 string or null")
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise ValueError("native_data must be valid base64") from exc


def handle_install(agent: str) -> int:
    path = marker_path(agent)
    already = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "agent": agent,
                "kind": "callback-bridge-marker",
                "description": "Entire Adapter invokes lifecycle hooks from Python callbacks/listeners.",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_json({"hooks_installed": 0 if already else 1})
    return 0


def handle_uninstall(agent: str) -> int:
    try:
        marker_path(agent).unlink()
    except FileNotFoundError:
        pass
    return 0


def handle_are_installed(agent: str) -> int:
    write_json({"installed": marker_path(agent).exists()})
    return 0


def write_private(path: Path, data: bytes) -> None:
    # Replace instead of truncating: the complete new contents remain private
    # even when the destination previously had permissive file permissions.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def handle_write_session() -> int:
    payload = read_json()
    session_ref = payload.get("session_ref")
    if not session_ref:
        return 0

    # A metadata-only session (including the adapter's fresh read response)
    # has no transcript to restore. An explicit empty value still restores
    # an empty transcript.
    if payload.get("native_data") is None:
        return 0

    try:
        data = decode_native(payload["native_data"])
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    path = Path(session_ref)
    write_private(path, data)

    metadata = dict(payload)
    metadata.pop("native_data", None)
    sidecar = session_sidecar(session_ref)
    write_private(sidecar, json.dumps(metadata, separators=(",", ":")).encode("utf-8"))
    return 0


def handle_read_session(agent: str, underlying: Path) -> int:
    payload = read_json()
    session_ref = payload.get("session_ref")
    if session_ref:
        sidecar = session_sidecar(session_ref)
        if sidecar.exists():
            metadata = json.loads(sidecar.read_text(encoding="utf-8"))
            # The adapter appends to the transcript without updating our
            # sidecar. Only the live file is authoritative for native data.
            try:
                data = Path(session_ref).read_bytes()
            except FileNotFoundError:
                metadata["native_data"] = None
            else:
                metadata["native_data"] = base64.b64encode(data).decode("ascii")
            metadata.setdefault("agent_name", agent)
            metadata.setdefault("modified_files", [])
            metadata.setdefault("new_files", [])
            metadata.setdefault("deleted_files", [])
            write_json(metadata)
            return 0

    return forward(underlying, ["read-session"], json.dumps(payload).encode("utf-8"))


def forward(underlying: Path, args: list[str], stdin: bytes | None = None) -> int:
    completed = subprocess.run(
        [str(underlying), *args],
        input=stdin,
        stdout=sys.stdout.buffer,
        stderr=sys.stderr.buffer,
        check=False,
    )
    return completed.returncode


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: protocol_wrapper.py <agent> <subcommand> [args]", file=sys.stderr)
        return 1

    agent = sys.argv[1]
    args = sys.argv[2:]
    if not args:
        print(f"usage: entire-agent-{agent} <subcommand> [args]", file=sys.stderr)
        return 1

    root = Path(__file__).resolve().parent
    underlying = root / ".venv" / "bin" / f"entire-agent-{agent}"
    command = args[0]

    if command == "install-hooks":
        return handle_install(agent)
    if command == "uninstall-hooks":
        return handle_uninstall(agent)
    if command == "are-hooks-installed":
        return handle_are_installed(agent)
    if command == "write-session":
        return handle_write_session()
    if command == "read-session":
        return handle_read_session(agent, underlying)

    return forward(underlying, args)


if __name__ == "__main__":
    raise SystemExit(main())
