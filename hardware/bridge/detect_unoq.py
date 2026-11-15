"""Detect an Arduino UNO Q through ADB or serial ports and manage its SSH service."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def adb_candidates() -> list[Path]:
    candidates: list[Path] = []
    configured = os.environ.get("ADB_PATH")
    if configured:
        candidates.append(Path(configured))
    resolved = shutil.which("adb")
    if resolved:
        candidates.append(Path(resolved))
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    if local:
        candidates.extend(
            [
                local / "Microsoft" / "WinGet" / "Links" / "adb.exe",
                local / "Android" / "Sdk" / "platform-tools" / "adb.exe",
            ]
        )
        candidates.extend(
            (local / "Microsoft" / "WinGet" / "Packages").glob(
                "Google.PlatformTools*/platform-tools/adb.exe"
            )
        )
        candidates.extend(
            (local / "Arduino15" / "packages" / "arduino" / "tools" / "adb").glob(
                "*/adb.exe"
            )
        )
    return [candidate for candidate in candidates if candidate.is_file()]


def find_adb() -> Path | None:
    return next(iter(adb_candidates()), None)


def run_adb(adb: Path, *args: str, device: str | None = None) -> str:
    command = [str(adb)]
    if device:
        command.extend(["-s", device])
    command.extend(args)
    result = subprocess.run(command, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def adb_devices(adb: Path) -> list[dict[str, str]]:
    output = run_adb(adb, "devices", "-l")
    devices: list[dict[str, str]] = []
    for line in output.splitlines():
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        devices.append({"serial": fields[0], "state": fields[1], "details": " ".join(fields[2:])})
    return devices


def serial_ports() -> list[dict[str, str]]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [
        {"device": port.device, "description": port.description, "hwid": port.hwid}
        for port in list_ports.comports()
    ]


def ssh_status(adb: Path, device: str) -> dict[str, str]:
    return {
        "hostname": run_adb(adb, "shell", "hostname", device=device),
        "network_mode": run_adb(
            adb, "shell", "arduino-app-cli system network-mode status", device=device
        ),
        "ssh_enabled": run_adb(adb, "shell", "systemctl is-enabled ssh", device=device),
        "ssh_active": run_adb(adb, "shell", "systemctl is-active ssh", device=device),
        "ip_addresses": run_adb(adb, "shell", "hostname -I", device=device),
    }


def enable_ssh(adb: Path, device: str) -> dict[str, str]:
    """Enable UNO Q network mode and make OpenSSH persistent across reboots."""
    run_adb(adb, "shell", "arduino-app-cli system network-mode enable", device=device)
    run_adb(adb, "shell", "sudo systemctl enable --now ssh", device=device)
    return ssh_status(adb, device)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-ssh", action="store_true")
    parser.add_argument("--device", help="ADB serial; required only when several boards are connected")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    adb = find_adb()
    report: dict[str, Any] = {
        "platform": platform.platform(),
        "adb": str(adb) if adb else None,
        "adb_devices": adb_devices(adb) if adb else [],
        "serial_ports": serial_ports(),
    }
    ready = [item for item in report["adb_devices"] if item["state"] == "device"]
    if args.enable_ssh:
        if not adb:
            raise SystemExit("ADB was not found. Install Android SDK Platform-Tools first.")
        if args.device:
            target = args.device
        elif len(ready) == 1:
            target = ready[0]["serial"]
        else:
            raise SystemExit("Connect exactly one authorized UNO Q or pass --device <ADB_SERIAL>.")
        report["ssh"] = enable_ssh(adb, target)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"ADB: {report['adb'] or 'not found'}")
        print(f"ADB devices: {report['adb_devices'] or 'none'}")
        print(f"Serial ports: {report['serial_ports'] or 'pyserial not installed / none'}")
        if "ssh" in report:
            print(f"SSH: {report['ssh']}")


if __name__ == "__main__":
    main()
