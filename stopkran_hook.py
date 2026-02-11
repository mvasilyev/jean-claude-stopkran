#!/usr/bin/env python3
"""
Stopkran hook for Claude Code.

Lightweight script (stdlib only) that forwards permission requests
to the stopkran daemon via Unix socket and returns the decision.

Graceful degradation: if the daemon is unavailable, exits silently
so Claude Code falls back to its normal interactive UI.
"""

import json
import socket
import sys
import uuid

SOCKET_PATH = "/tmp/stopkran.sock"
# Must be less than the hook timeout in settings.json (330s)
RECV_TIMEOUT = 310


def main():
    # Read the hook event from stdin
    try:
        raw = sys.stdin.read()
        event = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    # Only handle PermissionRequest events
    if event.get("hook_event_name") != "PermissionRequest":
        sys.exit(0)

    request_id = str(uuid.uuid4())

    payload = json.dumps({
        "request_id": request_id,
        "session_id": event.get("session_id", ""),
        "tool_name": event.get("tool_name", ""),
        "tool_input": event.get("tool_input", {}),
        "cwd": event.get("cwd", ""),
        "permission_suggestions": event.get("permission_suggestions", []),
    })

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(RECV_TIMEOUT)
        sock.connect(SOCKET_PATH)

        sock.sendall((payload + "\n").encode("utf-8"))

        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break

        sock.close()

        response = json.loads(data.decode("utf-8").strip())
        decision = response.get("decision", "deny")
        updated_input = response.get("updatedInput")
        updated_permissions = response.get("updatedPermissions")

    except (
        FileNotFoundError,
        ConnectionRefusedError,
        socket.timeout,
        OSError,
        json.JSONDecodeError,
    ):
        # Graceful degradation — let Claude Code handle it normally
        sys.exit(0)

    # Output the decision in Claude Code hook format
    if decision == "allow":
        decision_obj = {"behavior": "allow"}
        if updated_input is not None:
            decision_obj["updatedInput"] = updated_input
        if updated_permissions is not None:
            decision_obj["updatedPermissions"] = updated_permissions
        result = {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": decision_obj,
            }
        }
    else:
        result = {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "deny"},
            }
        }

    print(json.dumps(result))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
