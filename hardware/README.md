# Dev 1 — Arduino UNO Q hardware

The UNO Q is a dual-processor board: the STM32U585 MCU reads the controls and
drives the LEDs, while the QRB2210 runs Debian and the Python bridge.  The
processors communicate through **Arduino Bridge/RPC**.  Do not use `Serial1`
as the internal MCU-to-Linux connection; it is for external UART hardware.

## First, verify the board and SSH

On Windows, with the UNO Q connected by USB-C:

```powershell
python -m hardware.bridge.detect_unoq --json
python -m hardware.bridge.detect_unoq --enable-ssh --json
```

`--enable-ssh` enables Arduino App Lab network mode and makes the board's
OpenSSH service persistent across reboots.  It does **not** configure Wi-Fi:
complete first setup in Arduino App Lab or connect the board to Wi-Fi in SBC
mode.  Once both devices are on the same LAN, connect with:

```powershell
ssh arduino@<board-name>.local
```

For this board the current hostname is normally shown by the detector.  The
default shown during setup is often `uno-q`, so the likely command is
`ssh arduino@uno-q.local`.

## Laptop-only smoke test

The mock emits one MCU event; the daemon prints `THINKING` followed by a valid
feedback frame.  No Arduino, engine, or RawTree endpoint is required.

```powershell
python -m unittest hardware.tests.test_bridge
python -m hardware.bridge.mock_mcu --once hint |
  python -m hardware.bridge.serial_daemon --stdin --engine mock --no-ingest
```

For a physical external UART device install the only Python dependency:

```powershell
python -m pip install -r hardware/requirements.txt
python -m hardware.bridge.serial_daemon --port COM5 --engine mock --no-ingest
```

## Arduino App Lab deployment

Create an App Lab application with both an MCU sketch and Python component.
Copy these files into it:

- `firmware/socraticq_mcu.ino` → the app's `sketch/sketch.ino`.
- `bridge/unoq_app.py` → the app's `python/main.py`.
- `bridge/serial_daemon.py` → the same Python folder.

The sketch publishes `socraticq_event` and receives `socraticq_feedback` over
Arduino Bridge.  The Python app currently uses the deterministic mock engine;
change only the engine selection when Dev 2 exposes the frozen
`generate_socratic_hint` contract.

## Wiring before flashing

The sketch intentionally uses configurable example pin values.  Verify the
actual breadboard wiring and the UNO Q pinout before connecting components:

- category potentiometer: `A0`;
- confidence potentiometer: `A1`;
- hint button: `D2`, wired to GND with `INPUT_PULLUP`;
- submit button: `D3`, wired to GND with `INPUT_PULLUP`;
- external RGB LED: `D9`, `D10`, `D11` through resistors;
- buzzer: `D6`.

Change only the `SOCRATICQ_*_PIN` definitions in the sketch if the final
wiring differs.  Confirm the RGB LED polarity before powering it.

## Checks before each commit

```powershell
python -m contracts.check_contracts
python -m unittest hardware.tests.test_bridge
git status
```

Only files under `hardware/` belong in a Dev 1 commit.
