"""Offline Whisper.cpp speech-to-text module for SocraticQ (Arduino UNO Q / local)."""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

WHISPER_BIN_DEFAULT = os.environ.get(
    "WHISPER_CLI_PATH", "/home/arduino/whisper.cpp/build/bin/whisper-cli"
)
MODEL_DEFAULT = os.environ.get(
    "WHISPER_MODEL_PATH", "/home/arduino/models/ggml-tiny-q5_1.bin"
)

PROMPTS: dict[str, str] = {
    "ca": (
        "Problema matemàtic de les proves Cangur en català: "
        "triangle, triangles, rectangle, quadrat, cercle, cub, prisma, polígon, angles, vèrtexs, costats, "
        "perímetre, àrea, fracció, proporció, percentatge, nombre, nombres, xifra, xifres, suma, resta, "
        "multiplicació, divisió, igual, quan, quant, quants, quantes, els, als, del, dels, pel, pels."
    ),
    "es": (
        "Problema matemático de la competición Canguro en español: "
        "triángulo, rectángulo, cuadrado, círculo, polígono, ángulos, vértices, lados, perímetro, "
        "área, fracción, porcentaje, número, cifras, suma, resta, multiplicación, división, cuánto, cuántos."
    ),
    "en": (
        "Math competition Kangaroo problem: triangle, rectangle, square, circle, polygon, angles, "
        "vertices, perimeter, area, fraction, ratio, percentage, number, digits, sum, difference, product."
    ),
}

CATALAN_CORRECTIONS: list[tuple[str, str]] = [
    (r"\btriangla\b", "triangle"),
    (r"\btriangles\b", "triangles"),
    (r"\bquàn\b", "quan"),
    (r"\bquan\s+(val|és|són|fa|mesura|tenen|costa|hi ha)\b", r"quant \1"),
    (r"\bquant\s+(arribi|vingui|acabi|comenci|plogui)\b", r"quan \1"),
    (r"\bquadrat\b", "quadrat"),
    (r"\bperimetre\b", "perímetre"),
    (r"\barea\b", "àrea"),
    (r"\bpoligon\b", "polígon"),
    (r"\bfraccio\b", "fracció"),
    (r"\bfraccions\b", "fraccions"),
    (r"\bangles\b", "angles"),
    (r"\bvertexs\b", "vèrtexs"),
    (r"\bmultiplicacio\b", "multiplicació"),
    (r"\bdivisio\b", "divisió"),
    (r"\bporció\b", "proporció"),
]


def normalize_whisper_language(language: str | None) -> str:
    """Map UI/API language codes ('cat', 'eng', 'ca', 'en', 'auto') to whisper-cli -l codes."""
    raw = (language or "").strip().lower()
    if raw in ("cat", "ca", "ca-es", "catalan", "català"):
        return "ca"
    if raw in ("eng", "en", "en-us", "en-gb", "english"):
        return "en"
    if raw in ("es", "es-es", "spa", "spanish"):
        return "es"
    return "auto"


def clean_catalan_orthography(text: str) -> str:
    """Apply domain-specific orthographic fixes for Catalan math terms."""
    cleaned = text
    for pattern, replacement in CATALAN_CORRECTIONS:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    return cleaned


def is_whisper_available(
    whisper_bin: str = WHISPER_BIN_DEFAULT,
    model: str = MODEL_DEFAULT,
) -> bool:
    """Return True when both whisper-cli binary and quantized ggml model exist on disk."""
    return Path(whisper_bin).is_file() and Path(model).is_file()


def _resample_pcm16_mono(samples: list[int], src_rate: int, dst_rate: int = 16000) -> list[int]:
    if src_rate == dst_rate or not samples:
        return samples
    ratio = src_rate / dst_rate
    out_len = max(1, int(len(samples) / ratio))
    out: list[int] = []
    n = len(samples)
    for i in range(out_len):
        pos = i * ratio
        idx = int(pos)
        frac = pos - idx
        s0 = samples[min(idx, n - 1)]
        s1 = samples[min(idx + 1, n - 1)]
        val = int(s0 + (s1 - s0) * frac)
        out.append(max(-32768, min(32767, val)))
    return out


