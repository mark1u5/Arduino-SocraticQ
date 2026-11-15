#!/usr/bin/env python3
"""
stt_server.py — Robust Local Server for Whisper STT Demo
Datathon FME 2026 · Qualcomm QRB2210 / Arduino UNO Q Track

Decodes 16kHz PCM WAV bytes directly into NumPy arrays (bypassing PyAV/metadata_errors bug),
runs multilingual Whisper tiny (INT8 / CPU) with initial Catalan orthographic vocabulary prompts
and explicit post-processing corrections for Catalan math terms.
"""
import http.server
import io
import json
import math
import re
import socketserver
import sys
import time
import wave
import numpy as np

# Monkey-patch faster_whisper decode_audio to avoid PyAV 19.x metadata_errors bug
try:
    import faster_whisper.audio as fwa
    import av
    orig_decode = fwa.decode_audio

    def safe_decode_audio(input_file, sampling_rate=16000, split_stereo=False):
        resampler = av.audio.resampler.AudioResampler(
            format="s16",
            layout="mono" if not split_stereo else "stereo",
            rate=sampling_rate,
        )
        raw_buffer = io.BytesIO()
        dtype = None
        with av.open(input_file, mode="r") as container:
            frames = container.decode(audio=0)
            frames = fwa._ignore_invalid_frames(frames)
            frames = fwa._group_frames(frames, 500000)
            frames = fwa._resample_frames(frames, resampler)
            for frame in frames:
                arr = frame.to_ndarray()
                dtype = arr.dtype
                raw_buffer.write(arr)
        audio = np.frombuffer(raw_buffer.getbuffer(), dtype=dtype).astype(np.float32) / 32768.0
        return audio

    fwa.decode_audio = safe_decode_audio
except Exception as patch_err:
    sys.stderr.write(f"[STT Server] Warning on patch: {patch_err}\n")

from faster_whisper import WhisperModel

PORT = 8005
MODEL_NAME = "tiny"  # Exact 39M parameter model (Catalan, English, Spanish)

print(f"[STT Server] Loading Whisper model '{MODEL_NAME}' (INT8 on CPU)...")
t0 = time.time()
whisper_model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
print(f"[STT Server] Model '{MODEL_NAME}' ready in {time.time() - t0:.2f}s!")

# Domain-specific initial prompts to condition Whisper's language model & spelling
PROMPTS = {
    "ca": (
        "Enunciat d'un problema matemàtic de les proves Cangur en català: "
        "triangle, rectangle, quadrat, cercle, cub, prisma, polígon, angles, vèrtexs, costats, "
        "perímetre, àrea, fracció, proporció, percentatge, nombre, nombres, xifra, xifres, suma, resta, "
        "multiplicació, divisió, igual, quan, quant, quants, quantes, els, als, del, dels, pel, pels."
    ),
    "es": (
        "Enunciado de un problema matemático de la competición Canguro en español: "
        "triángulo, rectángulo, cuadrado, círculo, polígono, ángulos, vértices, lados, perímetro, "
        "área, fracción, porcentaje, número, cifras, suma, resta, multiplicación, división, cuánto, cuántos."
    ),
    "en": (
        "Math competition Kangaroo problem: triangle, rectangle, square, circle, polygon, angles, "
        "vertices, perimeter, area, fraction, ratio, percentage, number, digits, sum, difference, product."
    ),
}

# Post-processing regex dictionary to fix recurrent phonetic misspellings in Catalan
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
    """Applies canonical Catalan orthographic corrections for math vocabulary."""
    cleaned = text
    for pattern, replacement in CATALAN_CORRECTIONS:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    return cleaned


