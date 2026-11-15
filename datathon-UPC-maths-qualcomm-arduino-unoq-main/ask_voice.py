#!/usr/bin/env python3
"""Ask a math question to Qwen 0.8B using speech-to-text on Arduino UNO Q.

Usage on the board:
    python3 ask_voice.py /home/arduino/whisper.cpp/samples/jfk.wav
    python3 ask_voice.py my_math_question.wav --lang auto
"""
import argparse
import sys
import time

from copilot.client import post
from copilot.config import CONFIG
from copilot.stt import transcribe


def main():
    parser = argparse.ArgumentParser(description="End-to-end voice math assistant on Arduino UNO Q")
    parser.add_argument("audio", help="Path to input audio file (WAV, MP3, FLAC, OGG)")
    parser.add_argument("--lang", default="auto", help="Language code: auto, en, ca, es")
    args = parser.parse_args()

    print(f"\n[1/2] 🎙️ Transcribing audio with native whisper.cpp (ggml-tiny-q5_1)...")
    t0 = time.perf_counter()
    transcription = transcribe(args.audio, language=args.lang)
    t_stt = time.perf_counter() - t0

    if not transcription:
        print("❌ No speech recognized.")
        sys.exit(1)

    print(f"      Recognized ({t_stt:.2f}s): \"{transcription}\"")

    print(f"\n[2/2] 🧠 Querying Qwen 0.8B on port {CONFIG.get('LLM_PORT', 8081)}...")
    t1 = time.perf_counter()
    url = f"http://127.0.0.1:{CONFIG.get('LLM_PORT', 8081)}/v1/chat/completions"
    payload = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a helpful math tutor running on an Arduino UNO Q. "
                    "Provide a concise, direct explanation and the final answer."
                ),
            },
            {"role": "user", "content": transcription},
        ],
        "temperature": 0,
        "max_tokens": 100,
    }

    resp = post(url, payload, timeout=60)
    t_llm = time.perf_counter() - t1
    answer = resp["choices"][0]["message"]["content"].strip()

    print(f"\n{'=' * 60}")
    print(f"🤖 Qwen 0.8B Response ({t_llm:.2f}s):\n{answer}")
    print(f"{'=' * 60}")
    print(f"⏱️ Total Latency: {t_stt + t_llm:.2f}s (STT: {t_stt:.2f}s, LLM: {t_llm:.2f}s)\n")


if __name__ == "__main__":
    main()
