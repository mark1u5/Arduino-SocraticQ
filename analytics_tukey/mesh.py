"""SSH Mesh Communicator for SocraticQ Datathon Team.

Connects to 51.170.50.145 as datathon to read and post to:
  - /shared/datathon/all-devs.md
  - /shared/datathon/dev3-dev4.md
  - /shared/datathon/dev1-dev2.md
Uses flock and append-only logic per team protocol.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

try:
    import paramiko
except ImportError:  # pragma: no cover - optional SSH dependency
    paramiko = None

HOST = "51.170.50.145"
USER = "datathon"


def get_ssh_client():
    if paramiko is None:
        raise ImportError("paramiko is required for SSH mesh communication.")
    password = os.environ.get("DATATHON_SSH_PASS")
    if not password:
        raise RuntimeError("DATATHON_SSH_PASS environment variable is not set.")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=password, timeout=12)
    return ssh


def read_channel(channel_name: str) -> str:
    path = f"/shared/datathon/{channel_name}.md" if not channel_name.startswith("/") else channel_name
    ssh = get_ssh_client()
    try:
        cmd = f"cat {path} 2>/dev/null || echo '[EMPTY OR NOT FOUND]'"
        stdin, stdout, stderr = ssh.exec_command(cmd)
        return stdout.read().decode("utf-8", errors="replace")
    finally:
        ssh.close()


def post_message(channel_name: str, sender: str, message: str) -> None:
    path = f"/shared/datathon/{channel_name}.md" if not channel_name.startswith("/") else channel_name
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    formatted = f"\n[{now_str}] [{sender}]: {message}\n"
    
    # Escape double quotes for shell
    escaped_msg = formatted.replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    cmd = f'flock {path} -c "printf \\"{escaped_msg}\\" >> {path}"'

    ssh = get_ssh_client()
    try:
        stdin, stdout, stderr = ssh.exec_command(cmd)
        err = stderr.read().decode("utf-8", errors="replace")
        if err:
            print(f"[mesh error]: {err}", file=sys.stderr)
    finally:
        ssh.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "post":
        channel = sys.argv[2]
        sender = sys.argv[3]
        msg = " ".join(sys.argv[4:])
        post_message(channel, sender, msg)
        print(f"[OK] Posted to {channel}")
    else:
        for ch in ["all-devs", "dev3-dev4", "dev1-dev2"]:
            print(f"=== Channel: {ch} ===")
            print(read_channel(ch))
            print("-" * 50)
