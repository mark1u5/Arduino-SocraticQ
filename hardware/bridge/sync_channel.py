"""Sync channel client over SSH for Datathon coordination channels.

Interacts with /shared/datathon/*.md on the team coordination server.
Uses flock on append to avoid concurrent write collisions.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Optional

try:
    import paramiko
except ImportError:
    paramiko = None  # type: ignore

DEFAULT_HOST = os.environ.get("DATATHON_SSH_HOST", "51.170.50.145")
DEFAULT_USER = os.environ.get("DATATHON_SSH_USER", "datathon")
CHANNELS = {
    "all": "/shared/datathon/all-devs.md",
    "all-devs": "/shared/datathon/all-devs.md",
    "dev1-dev2": "/shared/datathon/dev1-dev2.md",
    "dev3-dev4": "/shared/datathon/dev3-dev4.md",
}


def get_ssh_client(
    host: str = DEFAULT_HOST,
    user: str = DEFAULT_USER,
    password: Optional[str] = None,
    timeout: float = 10.0,
) -> "paramiko.SSHClient":
    if paramiko is None:
        raise RuntimeError("paramiko is required: pip install paramiko")
    resolved_password = password if password is not None else os.environ.get("DATATHON_SSH_PASS")
    if not resolved_password:
        raise RuntimeError("DATATHON_SSH_PASS environment variable is required")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=resolved_password, timeout=timeout)
    return ssh


def read_channel(channel: str, host: str = DEFAULT_HOST, lines: Optional[int] = None) -> str:
    path = CHANNELS.get(channel, f"/shared/datathon/{channel}.md")
    ssh = get_ssh_client(host=host)
    try:
        cmd = f"tail -n {lines} {path}" if lines else f"cat {path}"
        stdin, stdout, stderr = ssh.exec_command(cmd)
        err = stderr.read().decode("utf-8")
        if err and not stdout.channel.recv_exit_status() == 0:
            raise RuntimeError(f"Error reading {path}: {err}")
        return stdout.read().decode("utf-8")
    finally:
        ssh.close()


def post_channel(
    channel: str,
    message: str,
    author: str = "dev1",
    host: str = DEFAULT_HOST,
) -> None:
    path = CHANNELS.get(channel, f"/shared/datathon/{channel}.md")
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    formatted = f"\n[{now_utc}] [{author}]: {message}\n"
    # Safely escape single quotes for bash
    escaped = formatted.replace("'", "'\\''")
    cmd = f"flock {path} -c 'printf \"%s\" \"{escaped}\" >> {path}'"
    ssh = get_ssh_client(host=host)
    try:
        stdin, stdout, stderr = ssh.exec_command(cmd)
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            err = stderr.read().decode("utf-8")
            raise RuntimeError(f"Error posting to {path} (exit {exit_status}): {err}")
    finally:
        ssh.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--channel",
        choices=["all", "all-devs", "dev1-dev2", "dev3-dev4"],
        default="all-devs",
        help="Channel name (default: all-devs)",
    )
    parser.add_argument("--read", action="store_true", help="Read channel contents")
    parser.add_argument("--tail", type=int, default=None, help="Read last N lines")
    parser.add_argument("--post", type=str, default=None, help="Post a message to channel")
    parser.add_argument("--author", default="dev1", help="Author tag (default: dev1)")
    args = parser.parse_args()

    if args.post:
        post_channel(args.channel, args.post, author=args.author)
        print(f"Posted to {args.channel} as [{args.author}]: {args.post}")
    elif args.read or args.tail:
        content = read_channel(args.channel, lines=args.tail)
        print(content)
    else:
        # Default read tail 20
        content = read_channel(args.channel, lines=20)
        print(content)


if __name__ == "__main__":
    main()