def wav_bytes_to_numpy(wav_bytes):
    """Converts 16-bit PCM WAV bytes directly to float32 NumPy array normalized to [-1.0, 1.0]."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        raw_frames = wf.readframes(n_frames)

        if sampwidth == 2:
            audio = np.frombuffer(raw_frames, dtype=np.int16).astype(np.float32) / 32768.0
        elif sampwidth == 4:
            audio = np.frombuffer(raw_frames, dtype=np.int32).astype(np.float32) / 2147483648.0
        else:
            audio = (np.frombuffer(raw_frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0

        if n_channels > 1:
            audio = audio[::n_channels]  # Mono

        # Resample to 16,000 Hz if necessary
        if framerate != 16000 and len(audio) > 0:
            target_len = int(len(audio) * 16000 / framerate)
            audio = np.interp(
                np.linspace(0, len(audio), target_len, endpoint=False),
                np.arange(len(audio)),
                audio
            ).astype(np.float32)

    return audio


HTML_PAGE = """<!DOCTYPE html>
<html lang="ca">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Whisper tiny Demo · Arduino UNO Q / Qualcomm QRB2210</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    @keyframes pulse-ring {
      0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.7); }
      70% { transform: scale(1.05); box-shadow: 0 0 0 20px rgba(239, 68, 68, 0); }
      100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
    }
    .recording-active { animation: pulse-ring 1.3s infinite; }
  </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen flex flex-col items-center p-4 md:p-8 font-sans">
  <div class="max-w-3xl w-full bg-slate-900 border border-slate-800 rounded-3xl shadow-2xl p-6 md:p-8 space-y-6">
    
    <!-- Capçalera -->
    <div class="border-b border-slate-800 pb-5 text-center space-y-2">
      <div class="inline-flex items-center gap-2 bg-indigo-950 text-indigo-400 px-3 py-1 rounded-full text-xs font-semibold tracking-wide border border-indigo-800/60">
        <span>⚡ QUALCOMM QRB2210 · ARDUINO UNO Q</span>
      </div>
      <h1 class="text-3xl font-extrabold text-white tracking-tight">🎙️ Whisper Edge STT Demo</h1>
      <p class="text-slate-400 text-sm max-w-xl mx-auto">
        Tria l'idioma abans de parlar per garantir màxima precisió ortogràfica (correcció automàtica de <em>triangle</em>, <em>quant/quan</em>, <em>als/els</em>).
      </p>
    </div>

    <!-- SELECTOR D'IDIOMA DESTACAT (ABANS DE GRAVAR) -->
    <div class="bg-slate-950/90 p-5 rounded-2xl border-2 border-indigo-500/50 space-y-3">
      <div class="flex items-center justify-between">
        <label class="text-xs font-bold uppercase tracking-wider text-indigo-300 flex items-center gap-2">
          <span>🌐 1. Selecciona l'idioma abans de gravar:</span>
        </label>
        <span id="active-badge" class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40">Català Actiu</span>
      </div>
      
      <!-- Radio button cards for language selection -->
      <div class="grid grid-cols-3 gap-3">
        <button type="button" onclick="setLang('ca')" id="btn-lang-ca"
          class="flex flex-col items-center justify-center p-3 rounded-xl border-2 border-indigo-500 bg-indigo-950/60 text-white font-bold transition-all shadow-md">
          <span class="text-2xl mb-1">🇦🇩/🏴</span>
          <span class="text-sm">Català</span>
          <span class="text-[10px] text-indigo-300 font-normal">Amb diccionari Cangur</span>
        </button>

        <button type="button" onclick="setLang('es')" id="btn-lang-es"
          class="flex flex-col items-center justify-center p-3 rounded-xl border-2 border-slate-800 bg-slate-900 hover:border-slate-700 text-slate-300 font-bold transition-all">
          <span class="text-2xl mb-1">🇪🇸</span>
          <span class="text-sm">Español</span>
          <span class="text-[10px] text-slate-400 font-normal">Canguro Matemático</span>
        </button>

        <button type="button" onclick="setLang('en')" id="btn-lang-en"
          class="flex flex-col items-center justify-center p-3 rounded-xl border-2 border-slate-800 bg-slate-900 hover:border-slate-700 text-slate-300 font-bold transition-all">
          <span class="text-2xl mb-1">🇬🇧</span>
          <span class="text-sm">English</span>
          <span class="text-[10px] text-slate-400 font-normal">Math Benchmark</span>
        </button>
      </div>
    </div>

    <!-- Botó Principal de Gravació -->
    <div class="flex flex-col items-center justify-center py-2 space-y-3">
      <button id="main-btn" class="w-28 h-28 rounded-full bg-indigo-600 hover:bg-indigo-500 active:scale-95 text-white flex flex-col items-center justify-center shadow-xl shadow-indigo-950 transition-all duration-200 focus:outline-none">
        <svg id="mic-icon" class="w-10 h-10 mb-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 02-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
        </svg>
        <span id="btn-label" class="text-xs font-bold uppercase tracking-wider">Gravar</span>
      </button>
      <span id="instruction-hint" class="text-xs text-slate-400 font-medium text-center">
        1. Fes clic per <strong>COMENÇAR</strong> a parlar<br>
        2. Fes clic de nou per <strong>ATURAR I TRANSCRIURE</strong>
      </span>
    </div>

    <!-- Nivell i Visualitzador d'Àudio -->
    <div class="space-y-1">
      <div class="flex justify-between text-xs text-slate-400 font-mono">
        <span id="vol-level">Nivell micròfon: 0%</span>
        <span id="timer-label">0.0 s gravats</span>
      </div>
      <canvas id="waveform-canvas" height="60" class="w-full bg-slate-950 rounded-xl border border-slate-800"></canvas>
    </div>

    <!-- Àudio Gravat (Per verificar què s'ha capturat) -->
    <div id="audio-playback-container" class="hidden bg-slate-950 p-3 rounded-xl border border-slate-800 space-y-1">
      <span class="text-xs text-slate-400 font-medium">Àudio capturat pel micròfon:</span>
      <audio id="audio-player" controls class="w-full h-8"></audio>
    </div>

    <!-- Resultat de la Transcripció Whisper -->
    <div class="space-y-2">
      <div class="flex justify-between items-center">
        <label class="text-xs font-bold text-slate-300 uppercase tracking-wider">📝 Transcripció Whisper tiny (Corregida per a matemàtiques)</label>
        <span id="metrics-badge" class="text-xs font-mono text-emerald-400 font-semibold"></span>
      </div>
      <div id="output-box" class="min-h-[110px] p-5 bg-slate-950 border-2 border-slate-800 rounded-2xl font-mono text-base leading-relaxed text-slate-100 select-text flex items-center justify-center text-center">
        <span class="text-slate-500 italic">Prem "GRAVAR", fes una pregunta de matemàtiques en veu alta i clica "ATURAR" per veure la transcripció.</span>
      </div>
    </div>

    <!-- Payload JSON preparat per al Qwen 0.8B -->
    <div class="space-y-2">
      <div class="flex justify-between items-center">
        <label class="text-xs font-bold text-emerald-400 uppercase tracking-wider">⚡ Crida Edge simulada (Qwen 0.8B al port 8081)</label>
      </div>
      <pre id="json-box" class="p-3 bg-slate-950 border border-slate-800 rounded-xl text-xs text-emerald-300 font-mono overflow-x-auto whitespace-pre-wrap">// Esperant entrada de veu...</pre>
    </div>

  </div>

  <script>
    let currentLang = 'ca'; // Default explicitly to Catalan
    let isRecording = false;
    let audioContext = null;
    let mediaStream = null;
    let processorNode = null;
    let pcmSamples = [];
    let timerId = null;
    let startTime = 0;
    let animId = null;
    let analyser = null;

    const mainBtn = document.getElementById('main-btn');
    const btnLabel = document.getElementById('btn-label');
    const timerLabel = document.getElementById('timer-label');
    const volLevel = document.getElementById('vol-level');
    const outputBox = document.getElementById('output-box');
    const jsonBox = document.getElementById('json-box');
    const metricsBadge = document.getElementById('metrics-badge');
    const instructionHint = document.getElementById('instruction-hint');
    const activeBadge = document.getElementById('active-badge');
    const audioContainer = document.getElementById('audio-playback-container');
    const audioPlayer = document.getElementById('audio-player');
    const canvas = document.getElementById('waveform-canvas');
    const canvasCtx = canvas.getContext('2d');

    function setLang(lang) {
      if (isRecording) return;
      currentLang = lang;
      
      const langs = ['ca', 'es', 'en'];
      langs.forEach(l => {
        const btn = document.getElementById(`btn-lang-${l}`);
        if (l === lang) {
          btn.className = "flex flex-col items-center justify-center p-3 rounded-xl border-2 border-indigo-500 bg-indigo-950/60 text-white font-bold transition-all shadow-md";
        } else {
          btn.className = "flex flex-col items-center justify-center p-3 rounded-xl border-2 border-slate-800 bg-slate-900 hover:border-slate-700 text-slate-300 font-bold transition-all";
        }
      });

      if (lang === 'ca') {
        activeBadge.textContent = "Català Actiu";
        activeBadge.className = "px-2.5 py-0.5 rounded-full text-xs font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40";
      } else if (lang === 'es') {
        activeBadge.textContent = "Español Activo";
        activeBadge.className = "px-2.5 py-0.5 rounded-full text-xs font-bold bg-rose-500/20 text-rose-300 border border-rose-500/40";
      } else {
        activeBadge.textContent = "English Active";
        activeBadge.className = "px-2.5 py-0.5 rounded-full text-xs font-bold bg-blue-500/20 text-blue-300 border border-blue-500/40";
      }
    }

    // Canvas Idle
    function drawIdle() {
      canvas.width = canvas.clientWidth * window.devicePixelRatio;
      canvas.height = canvas.clientHeight * window.devicePixelRatio;
      canvasCtx.fillStyle = '#020617';
      canvasCtx.fillRect(0, 0, canvas.width, canvas.height);
      canvasCtx.lineWidth = 1 * window.devicePixelRatio;
      canvasCtx.strokeStyle = '#334155';
      canvasCtx.beginPath();
      canvasCtx.moveTo(0, canvas.height / 2);
      canvasCtx.lineTo(canvas.width, canvas.height / 2);
      canvasCtx.stroke();
    }
    drawIdle();
    window.addEventListener('resize', drawIdle);

    async function startRecording() {
      try {
        mediaStream = await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            sampleRate: 16000
          }
        });
      } catch (err) {
        alert("Error de micròfon: Assegura't de donar permís al navegador!");
        return;
      }

      audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
      const source = audioContext.createMediaStreamSource(mediaStream);

      analyser = audioContext.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);

      processorNode = audioContext.createScriptProcessor(4096, 1, 1);
      pcmSamples = [];

      processorNode.onaudioprocess = (e) => {
        if (!isRecording) return;
        const inputData = e.inputBuffer.getChannelData(0);
        pcmSamples.push(new Float32Array(inputData));

        let sum = 0;
        for (let i = 0; i < inputData.length; i++) sum += inputData[i] * inputData[i];
        const rms = Math.sqrt(sum / inputData.length);
        const pct = Math.min(100, Math.round(rms * 400));
        volLevel.textContent = `Nivell micròfon: ${pct}%`;
      };

      source.connect(processorNode);
      processorNode.connect(audioContext.destination);

      isRecording = true;
      startTime = Date.now();
      timerId = setInterval(() => {
        const secs = ((Date.now() - startTime) / 1000).toFixed(1);
        timerLabel.textContent = `${secs} s gravats`;
      }, 100);

      mainBtn.classList.remove('bg-indigo-600', 'hover:bg-indigo-500');
      mainBtn.classList.add('bg-rose-600', 'hover:bg-rose-500', 'recording-active');
      btnLabel.textContent = "ATURAR";
      instructionHint.innerHTML = "Parla en <strong class='text-white uppercase'>" + currentLang + "</strong>. Prem el botó vermell per finalitzar.";
      drawWave();
    }

    async function stopRecording() {
      if (!isRecording) return;
      isRecording = false;
      clearInterval(timerId);
      if (animId) cancelAnimationFrame(animId);

      mainBtn.classList.remove('bg-rose-600', 'hover:bg-rose-500', 'recording-active');
      mainBtn.classList.add('bg-indigo-600', 'hover:bg-indigo-500');
      btnLabel.textContent = "GRAVAR";
      instructionHint.innerHTML = "Processant la teva veu...";
      drawIdle();

      if (processorNode) processorNode.disconnect();
      if (mediaStream) mediaStream.getTracks().forEach(t => t.stop());
      if (audioContext) audioContext.close();

      outputBox.innerHTML = '<div class="space-y-2"><div class="inline-block animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-indigo-400"></div><p class="text-sm text-indigo-300">Executant inferència de Whisper tiny en ' + currentLang.toUpperCase() + '...</p></div>';

      let totalLength = 0;
      for (const chunk of pcmSamples) totalLength += chunk.length;
      const mergedSamples = new Float32Array(totalLength);
      let offset = 0;
      for (const chunk of pcmSamples) {
        mergedSamples.set(chunk, offset);
        offset += chunk.length;
      }

      const wavBlob = encodeWAV(mergedSamples, 16000);
      
      const audioUrl = URL.createObjectURL(wavBlob);
      audioPlayer.src = audioUrl;
      audioContainer.classList.remove('hidden');

      await sendWavToServer(wavBlob);
    }

    function encodeWAV(samples, sampleRate) {
      const buffer = new ArrayBuffer(44 + samples.length * 2);
      const view = new DataView(buffer);

      writeString(view, 0, 'RIFF');
      view.setUint32(4, 36 + samples.length * 2, true);
      writeString(view, 8, 'WAVE');
      writeString(view, 12, 'fmt ');
      view.setUint32(16, 16, true);
      view.setUint16(20, 1, true);
      view.setUint16(22, 1, true);
      view.setUint32(24, sampleRate, true);
      view.setUint32(28, sampleRate * 2, true);
      view.setUint16(32, 2, true);
      view.setUint16(34, 16, true);
      writeString(view, 36, 'data');
      view.setUint32(40, samples.length * 2, true);

      let index = 44;
      for (let i = 0; i < samples.length; i++) {
        let s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(index, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
        index += 2;
      }
      return new Blob([view], { type: 'audio/wav' });
    }

    function writeString(view, offset, string) {
      for (let i = 0; i < string.length; i++) {
        view.setUint8(offset + i, string.charCodeAt(i));
      }
    }

    function drawWave() {
      if (!analyser) return;
      const bufferLength = analyser.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);
      analyser.getByteTimeDomainData(dataArray);

      canvas.width = canvas.clientWidth * window.devicePixelRatio;
      canvas.height = canvas.clientHeight * window.devicePixelRatio;

      canvasCtx.fillStyle = '#020617';
      canvasCtx.fillRect(0, 0, canvas.width, canvas.height);
      canvasCtx.lineWidth = 2 * window.devicePixelRatio;
      canvasCtx.strokeStyle = '#10b981';
      canvasCtx.beginPath();

      const sliceWidth = canvas.width * 1.0 / bufferLength;
      let x = 0;

      for (let i = 0; i < bufferLength; i++) {
        const v = dataArray[i] / 128.0;
        const y = v * (canvas.height / 2);
        if (i === 0) canvasCtx.moveTo(x, y);
        else canvasCtx.lineTo(x, y);
        x += sliceWidth;
      }
      canvasCtx.lineTo(canvas.width, canvas.height / 2);
      canvasCtx.stroke();
      if (isRecording) animId = requestAnimationFrame(drawWave);
    }

    async function sendWavToServer(blob) {
      try {
        const res = await fetch(`/transcribe?lang=${currentLang}`, {
          method: 'POST',
          headers: { 'Content-Type': 'audio/wav' },
          body: blob
        });
        const data = await res.json();

        instructionHint.innerHTML = "1. Fes clic per <strong>COMENÇAR</strong> a parlar<br>2. Fes clic de nou per <strong>ATURAR I TRANSCRIURE</strong>";

        if (data.text && data.text.trim()) {
          outputBox.innerHTML = `<span class="text-emerald-300 font-bold text-xl leading-relaxed">"${data.text.trim()}"</span>`;
          metricsBadge.textContent = `Temps: ${data.latency_s}s · Confiança: ${data.confidence}% [${data.language.toUpperCase()}]`;

          const payload = {
            model: "qwen3.5-0.8b",
            messages: [
              { role: "system", content: "You are a Socratic Kangaroo math tutor on Qualcomm QRB2210." },
              { role: "user", content: data.text.trim() }
            ],
            temperature: 0,
            max_tokens: 150
          };
          jsonBox.textContent = `POST http://127.0.0.1:8081/v1/chat/completions\\nContent-Type: application/json\\n\\n${JSON.stringify(payload, null, 2)}`;
        } else {
          outputBox.innerHTML = `<div class="space-y-1"><span class="text-amber-400 font-semibold">No s'ha detectat cap paraula clara.</span><p class="text-xs text-slate-500">Escolta l'àudio capturat de dalt per comprovar si el micròfon se sent bé.</p></div>`;
          metricsBadge.textContent = `Temps: ${data.latency_s}s`;
        }
      } catch (err) {
        outputBox.innerHTML = `<span class="text-rose-400 font-semibold">Error: ${err.message}</span>`;
      }
    }

    mainBtn.addEventListener('click', () => {
      if (isRecording) {
        stopRecording();
      } else {
        startRecording();
      }
    });
  </script>
