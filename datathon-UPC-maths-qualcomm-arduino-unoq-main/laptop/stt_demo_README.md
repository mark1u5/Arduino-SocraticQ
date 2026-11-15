# STT Demo — `stt_demo.py`

> **Datathon FME 2026 · Qualcomm QRB2210 (Arduino UNO Q) track**
> Offline speech-to-text pipeline for spoken math problems.

---

## Why faster-whisper + tiny.en?

| Criterion | Decision |
|-----------|----------|
| **Offline** | Fully on-device — no cloud, no internet required during demo |
| **Model size** | `tiny.en` ≈ 75 MB (fp16) · **31 MB q5\_1 quantized** |
| **Accuracy** | English-only model; WER ~10 % on clean speech, fine for math vocabulary |
| **Speed on QRB2210** | ~0.3 × real-time on 4 Cortex-A78 cores (est.) |
| **Backend** | CTranslate2 (C++ kernel) → same INT8 optimized code runs on ARM64 |

### Why *not* `base.en` or larger?

The board already reserves ~1.1 GB for Qwen 0.8B + embeddings (of a 4 GB total).
`tiny.en` q5\_1 adds only **~50 MB RAM** in practice, leaving comfortable headroom.

---

## Model comparison

| Engine | Model | Disk (quantized) | RAM peak | WER (LibriSpeech) | ARM64 build |
|--------|-------|-----------------|----------|-------------------|-------------|
| **faster-whisper** | **tiny.en q5\_1** | **31 MB** | **~50 MB** | ~10 % | ✅ via CTranslate2 |
| faster-whisper | base.en q5\_1 | 57 MB | ~100 MB | ~7 % | ✅ |
| Vosk | vosk-model-small-en-us-0.15 | 40 MB | ~80 MB | ~14 % | ✅ |
| sherpa-onnx | silero-vad + sherpa-onnx-whisper-tiny | 44 MB | ~90 MB | ~11 % | ✅ |
| OpenAI Whisper (Python) | tiny.en | 75 MB | ~300 MB | ~10 % | ❌ (PyTorch dep) |

> [!TIP]
> `faster-whisper` is chosen over raw `openai-whisper` because CTranslate2 runs
> efficiently on ARM without PyTorch or CUDA, making it ideal for the QRB2210.

---

## Running on Windows (laptop demo)

### 1. Install

```bash
pip install faster-whisper sounddevice
```

### 2. Interactive mic mode

```bash
cd datathon-UPC-maths-qualcomm-arduino-unoq-main
python laptop/stt_demo.py
```

- Press **Enter** to start recording (up to 10 s).
- Speak your math problem ("What is the integral of x squared?").
- The script transcribes and shows what would be POSTed to Qwen 0.8B on `localhost:8081`.
- Press **q** then Enter to quit.

### 3. File mode (no mic needed)

```bash
python laptop/stt_demo.py --file my_recording.wav
```

Transcribes any 16 kHz mono WAV directly — great for CI/regression testing.

### 4. Larger model (better accuracy, more RAM)

```bash
python laptop/stt_demo.py --model base.en
```

---

## Deployment plan — Arduino UNO Q (QRB2210, ARM64 Linux)

> [!IMPORTANT]
> The board runs a **minimal ARM64 Linux** image. Steps below assume root access and ~200 MB free storage.

### Step 1 — Build whisper.cpp on the board

```bash
git clone https://github.com/ggerganov/whisper.cpp
cd whisper.cpp
cmake -B build -DWHISPER_NO_AVX=1   # ARM: disable x86 SIMD
cmake --build build --config Release -j4
```

### Step 2 — Download the quantized model

```bash
# On the board (or scp from laptop)
bash models/download-ggml-model.sh tiny.en
# Result: models/ggml-tiny.en.bin (~75 MB fp16)

# Optional: quantize to q5_1 (~31 MB, ~same accuracy)
./build/bin/quantize models/ggml-tiny.en.bin models/ggml-tiny.en.q5_1.bin q5_1
```

### Step 3 — Integrate with the eval pipeline

The board's `eval.py` calls `predict.py` per problem. Add a wrapper:

```python
# board/stt_bridge.py
import subprocess, json

def transcribe_mic(seconds=10, model="models/ggml-tiny.en.q5_1.bin"):
    """Record from mic and transcribe via whisper.cpp CLI."""
    result = subprocess.run(
        ["./whisper.cpp/build/bin/whisper-cli",
         "-m", model,
         "-t", "4",          # 4 threads
         "--no-timestamps",
         "-l", "en",
         "--mic",            # built-in mic capture
         "-d", str(seconds * 1000)],  # ms
        capture_output=True, text=True
    )
    return result.stdout.strip()
```

Then in `eval.py`:

```python
from board.stt_bridge import transcribe_mic

problem_text = transcribe_mic(seconds=10)
# → send to Qwen 0.8B on localhost:8081
```

### Step 4 — Memory budget on the board

| Component | RAM |
|-----------|-----|
| Qwen 0.8B (q4\_0) | ~700 MB |
| Embedding server | ~400 MB |
| **whisper.cpp tiny.en q5\_1** | **~50 MB** |
| OS + kernel | ~200 MB |
| **Total** | **~1.35 GB / 4 GB** ✅ |

> [!NOTE]
> Run `free -m` on the board to verify before loading all three services.
> If tight, swap to `ggml-tiny.en.bin` and load/unload whisper between turns.

---

## File overview

```
laptop/
├── stt_demo.py          ← this demo (Windows + ARM64 compatible)
├── stt_demo_README.md   ← this file
├── requirements.txt     ← pip deps (add faster-whisper sounddevice)
└── prepare_m3kang.py    ← existing dataset prep
```
