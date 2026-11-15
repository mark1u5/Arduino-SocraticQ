# SocraticQ: Offline Edge Socratic Math Mentor

> **Track:** Main Track — Qualcomm & Arduino Uno Q (€600)  
> **Team:** UPC Datathon 2026 (FME)  
> **Platform:** Arduino Uno Q (Qualcomm QRB2210 SoC running Debian Linux ARM64 + Physical Arduino MCU)

---

## 1. Project Overview

In Catalonia, tens of thousands of high-school students participate in **Proves Cangur** mathematics competitions annually. When practicing, students hit conceptual roadblocks—most commonly in Combinatorics (parity invariants) and Geometry (area ratios). Standard LLM chatbots ruin learning by spoiling the final answer option ($A$ through $E$). Moreover, classroom testing environments strictly ban internet access to prevent cheating.

**SocraticQ** is an offline, edge-native Socratic tutor. It runs a quantized small language model (`Qwen3.5-0.8B` on `llama-server` `:8081` + `bge-small` embedding model on `:8082`) locally on the **Qualcomm QRB2210 Debian Linux** environment, interfaced with the built-in 8×13 LED Matrix and physical dials and buttons via the onboard STM32U585 microcontroller. It provides pedagogical nudges, enforces zero-answer leakage, and captures physical self-assessed confidence metrics without requiring a single byte of cloud connectivity.

---

## 2. Hardware Bill of Materials & Wiring Diagram

The firmware (`hardware/firmware/socraticq_mcu.ino`) runs in two modes:
1. **Default Mode (`SOCRATICQ_EXTERNAL_IO 0`):** Operates standalone using the Uno Q's built-in **8×13 LED Matrix** (104 individually addressable pixels) with RouterBridge RPC (`Bridge.call("draw", ...)`), requiring no external wiring.
2. **Interactive Rig Mode (`SOCRATICQ_EXTERNAL_IO 1`):** Enables external potentiometers, momentary pushbuttons, RGB LED, and piezo buzzer.

| Component | Interface | Pin / Port | Function |
| :--- | :--- | :--- | :--- |
| **Arduino Uno Q** | Dual Core | USB-C Power (5V/3A) | Main SBC + Real-time MCU |
| **Rotary Potentiometer 1** | Analog Input | `A0` (MCU ADC) | **Category Dial:** Problem domain selector (Logic, Combinatorics, Geometry, NumberTheory, Algebra) |
| **Rotary Potentiometer 2** | Analog Input | `A1` (MCU ADC) | **Confidence Dial:** Student certainty self-assessment ($0..100\%$) |
| **Momentary Pushbutton 1** | Digital Input | `D2` (Internal Pullup) | **Hint Request Button:** Emits Socratic ladder rung ($1..3$) |
| **Momentary Pushbutton 2** | Digital Input | `D3` (Internal Pullup) | **Answer Submit Button:** Verifies attempt and logs telemetry |
| **Common-Cathode RGB LED** | Digital PWM | `D9` (R), `D10` (G), `D11` (B) | Visual state indicator (Thinking, Nudge, Solved, Alert) |
| **Piezo Buzzer** | Digital Output | `D6` | Tactile audio feedback patterns |
| **Internal RouterBridge** | IPC Serial | `/dev/ttyHS1` / Bridge RPC | High-speed JSON bridge between MCU & Linux Daemon |

### LED State Encoding
* **IDLE (Solid Blue `[0, 100, 255]`):** System ready for user interaction.
* **THINKING (Pulsing Cyan `[0, 255, 255]`):** Local SLM inference active on QRB2210.
* **SOCRATIC_NUDGE (Amber `[255, 180, 0]`):** Pedagogical hint emitted to student.
* **SOLVED (Green `[0, 255, 0]`):** Correct solution verified.
* **BOTTLENECK_ALERT (Purple `[168, 85, 247]`):** Proactive intervention received from RawTree Agent loop.

---

## 3. Software Architecture & Edge Pipeline

```text
[ Physical Controls (Dials & Buttons) ]
                 │
                 ▼
[ Arduino MCU Firmware (socraticq_mcu.ino) ]
                 │
                 ▼ UART Newline JSON (/dev/ttyHS1 @ 115200 baud)
[ Linux Hardware Daemon (serial_daemon.py) ]
        │                                 │
        ▼ IPC                             ▼ Spool
[ Local Socratic Engine ]         [ Local SQLite Buffer ]
 - Proves Cangur Corpus 2007-2024   - Queues telemetry offline
 - Quantized SLM (llama.cpp)       - Flushes JSON-in when online
 - Zero-Leakage Guardrails
```

### Quantization & SLM Optimization on Qualcomm QRB2210
* **Reasoning Model:** `Qwen3.5-0.8B` running on local `llama-server` at `127.0.0.1:8081` (`bge-small` embedding model on `:8082`).
* **Inference Engine:** `llama.cpp` compiled on ARM64 Debian with NEON vector extensions.
* **Inference Latency:** $\sim 650\text{ms} - 950\text{ms}$ per Socratic response ($\le 80$ tokens).
* **RAM Footprint:** $< 900\text{ MB}$, well within the Uno Q physical memory limit.

---

## 4. Zero-Answer-Leakage Pedagogical Taxonomy

SocraticQ strictly refuses to output the correct multiple-choice letter ($A$ through $E$). Hints are taxonomized into distinct pedagogical moves:
1. `DECOMPOSE_PROBLEM`: Breaks multi-step logic into smaller questions.
2. `COUNTEREXAMPLE_CHECK`: Asks student to test extreme cases ($n=0, 1$).
3. `INVARIANT_NUDGE`: Prompts search for conserved quantities.
4. `PARITY_CHECK`: Suggests odd/even or chessboard tile coloring.
5. `BOUNDARY_TEST`: Directs attention to corner or edge constraints.

---

## 5. Offline Verification Instructions for Jury

1. Disconnect all Wi-Fi, Ethernet, and Bluetooth connections from the Uno Q.
2. Power the Uno Q via a 5V USB-C battery pack.
3. Turn the Category Dial (`A0`) to `Combinatorics` (`CAM-2023-N4-17`).
4. Set the Confidence Dial (`A1`) low ($30\%$).
5. Press the **Hint Request** button (`D2`):
   - Notice the RGB LED pulses Cyan (`THINKING`) and flips to Amber (`SOCRATIC_NUDGE`) in $< 1.0\text{s}$.
   - The display / terminal confirms the prompt: *"Can two adjacent steps land on cells with identical parity?"*
6. Notice that zero internet packets are transmitted; all computation remains on edge silicon.