</body>
</html>
"""


class RobustSTTHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "model": MODEL_NAME}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path.startswith("/transcribe"):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)

            # Extract language parameter
            lang = "ca"  # Default explicitly to Catalan
            if "lang=es" in self.path:
                lang = "es"
            elif "lang=en" in self.path:
                lang = "en"
            elif "lang=ca" in self.path:
                lang = "ca"

            initial_prompt = PROMPTS.get(lang, None)
            duration_s = round((content_length - 44) / (16000 * 2), 2)
            sys.stderr.write(f"\n[STT Server] >>> Audio: {content_length} bytes (~{duration_s}s), language={lang}\n")

            try:
                t_start = time.perf_counter()
                
                # Decode WAV bytes directly into NumPy float32 array
                audio_np = wav_bytes_to_numpy(body)
                sys.stderr.write(f"[STT Server] Decoded float32: shape={audio_np.shape}\n")

                # Transcribe directly from NumPy array with domain-specific prompt
                segments, info = whisper_model.transcribe(
                    audio_np,
                    beam_size=5,
                    language=lang,
                    initial_prompt=initial_prompt,
                    vad_filter=False
                )
                text_parts = []
                logprobs = []
                for seg in segments:
                    text_parts.append(seg.text.strip())
                    logprobs.append(seg.avg_logprob)

                raw_text = " ".join(text_parts).strip()
                
                # Apply Catalan orthographic post-processing
                if lang == "ca":
                    final_text = clean_catalan_orthography(raw_text)
                else:
                    final_text = raw_text

                t_elapsed = round(time.perf_counter() - t_start, 2)
                avg_logprob = sum(logprobs) / len(logprobs) if logprobs else -1.0
                conf = round(max(0.0, min(100.0, math.exp(avg_logprob) * 100)), 1)
                detected_lang = info.language

                sys.stderr.write(f"[STT Server] <<< Transcribed in {t_elapsed}s (lang: {detected_lang}): '{final_text}' (raw: '{raw_text}')\n")

                response_payload = {
                    "text": final_text,
                    "confidence": conf,
                    "latency_s": t_elapsed,
                    "language": detected_lang,
                    "model": f"whisper-{MODEL_NAME}-int8"
                }
            except Exception as e:
                import traceback
                traceback.print_exc(file=sys.stderr)
                sys.stderr.write(f"[STT Server] !!! Error transcribing: {e}\n")
                response_payload = {
                    "text": "",
                    "confidence": 0,
                    "latency_s": 0,
                    "language": "unknown",
                    "error": str(e)
                }

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(response_payload).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def run():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), RobustSTTHandler) as httpd:
        print(f"\n=======================================================")
        print(f"🚀 Robust Whisper STT Server is LIVE at: http://localhost:{PORT}")
        print(f"=======================================================\n")
        httpd.serve_forever()


if __name__ == "__main__":
    run()
