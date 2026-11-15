#!/usr/bin/env python3
"""
stt_demo.py — Speech-to-Text demo for Datathon FME 2026
Runs on Windows laptop; deploy to Arduino UNO Q (Qualcomm QRB2210).

Engine  : faster-whisper (CTranslate2 backend)
Model   : tiny.en  (~75 MB, q5_1 ≈31 MB)
Mic I/O : sounddevice
"""

import argparse
import sys
import os
import math
import tempfile
import threading
import wave
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Lazy imports so --help works even if deps are missing
# ---------------------------------------------------------------------------

BANNER = r"""
╔══════════════════════════════════════════════════════════════════════════╗
║   🎙  Datathon FME 2026 — Math STT Demo  (Qualcomm QRB2210 / UNO Q)   ║
║                                                                          ║
║   Engine  : whisper-tiny.en  via faster-whisper (CTranslate2)           ║
║   Backend : runs fully OFFLINE — no cloud, no latency                   ║
║   Purpose : transcribe spoken math problems → Qwen 0.8B on port 8081    ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

SAMPLE_RATE   = 16_000   # Hz — Whisper native rate
MAX_SECONDS   = 10       # maximum recording window
SILENCE_THRESHOLD = 0.01 # RMS below this = silence
SILENCE_GRACE  = 1.5     # seconds of silence before auto-stop


def rms(data):
    """Root-mean-square of a numpy array."""
    import numpy as np
    return math.sqrt(np.mean(data.astype("float32") ** 2))


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def load_model(model_name: str = "tiny.en"):
    """Load faster-whisper model (auto-downloaded on first run)."""
    from faster_whisper import WhisperModel
    print(f"[STT] Loading model '{model_name}' (downloads on first run ~75 MB)…")
    t0 = time.time()
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    print(f"[STT] Model ready in {time.time()-t0:.1f}s")
    return model


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def transcribe_file(model, wav_path: str, beam_size: int = 5):
    """Transcribe a WAV file; return (text, avg_confidence)."""
    segments, info = model.transcribe(wav_path, beam_size=beam_size, language="en")
    text_parts = []
    logprobs   = []
    for seg in segments:
        text_parts.append(seg.text.strip())
        logprobs.append(seg.avg_logprob)

    full_text   = " ".join(text_parts).strip()
    avg_logprob = sum(logprobs) / len(logprobs) if logprobs else -999
    # convert log-prob to a 0-100 confidence %
    confidence  = max(0.0, min(100.0, math.exp(avg_logprob) * 100))
    return full_text, confidence


# ---------------------------------------------------------------------------
# Microphone recording
# ---------------------------------------------------------------------------

def record_mic(max_seconds: int = MAX_SECONDS) -> str:
    """
    Record from default mic until:
      • MAX_SECONDS elapsed, OR
      • SILENCE_GRACE seconds of continuous silence (after at least 1 s of speech)

    Returns path to a temporary WAV file.
    """
    import sounddevice as sd
    import numpy as np

    print(f"[STT] 🔴 Recording (max {max_seconds}s) — speak now…", flush=True)

    frames        = []
    silent_chunks = 0
    spoke         = False
    chunk_size    = int(SAMPLE_RATE * 0.1)   # 100 ms chunks
    max_chunks    = int(max_seconds / 0.1)
    grace_chunks  = int(SILENCE_GRACE / 0.1)
    stop_event    = threading.Event()

    def _callback(indata, frame_count, time_info, status):
        nonlocal silent_chunks, spoke
        chunk = indata[:, 0].copy()
        frames.append(chunk)
        level = rms(chunk)
        if level > SILENCE_THRESHOLD:
            spoke         = True
            silent_chunks = 0
        else:
            if spoke:
                silent_chunks += 1
        if silent_chunks >= grace_chunks and spoke:
            stop_event.set()

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                        dtype="int16", blocksize=chunk_size,
                        callback=_callback):
        t0 = time.time()
        while not stop_event.is_set() and len(frames) < max_chunks:
            elapsed = time.time() - t0
            remaining = max_seconds - elapsed
            if remaining <= 0:
                break
            # animate a progress dot
            print(f"\r   {elapsed:5.1f}s / {max_seconds}s  {'▓' * int(elapsed)}{'░' * (max_seconds - int(elapsed))}",
                  end="", flush=True)
            time.sleep(0.1)

    print("\n[STT] ⏹  Recording stopped.", flush=True)

    import numpy as np
    audio = np.concatenate(frames, axis=0).astype("int16")

    # save to temp WAV
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(tmp.name, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)   # int16 → 2 bytes
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())
    return tmp.name


# ---------------------------------------------------------------------------
# Interactive loop
# ---------------------------------------------------------------------------

def interactive_loop(model):
    print(BANNER)
    print("  Press Enter to start recording, 'q' + Enter to quit.\n")

    while True:
        try:
            key = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n[STT] Bye!")
            break

        if key == "q":
            print("[STT] Exiting.")
            break

        # Record
        wav_path = record_mic()

        # Transcribe
        print("[STT] Transcribing…", flush=True)
        try:
            text, confidence = transcribe_file(model, wav_path)
        finally:
            try:
                os.remove(wav_path)
            except OSError:
                pass

        # Results
        print()
        print(f"  ┌─ Transcription ({'%.1f' % confidence}% confidence) ─────────────────────")
        print(f"  │  {text if text else '(nothing detected)'}")
        print(f"  └──────────────────────────────────────────────────────────────────")

        if text:
            print()
            print("  ┌─ Would send to Qwen 0.8B (http://localhost:8081) ─────────────")
            print(f"  │  POST /v1/chat/completions")
            print(f"  │  {{\"model\": \"qwen-0.8b\", \"messages\": [")
            print(f"  │      {{\"role\": \"system\", \"content\": \"You are a math tutor.\"}},")
            print(f"  │      {{\"role\": \"user\",   \"content\": \"{text}\"}}]")
            print(f"  │  }}")
            print(f"  └──────────────────────────────────────────────────────────────")

        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="stt_demo",
        description=(
            "Datathon FME 2026 — offline Speech-to-Text demo.\n"
            "Uses faster-whisper (tiny.en) on CPU. Optionally transcribes a WAV file."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python stt_demo.py                   # interactive mic mode\n"
            "  python stt_demo.py --file audio.wav  # transcribe WAV file\n"
            "  python stt_demo.py --model base.en   # use a larger model\n"
        ),
    )
    parser.add_argument(
        "--file", "-f",
        metavar="WAV",
        help="Transcribe this WAV file instead of using the microphone.",
    )
    parser.add_argument(
        "--model", "-m",
        default="tiny.en",
        metavar="MODEL",
        help="faster-whisper model name (default: tiny.en). "
             "Options: tiny.en, base.en, small.en, medium.en",
    )
    parser.add_argument(
        "--beam-size", "-b",
        type=int,
        default=5,
        metavar="N",
        help="Beam size for decoding (default: 5).",
    )
    return parser


def main():
    parser = build_parser()
    args   = parser.parse_args()

    model = load_model(args.model)

    if args.file:
        wav = str(Path(args.file).resolve())
        if not os.path.isfile(wav):
            print(f"[STT] ERROR: file not found: {wav}", file=sys.stderr)
            sys.exit(1)
        print(f"[STT] Transcribing file: {wav}")
        text, confidence = transcribe_file(model, wav, beam_size=args.beam_size)
        print(f"\n  Transcription ({confidence:.1f}% confidence):\n  {text}\n")
    else:
        interactive_loop(model)


if __name__ == "__main__":
    main()
