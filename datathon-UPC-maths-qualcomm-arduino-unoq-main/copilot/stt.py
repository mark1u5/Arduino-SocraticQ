"""Speech-to-text bridge using native whisper.cpp on Qualcomm QRB2210 (Arduino UNO Q)."""
import os
import re
import subprocess
import sys

WHISPER_BIN_DEFAULT = "/home/arduino/whisper.cpp/build/bin/whisper-cli"
MODEL_DEFAULT = "/home/arduino/models/ggml-tiny-q5_1.bin"

# Mathematical prompt conditioning to force correct orthography in Catalan
PROMPTS = {
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

# Post-processing corrections for Catalan math terms
CATALAN_CORRECTIONS = [
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


def clean_catalan_orthography(text: str) -> str:
    cleaned = text
    for pattern, replacement in CATALAN_CORRECTIONS:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    return cleaned


def transcribe(audio_path: str, model: str = MODEL_DEFAULT, language: str = "ca", threads: int = 4) -> str:
    """Transcribes an audio file (WAV, MP3, FLAC, OGG) using native whisper.cpp.

    Args:
        audio_path: path to audio file on disk
        model: path to quantized ggml model (default: ggml-tiny-q5_1.bin)
        language: 'ca', 'es', 'en', or 'auto'
        threads: number of CPU threads (default: 4 for QRB2210 quad-core)

    Returns:
        Transcribed clean string with domain orthographic corrections
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    cmd = [
        WHISPER_BIN_DEFAULT,
        "-m", model,
        "-f", audio_path,
        "-t", str(threads),
        "-l", language,
        "--no-timestamps"
    ]

    prompt = PROMPTS.get(language, None)
    if prompt:
        cmd.extend(["--prompt", prompt])

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"whisper-cli failed (code {proc.returncode}): {proc.stderr}")

    lines = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("whisper_") or line.startswith("main:") or line.startswith("system_info:") or line.startswith("read_audio_data:"):
            continue
        lines.append(line)

    raw_text = " ".join(lines).strip()
    if language == "ca":
        return clean_catalan_orthography(raw_text)
    return raw_text


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 -m copilot.stt <audio_file> [language]")
        sys.exit(1)
    target = sys.argv[1]
    target_lang = sys.argv[2] if len(sys.argv) > 2 else "ca"
    result = transcribe(target, language=target_lang)
    print(f"Transcription: {result}")
