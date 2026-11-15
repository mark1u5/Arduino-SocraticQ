"""SSH Markdown Channel Synchronization Client for Datathon AI Agents.

Manages coordination over the shared SSH channels at 51.170.50.145:
- /shared/datathon/all-devs.md (General team sync)
- /shared/datathon/dev3-dev4.md (Dev 3 & Dev 4 coordination)
- /shared/datathon/dev1-dev2.md (Dev 1 & Dev 2 coordination)

Features:
- Safe atomic append with `flock`
- Automatic timestamping [YYYY-MM-DD HH:MM] [dev3]
- Reading and tailing channel status
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from datetime import datetime, timezone
from typing import Optional

try:
    import paramiko
except ImportError:
    paramiko = None

SSH_HOST = os.getenv("DATATHON_SSH_HOST", "51.170.50.145")
SSH_USER = os.getenv("DATATHON_SSH_USER", "datathon")
SSH_PASS = os.getenv("DATATHON_SSH_PASS", "")
SHARED_DIR = "/shared/datathon"


def get_ssh_client() -> paramiko.SSHClient:
    if paramiko is None:
        raise ImportError("paramiko is required. Run 'pip install paramiko'.")
    if not SSH_PASS:
        raise RuntimeError("DATATHON_SSH_PASS environment variable must be set.")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(SSH_HOST, username=SSH_USER, password=SSH_PASS, timeout=10)
    return client


def _build_post_command(file_path: str, payload_b64: str) -> str:
    """Build the remote flock command that appends a base64-encoded payload.

    The payload travels as base64 (charset [A-Za-z0-9+/=]) so quotes, backticks,
    '$(', newlines, or a literal 'EOF' line inside a message can never be
    interpreted by the remote shell (no injection, no clobbering).
    """
    return (
        f'flock {file_path} -c "printf %s {payload_b64} | base64 -d >> {file_path}"'
    )


def post_message(channel: str, message: str, dev_id: str = "dev3") -> bool:
    """Append a message to a channel file using flock to prevent concurrency clobbering."""
    if not channel.endswith(".md"):
        channel = f"{channel}.md"

    file_path = f"{SHARED_DIR}/{channel}"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    header = f"[{now_str}] [{dev_id}]:"
    full_entry = f"\n{header} {message.strip()}\n"

    payload_b64 = base64.b64encode(full_entry.encode("utf-8")).decode("ascii")
    remote_cmd = _build_post_command(file_path, payload_b64)

    ssh = get_ssh_client()
    try:
        stdin, stdout, stderr = ssh.exec_command(remote_cmd)
        err = stderr.read().decode("utf-8").strip()
        if err:
            print(f"[Sync] Error posting to {channel}: {err}", file=sys.stderr)
            return False
        print(f"[Sync] Posted successfully to {channel}")
        return True
    finally:
        ssh.close()


def read_channel(channel: str) -> str:
    """Read full contents of a channel file."""
    if not channel.endswith(".md"):
        channel = f"{channel}.md"

    file_path = f"{SHARED_DIR}/{channel}"
    ssh = get_ssh_client()
    try:
        stdin, stdout, stderr = ssh.exec_command(f"cat {file_path}")
        return stdout.read().decode("utf-8")
    finally:
        ssh.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Datathon SSH Markdown Channel Sync")
    parser.add_argument("--channel", choices=["all-devs", "dev3-dev4", "dev1-dev2"], default="all-devs")
    parser.add_argument("--msg", type=str, help="Message content to append")
    parser.add_argument("--read", action="store_true", help="Read channel contents")
    parser.add_argument("--read-all", action="store_true", help="Read all channels")
    args = parser.parse_args()

    if args.read_all:
        for ch in ["all-devs", "dev3-dev4", "dev1-dev2"]:
            print(f"\n=================== {ch}.md ===================")
            print(read_channel(ch))
        return

    if args.read:
        print(read_channel(args.channel))
        return

    if args.msg:
        post_message(args.channel, args.msg)
    else:
        print("Please specify --msg <content> or --read.")


if __name__ == "__main__":
    main()