def _convert_wav_pure_python(input_path: str, out_wav_path: str) -> str:
    """Ensure a WAV file is 16kHz mono 16-bit PCM using stdlib wave + struct only."""
    with wave.open(input_path, "rb") as wf:
        nch = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        rate = wf.getframerate()
        nframes = wf.getnframes()
        if nch == 1 and sampwidth == 2 and rate == 16000:
            return input_path
        raw = wf.readframes(nframes)

    if sampwidth == 2:
        total_samples = len(raw) // 2
        vals = list(struct.unpack(f"<{total_samples}h", raw[: total_samples * 2]))
    elif sampwidth == 1:
        vals = [(b - 128) * 256 for b in raw]
    else:
        raise RuntimeError(f"Unsupported WAV sample width: {sampwidth} bytes (need ffmpeg)")

    if nch > 1:
        mono: list[int] = []
        for i in range(0, len(vals) - nch + 1, nch):
            mono.append(sum(vals[i : i + nch]) // nch)
        vals = mono

    vals = _resample_pcm16_mono(vals, rate, 16000)
    pcm_bytes = struct.pack(f"<{len(vals)}h", *vals)
    with wave.open(out_wav_path, "wb") as out_wf:
        out_wf.setnchannels(1)
        out_wf.setsampwidth(2)
        out_wf.setframerate(16000)
        out_wf.writeframes(pcm_bytes)
    return out_wav_path


def convert_to_16k_mono_wav(input_path: str, out_wav_path: str) -> str:
    """Convert audio (WebM/Opus/OGG/MP3/WAV) to 16kHz mono 16-bit WAV when needed."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                input_path,
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                out_wav_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0 and Path(out_wav_path).is_file():
            return out_wav_path
        raise RuntimeError(f"ffmpeg conversion failed: {proc.stderr.strip()}")

    with open(input_path, "rb") as f:
        header = f.read(12)
    is_wav = len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE"
    if is_wav:
        return _convert_wav_pure_python(input_path, out_wav_path)

    ext = Path(input_path).suffix.lower()
    if ext in (".ogg", ".mp3", ".flac"):
        return input_path

    raise RuntimeError(
        f"Cannot decode '{ext or 'unknown'}' audio without ffmpeg; send 16kHz WAV or install ffmpeg."
    )


def transcribe_file(
    audio_path: str,
    language: str = "auto",
    threads: int = 4,
    fallback_transcript: str = "",
    whisper_bin: str = WHISPER_BIN_DEFAULT,
    model: str = MODEL_DEFAULT,
) -> dict[str, Any]:
    """Transcribe an audio file using whisper-cli with safe fallback."""
    t0 = time.perf_counter()
    wlang = normalize_whisper_language(language)
    fallback = (fallback_transcript or "").strip()

    if not Path(audio_path).is_file():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    if not is_whisper_available(whisper_bin, model):
        return {
            "text": fallback,
            "language": wlang,
            "used_fallback": True,
            "stt_latency_ms": 0,
            "reason": "whisper_unavailable",
        }

    with tempfile.TemporaryDirectory(prefix="sq_stt_") as tmpdir:
        wav_target = str(Path(tmpdir) / "audio_16k.wav")
        try:
            ready_path = convert_to_16k_mono_wav(audio_path, wav_target)
        except Exception as exc:
            if fallback:
                return {
                    "text": fallback,
                    "language": wlang,
                    "used_fallback": True,
                    "stt_latency_ms": int((time.perf_counter() - t0) * 1000),
                    "reason": str(exc),
                }
            raise

        cmd = [
            whisper_bin,
            "-m",
            model,
            "-f",
            ready_path,
            "-t",
            str(max(1, int(threads))),
            "-l",
            wlang,
            "--no-timestamps",
        ]
        prompt = PROMPTS.get(wlang)
        if prompt:
            cmd.extend(["--prompt", prompt])

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            if fallback:
                return {
                    "text": fallback,
                    "language": wlang,
                    "used_fallback": True,
                    "stt_latency_ms": int((time.perf_counter() - t0) * 1000),
                    "reason": proc.stderr.strip() or f"whisper exit {proc.returncode}",
                }
            raise RuntimeError(f"whisper-cli failed (code {proc.returncode}): {proc.stderr}")

        lines: list[str] = []
        for line in proc.stdout.splitlines():
            s = line.strip()
            if not s or s.startswith(("whisper_", "main:", "system_info:", "read_audio_data:")):
                continue
            lines.append(s)

        raw_text = " ".join(lines).strip()
        if wlang == "ca" and raw_text:
            raw_text = clean_catalan_orthography(raw_text)

        used_fallback = False
        if not raw_text and fallback:
            raw_text = fallback
            used_fallback = True

        return {
            "text": raw_text,
            "language": wlang,
            "used_fallback": used_fallback,
            "stt_latency_ms": int((time.perf_counter() - t0) * 1000),
        }


def transcribe_bytes(
    audio_bytes: bytes,
    filename: str = "utterance.wav",
    language: str = "auto",
    threads: int = 4,
    fallback_transcript: str = "",
) -> dict[str, Any]:
    """Transcribe uploaded audio bytes with automatic temp-file cleanup and fallback."""
    suffix = Path(filename or "utterance.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(prefix="sq_upload_", suffix=suffix, delete=False) as tf:
        tf.write(audio_bytes)
        tmp_path = tf.name
    try:
        return transcribe_file(
            tmp_path,
            language=language,
            threads=threads,
            fallback_transcript=fallback_transcript,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
