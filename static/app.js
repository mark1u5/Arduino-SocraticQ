/* SocraticQ — 100% Offline-Ready Watery Glass + Workbench Controller */
(() => {
  "use strict";

  const state = {
    tutorMode: "teach", // "teach" | "solve" | "explorer"
    lang: (localStorage.getItem("sq.lang") || "").replace(/"/g, "") === "cat" ? "cat" : "eng",
    problems: [],
    activeProblemId: "FREE-MATH",
    selectedOption: null,
    attemptNum: 1,
    sessionId: "sess-web-" + Math.random().toString(36).slice(2, 8),
    turnStartTs: performance.now(),
    isRecording: false,
    mediaRecorder: null,
    mediaStream: null,
    chunks: [],
    recognition: null,
    liveTranscript: "",
    // 4-Rung Hint Ladder state
    activeRung: 1,
    hintLadder: null,
    rung4Unlocked: false,
    // Explorer / Workbench state
    expProblems: [],
    expIndex: 0,
    expLang: "eng",
    expSelectedOption: null,
    expR4Confirmed: false,
    expLastInspect: null,
  };

  const $ = (id) => document.getElementById(id);

  function esc(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // --- Self-Contained Offline Math Renderer (Zero CDN Dependencies) ---
  const TEX_SYMBOLS = {
    "\\alpha": "α",
    "\\beta": "β",
    "\\gamma": "γ",
    "\\delta": "δ",
    "\\theta": "θ",
    "\\lambda": "λ",
    "\\mu": "μ",
    "\\pi": "π",
    "\\sigma": "σ",
    "\\phi": "φ",
    "\\omega": "ω",
    "\\Delta": "Δ",
    "\\Sigma": "Σ",
    "\\Omega": "Ω",
    "\\times": "×",
    "\\cdot": "·",
    "\\div": "÷",
    "\\pm": "±",
    "\\mp": "∓",
    "\\leq": "≤",
    "\\le": "≤",
    "\\geq": "≥",
    "\\ge": "≥",
    "\\neq": "≠",
    "\\ne": "≠",
    "\\approx": "≈",
    "\\equiv": "≡",
    "\\infty": "∞",
    "\\degree": "°",
    "\\circ": "°",
    "\\rightarrow": "→",
    "\\to": "→",
    "\\Rightarrow": "⇒",
    "\\leftarrow": "←",
    "\\leftrightarrow": "↔",
    "\\in": "∈",
    "\\notin": "∉",
    "\\subset": "⊂",
    "\\subseteq": "⊆",
    "\\cup": "∪",
    "\\cap": "∩",
    "\\forall": "∀",
    "\\exists": "∃",
    "\\ldots": "…",
    "\\dots": "…",
    "\\cdots": "⋯",
  };

  function formatTexSegment(escapedTex) {
    let out = escapedTex.trim();
    // Unwrap \text{...}, \mathrm{...}, \mathbf{...}
    out = out.replace(/\\(?:text|mathrm|mathbf|mathit)\{([^{}]*)\}/g, "$1");
    out = out.replace(/\\pmod\{([^{}]*)\}/g, " (mod $1)").replace(/\\bmod\b/g, " mod ");
    // Fractions \frac{a}{b} (handles up to 2 nested levels cleanly)
    for (let i = 0; i < 2; i++) {
      out = out.replace(
        /\\frac\{([^{}]*)\}\{([^{}]*)\}/g,
        '<span class="sq-frac"><span class="sq-frac-num">$1</span><span class="sq-frac-den">$2</span></span>'
      );
    }
    // Square roots \sqrt{x}
    out = out.replace(/\\sqrt\{([^{}]*)\}/g, '√<span class="sq-sqrt">$1</span>');
    // Symbols
    Object.entries(TEX_SYMBOLS).forEach(([cmd, sym]) => {
      out = out.split(cmd).join(sym);
    });
    // Superscripts & subscripts
    out = out.replace(/\^\{([^{}]+)\}/g, "<sup>$1</sup>");
    out = out.replace(/_\{([^{}]+)\}/g, "<sub>$1</sub>");
    out = out.replace(/\^([A-Za-z0-9°+\-])/g, "<sup>$1</sup>");
    out = out.replace(/_([A-Za-z0-9])/g, "<sub>$1</sub>");
    return out;
  }

  function formatMathHtml(rawText) {
    const escaped = esc(rawText);
    const tokens = [];
    const stash = (html) => {
      const idx = tokens.length;
      tokens.push(html);
      return `\u0000M${idx}\u0000`;
    };

    let text = escaped
      .replace(/\$\$([\s\S]+?)\$\$/g, (_, m) => stash(`<span class="sq-math-display">${formatTexSegment(m)}</span>`))
      .replace(/\\\[([\s\S]+?)\\\]/g, (_, m) => stash(`<span class="sq-math-display">${formatTexSegment(m)}</span>`))
      .replace(/\\\(([\s\S]+?)\\\)/g, (_, m) => stash(`<span class="sq-math-inline">${formatTexSegment(m)}</span>`))
      .replace(/\$([^$\n]+?)\$/g, (_, m) => stash(`<span class="sq-math-inline">${formatTexSegment(m)}</span>`));

    // Also render bare superscripts like x^2 or 10^3 outside $...$ safely
    text = text.replace(/([A-Za-z0-9)])\^(\{([^{}]+)\}|([0-9]+))/g, (_, base, _w, braced, simple) => {
      return `${base}<sup>${braced || simple}</sup>`;
    });

    return text.replace(/\u0000M(\d+)\u0000/g, (_, idx) => tokens[Number(idx)] || "");
  }

  function renderMathInText(node, rawText) {
    if (!node) return;
    node.innerHTML = formatMathHtml(rawText);
  }

  function speakIfEnabled(text) {
    const toggle = $("tts-toggle");
    if (!toggle || !toggle.checked || !("speechSynthesis" in window) || !text) return;
    try {
      window.speechSynthesis.cancel();
      const utter = new SpeechSynthesisUtterance(text);
      utter.lang = state.lang === "cat" ? "ca-ES" : "en-US";
      const voices = window.speechSynthesis.getVoices() || [];
      const match = voices.find((v) => v.lang && v.lang.toLowerCase().startsWith(state.lang === "cat" ? "ca" : "en"));
      if (match) utter.voice = match;
      utter.rate = 1.02;
      window.speechSynthesis.speak(utter);
    } catch (_) {}
  }

  function resetTurnTimer() {
    state.turnStartTs = performance.now();
  }

  function getHesitationMs() {
    return Math.max(150, Math.round(performance.now() - state.turnStartTs));
  }

  function removeEmptyState() {
    const empty = $("empty-state");
    if (empty) empty.remove();
  }

  // --- Latency & Offline HUD ---
  function updateHud(info = {}) {
    const badge = $("hud-offline-badge");
    const dot = $("status-dot");
    const label = $("status-label");
    const promptEl = $("hud-prompt-n");
    const tokEl = $("hud-tok-s");
    const ramEl = $("hud-ram-free");

    if (info.offline_mode !== undefined) {
      const isOffline = Boolean(info.offline_mode);
      if (badge) {
        badge.classList.toggle("offline", isOffline);
        badge.classList.toggle("online", !isOffline);
      }
      if (dot) dot.classList.toggle("offline", isOffline);
      if (label) label.textContent = isOffline ? "OFFLINE" : "ONLINE";
    }
    if (info.prompt_n !== undefined && info.prompt_n !== null && promptEl) {
      promptEl.textContent = `prompt_n ${Number(info.prompt_n)}`;
    }
    if (info.tok_s !== undefined && info.tok_s !== null && tokEl) {
      tokEl.textContent = `${Number(info.tok_s).toFixed(1)} tok/s`;
    }
    if (info.ram_free_mb !== undefined && info.ram_free_mb !== null && ramEl) {
      const mb = Math.round(Number(info.ram_free_mb));
      ramEl.textContent = mb > 0 ? `free ${mb} MB` : "free — MB";
    }
  }

  // --- Three-Mode Switch ("teach" | "solve" | "explorer") ---
  function setTutorMode(mode) {
    if (mode === "explorer") state.tutorMode = "explorer";
    else if (mode === "solve") state.tutorMode = "solve";
    else state.tutorMode = "teach";

    const teachBtn = $("mode-teach");
    const solveBtn = $("mode-solve");
    const expBtn = $("mode-explorer");

    teachBtn.classList.toggle("active", state.tutorMode === "teach");
    teachBtn.setAttribute("aria-selected", String(state.tutorMode === "teach"));
    solveBtn.classList.toggle("active", state.tutorMode === "solve");
    solveBtn.setAttribute("aria-selected", String(state.tutorMode === "solve"));
    expBtn.classList.toggle("active", state.tutorMode === "explorer");
    expBtn.setAttribute("aria-selected", String(state.tutorMode === "explorer"));

    const isExplorer = state.tutorMode === "explorer";
    $("tutor-view").hidden = isExplorer;
    $("explorer-view").hidden = !isExplorer;

    if (isExplorer && state.expProblems.length === 0) {
      loadExplorerProblems();
    } else if (isExplorer && state.expProblems.length > 0) {
      runExplorerInspect(false);
    }
  }

  // --- 4-Rung Hint Ladder ---
  function renderHintLadder() {
    const contentEl = $("ladder-content");
    const gateEl = $("rung4-confirm-box");
    const ladder = state.hintLadder;

    [1, 2, 3, 4].forEach((r) => {
      const btn = $(`rung-btn-${r}`);
      if (btn) btn.classList.toggle("active", state.activeRung === r);
    });

    if (gateEl) {
      gateEl.hidden = !(state.activeRung === 4 && !state.rung4Unlocked);
    }

    if (!contentEl) return;
    if (state.activeProblemId === "FREE-MATH" && !ladder) {
      contentEl.innerHTML = "Select a Cangur problem above (or ask a math question) to populate Rungs 1–4.";
      return;
    }

    if (!ladder) {
      contentEl.innerHTML = "Loading 4-Rung Hint Ladder…";
      return;
    }

    if (state.activeRung === 1) {
      const r1 = ladder.rung1 || {};
      if (r1.available && r1.statement) {
        contentEl.innerHTML = `
          <div><strong>Easier Neighbour (${esc(r1.problem_id || "Tier 1")} · ${esc(r1.category || "")} · Tier ${esc(r1.difficulty_level || 1)}):</strong> ${formatMathHtml(r1.statement)}</div>
          <div class="sq-ladder-sub">Pattern: ${formatMathHtml(r1.intuition_hook || "")}</div>
        `;
      } else {
        contentEl.innerHTML = `<div><strong>Easier Case:</strong> ${formatMathHtml(r1.intuition_hook || "Test a smaller version of the problem first.")}</div>`;
      }
      return;
    }

    if (state.activeRung === 2) {
      const r2 = ladder.rung2 || {};
      const votesStr = r2.votes
        ? " · Votes: " + Object.entries(r2.votes).map(([k, v]) => `${k}(${v})`).join(", ")
        : "";
      contentEl.innerHTML = `
        <div><strong>Topic:</strong> ${esc(r2.topic || "Math")} · <strong>Strategy:</strong> ${formatMathHtml(r2.strategy || r2.pedagogical_move || "")}</div>
        <div class="sq-ladder-sub">Move: ${esc(r2.pedagogical_move || "DECOMPOSE_PROBLEM")} (${esc(r2.socratic_dimension || "Clarification")})${esc(votesStr)}</div>
      `;
      return;
    }

    if (state.activeRung === 3) {
      const r3 = ladder.rung3 || {};
      if (r3.hint_text) {
        const meta = r3.latency_ms != null ? `${esc(r3.model_id || "edge")} · ${esc(r3.latency_ms)} ms · 0 leaks` : "0 leaks";
        contentEl.innerHTML = `
          <div><strong>Socratic Nudge:</strong> ${formatMathHtml(r3.hint_text)}</div>
          <div class="sq-ladder-sub">${meta}</div>
        `;
      } else {
        contentEl.innerHTML = `
          <div>Click <strong>R3 Socratic Nudge</strong> again or send your thought below to generate a safe Socratic hint.</div>
        `;
      }
      return;
    }

    if (state.activeRung === 4) {
      const r4 = ladder.rung4 || {};
      if (state.rung4Unlocked && r4.solution_text) {
        contentEl.innerHTML = `
          <div><strong>Rung 4 · Full Worked Solution:</strong></div>
          <div style="margin-top:4px;white-space:pre-wrap;">${formatMathHtml(r4.solution_text)}</div>
        `;
      } else {
        contentEl.innerHTML = `<div><strong>Rung 4 (Gated):</strong> Full solution is locked until you explicitly confirm below.</div>`;
      }
    }
  }

  async function loadProblemLadder(pid) {
    if (!pid || pid === "FREE-MATH") {
      state.hintLadder = null;
      renderHintLadder();
      return;
    }
    try {
      const resp = await fetch(`/api/problem/${encodeURIComponent(pid)}/ladder`);
      if (!resp.ok) return;
      const data = await resp.json();
      state.hintLadder = data.hint_ladder || null;
      renderHintLadder();
    } catch (_) {}
  }

  async function requestRung3Hint() {
    const pid = state.activeProblemId === "FREE-MATH" ? "GENERAL-MATH" : state.activeProblemId;
    const confidence = parseInt($("conf-slider").value || "50", 10);
    const contentEl = $("ladder-content");
    if (contentEl) contentEl.textContent = "Generating safe Rung 3 Socratic nudge…";
    try {
      const resp = await fetch("/api/text-query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          problem_id: pid,
          text: "Give me a Socratic hint for this problem.",
          event_type: "HINT_REQUEST",
          attempt_num: state.attemptNum,
          confidence_pct: confidence,
          selected_option: state.selectedOption,
          session_id: state.sessionId,
          hesitation_ms: getHesitationMs(),
          mode: "teach",
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      state.attemptNum += 1;
      updateHud(data);
      if (data.hint_ladder) state.hintLadder = data.hint_ladder;
      renderHintLadder();
      appendTutorBubble(data);
    } catch (_) {
      if (contentEl) contentEl.textContent = "Unable to generate Rung 3 hint right now.";
    }
  }

  async function unlockRung4Solution() {
    const pid = state.activeProblemId === "FREE-MATH" ? "GENERAL-MATH" : state.activeProblemId;
    const confidence = parseInt($("conf-slider").value || "50", 10);
    const contentEl = $("ladder-content");
    if (contentEl) contentEl.textContent = "Unlocking Rung 4 worked solution…";
    try {
      const resp = await fetch("/api/text-query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          problem_id: pid,
          text: $("math-input").value.trim() || "Show step-by-step solution.",
          event_type: "HINT_REQUEST",
          attempt_num: state.attemptNum,
          confidence_pct: confidence,
          selected_option: state.selectedOption,
          session_id: state.sessionId,
          hesitation_ms: getHesitationMs(),
          mode: "solve",
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      state.rung4Unlocked = true;
      updateHud(data);
      if (data.hint_ladder) state.hintLadder = data.hint_ladder;
      if (state.hintLadder && state.hintLadder.rung4) {
        state.hintLadder.rung4.solution_text =
          data.solution_text || state.hintLadder.rung4.solution_text || data.socratic_response?.hint_text;
      }
      renderHintLadder();
      appendTutorBubble({ ...data, _confirmedSolution: true });
    } catch (_) {
      if (contentEl) contentEl.textContent = "Failed to unlock Rung 4 solution.";
    }
  }

  // --- Problem Selection ---
  function renderProblemDropdown() {
    const sel = $("problem-select");
    if (!sel) return;
    sel.innerHTML = '<option value="FREE-MATH">Free Math — Ask anything</option>';
    state.problems.forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.problem_id;
      opt.textContent = `${p.problem_id} · ${p.category} (Tier ${p.difficulty_level})`;
      sel.appendChild(opt);
    });
    sel.value = state.activeProblemId;
  }

  function selectProblemById(pid) {
    state.activeProblemId = pid || "FREE-MATH";
    state.selectedOption = null;
    state.attemptNum = 1;
    state.activeRung = 1;
    state.rung4Unlocked = false;
    state.hintLadder = null;
    resetTurnTimer();

    const metaEl = $("active-problem-meta");
    const stemEl = $("active-problem-stem");
    const optsRow = $("active-options-row");
    const submitOptBtn = $("btn-submit-option");

    optsRow.innerHTML = "";
    submitOptBtn.style.display = "none";

    if (state.activeProblemId === "FREE-MATH") {
      metaEl.textContent = "Free Math";
      stemEl.textContent = "Ask any algebra, geometry, calculus, or contest math question below.";
      renderHintLadder();
      return;
    }

    const item = state.problems.find((p) => p.problem_id === state.activeProblemId);
    if (!item) return;

    const stmtText = state.lang === "cat" && item.statement_cat ? item.statement_cat : (item.statement || "");
    const optsMap = state.lang === "cat" && item.options_cat && Object.keys(item.options_cat).length ? item.options_cat : item.options;
    metaEl.textContent = `${item.category} · Tier ${item.difficulty_level}`;
    renderMathInText(stemEl, stmtText);

    if (optsMap && Object.keys(optsMap).length > 0) {
      Object.entries(optsMap).forEach(([key, val]) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "sq-opt-btn";
        btn.innerHTML = `${esc(key)}) ${formatMathHtml(val)}`;
        btn.onclick = () => {
          state.selectedOption = key;
          Array.from(optsRow.children).forEach((c) => c.classList.remove("selected"));
          btn.classList.add("selected");
          submitOptBtn.textContent = `Submit ${key}`;
          submitOptBtn.style.display = "inline-flex";
        };
        optsRow.appendChild(btn);
      });
    }

    loadProblemLadder(state.activeProblemId);
  }

  async function loadProblems() {
    try {
      const resp = await fetch("/problems");
      if (!resp.ok) return;
      state.problems = await resp.json();
      renderProblemDropdown();
      if (state.problems.length > 0) {
        state.activeProblemId = state.problems[0].problem_id;
        $("problem-select").value = state.activeProblemId;
        selectProblemById(state.activeProblemId);
      }
    } catch (_) {}
  }

  // --- Conversation Stream Rendering ---
  function appendUserBubble(text, option = null) {
    removeEmptyState();
    const stream = $("dialogue-stream");
    const msg = document.createElement("div");
    msg.className = "sq-msg sq-msg-user";
    const display = text || (option ? `Submitted option ${option}` : "Hint request");
    msg.innerHTML = `<div class="sq-msg-body">${formatMathHtml(display)}</div>`;
    stream.appendChild(msg);
    stream.scrollTop = stream.scrollHeight;
  }

  function appendTutorBubble(data) {
    removeEmptyState();
    const stream = $("dialogue-stream");
    const sr = data.socratic_response || data;
    const guardrailTriggered = Boolean(data.guardrail_triggered);
    const solutionText = data.solution_text || sr.solution_text || "";
    const hintText = sr.hint_text || "";
    const isSolveMode = (data.mode || state.tutorMode) === "solve";
    const preConfirmed = Boolean(data._confirmedSolution);

    const msg = document.createElement("div");
    msg.className = "sq-msg sq-msg-tutor";

    let pillHtml = "";
    if (guardrailTriggered) {
      pillHtml = `<div class="sq-msg-pill guardrail">Socratic Guardrail · ${esc(data.guardrail_reason || "Guided")}</div>`;
    } else if (isSolveMode) {
      pillHtml = `<div class="sq-msg-pill solve">Rung 4 · Worked Solution</div>`;
    }

    const bodyHtml = hintText ? `<div class="sq-msg-body">${formatMathHtml(hintText)}</div>` : "";
    const latency = sr.latency_ms != null ? `${esc(sr.latency_ms)} ms` : "";
    const tokMeta = data.tok_s ? ` · ${Number(data.tok_s).toFixed(1)} tok/s` : "";
    const metaHtml = latency ? `<div class="sq-msg-meta"><span>${latency}${tokMeta}</span></div>` : "";

    msg.innerHTML = `${pillHtml}${bodyHtml}`;

    // Gate solutionText behind an explicit confirmation click if not already confirmed
    if (solutionText) {
      const solWrap = document.createElement("div");
      if (preConfirmed || isSolveMode) {
        solWrap.className = "sq-solution-block";
        solWrap.innerHTML = `<div class="sq-solution-label">${isSolveMode ? "Step-by-Step Solution" : "Step-by-Step Solution (Confirmed)"}</div><div>${formatMathHtml(solutionText)}</div>`;
      } else {
        solWrap.className = "sq-confirm-gate";
        solWrap.innerHTML = `
          <div class="sq-confirm-text"><strong>Rung 4 Locked:</strong> Confirm to reveal full worked solution.</div>
          <div class="sq-confirm-actions">
            <button type="button" class="sq-confirm-yes">Show Solution</button>
          </div>
        `;
        const revealBtn = solWrap.querySelector(".sq-confirm-yes");
        let armed = false;
        revealBtn.onclick = () => {
          if (!armed) {
            armed = true;
            revealBtn.textContent = "Confirm Reveal";
            return;
          }
          solWrap.className = "sq-solution-block";
          solWrap.innerHTML = `<div class="sq-solution-label">Step-by-Step Solution</div><div>${formatMathHtml(solutionText)}</div>`;
        };
      }
      msg.appendChild(solWrap);
    }

    if (metaHtml) {
      msg.insertAdjacentHTML("beforeend", metaHtml);
    }

    stream.appendChild(msg);
    stream.scrollTop = stream.scrollHeight;

    speakIfEnabled(preConfirmed && solutionText ? `${hintText} ${solutionText}`.trim() : hintText);
    resetTurnTimer();
  }

  // --- Text Query Submission ---
  async function submitTextQuery(eventType = "HINT_REQUEST") {
    const inputEl = $("math-input");
    const rawText = inputEl.value.trim();
    if (!rawText && eventType !== "ANSWER_SUBMIT") return;

    const confidence = parseInt($("conf-slider").value || "50", 10);
    const hesitationMs = getHesitationMs();
    const pid = state.activeProblemId === "FREE-MATH" ? "GENERAL-MATH" : state.activeProblemId;

    appendUserBubble(rawText, eventType === "ANSWER_SUBMIT" ? state.selectedOption : null);
    inputEl.value = "";
    inputEl.style.height = "auto";

    const sendBtn = $("btn-send");
    sendBtn.disabled = true;

    try {
      const resp = await fetch("/api/text-query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          problem_id: pid,
          text: rawText,
          event_type: eventType,
          attempt_num: state.attemptNum,
          confidence_pct: confidence,
          selected_option: state.selectedOption,
          session_id: state.sessionId,
          hesitation_ms: hesitationMs,
          mode: state.tutorMode === "solve" ? "solve" : "teach",
          language: state.lang,
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      state.attemptNum += 1;
      updateHud(data);
      if (data.hint_ladder) {
        state.hintLadder = data.hint_ladder;
        if (!state.rung4Unlocked && state.activeRung !== 4) {
          state.activeRung = 3;
        }
        renderHintLadder();
      }
      appendTutorBubble(data);
    } catch (_) {
      appendTutorBubble({
        socratic_response: {
          hint_text: "Unable to reach the SocraticQ backend. Please check the server connection.",
        },
      });
    } finally {
      sendBtn.disabled = false;
    }
  }

  // --- Voice Recording (MediaRecorder + SpeechRecognition) ---
  function startSpeechRecognitionPreview() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    state.liveTranscript = "";
    if (!SR) return;
    try {
      const rec = new SR();
      rec.continuous = true;
      rec.interimResults = true;
      rec.lang = state.lang === "cat" ? "ca-ES" : "en-US";
      rec.onresult = (event) => {
        let transcript = "";
        for (let i = 0; i < event.results.length; i++) {
          transcript += event.results[i][0].transcript + " ";
        }
        state.liveTranscript = transcript.trim();
        if (state.liveTranscript) {
          $("voice-status-text").textContent = `"${state.liveTranscript}"`;
        }
      };
      rec.onerror = () => {};
      rec.start();
      state.recognition = rec;
    } catch (_) {}
  }

  function stopSpeechRecognitionPreview() {
    if (state.recognition) {
      try {
        state.recognition.stop();
      } catch (_) {}
      state.recognition = null;
    }
  }

  async function toggleMicRecording() {
    if (state.isRecording) {
      stopRecording();
      return;
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      $("voice-status-text").textContent = "Microphone unavailable in this browser.";
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      state.mediaStream = stream;
      state.chunks = [];

      const mimeType = MediaRecorder.isTypeSupported("audio/webm")
        ? "audio/webm"
        : MediaRecorder.isTypeSupported("audio/ogg")
          ? "audio/ogg"
          : "";
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) state.chunks.push(e.data);
      };

      recorder.onstop = async () => {
        const blob = new Blob(state.chunks, { type: mimeType || "audio/webm" });
        await uploadVoiceBlob(blob, mimeType || "audio/webm");
      };

      recorder.onerror = () => {
        stopRecording();
        $("voice-status-text").textContent = "Mic error — please retry or type.";
      };

      state.mediaRecorder = recorder;
      state.isRecording = true;
      recorder.start();
      startSpeechRecognitionPreview();

      $("mic-btn").classList.add("recording");
      $("voice-status-text").textContent = "Listening… tap mic again to send";
    } catch (err) {
      if (state.mediaStream) {
        state.mediaStream.getTracks().forEach((t) => t.stop());
        state.mediaStream = null;
      }
      state.isRecording = false;
      $("voice-status-text").textContent = "Mic permission denied (" + (err.message || "error") + ")";
    }
  }

  function stopRecording() {
    state.isRecording = false;
    stopSpeechRecognitionPreview();

    if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
      state.mediaRecorder.stop();
    }
    if (state.mediaStream) {
      state.mediaStream.getTracks().forEach((t) => t.stop());
      state.mediaStream = null;
    }

    $("mic-btn").classList.remove("recording");
    $("voice-status-text").textContent = "Transcribing & thinking…";
  }

  async function uploadVoiceBlob(blob, mimeType) {
    const confidence = parseInt($("conf-slider").value || "50", 10);
    const hesitationMs = getHesitationMs();
    const ext = mimeType.includes("wav") ? "wav" : "webm";
    const pid = state.activeProblemId === "FREE-MATH" ? "GENERAL-MATH" : state.activeProblemId;

    const fd = new FormData();
    fd.append("audio", blob, `utterance.${ext}`);
    fd.append("problem_id", pid);
    fd.append("confidence_pct", String(confidence));
    fd.append("attempt_num", String(state.attemptNum));
    if (state.selectedOption) fd.append("selected_option", state.selectedOption);
    fd.append("session_id", state.sessionId);
    fd.append("hesitation_ms", String(hesitationMs));
    if (state.liveTranscript) fd.append("fallback_transcript", state.liveTranscript);
    fd.append("mode", state.tutorMode === "solve" ? "solve" : "teach");
    fd.append("language", state.lang);

    try {
      const resp = await fetch("/api/voice-query", { method: "POST", body: fd });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      state.attemptNum += 1;
      updateHud(data);
      if (data.hint_ladder) {
        state.hintLadder = data.hint_ladder;
        renderHintLadder();
      }
      appendUserBubble(data.transcription || state.liveTranscript || "[Voice message]", state.selectedOption);
      appendTutorBubble(data);
      $("voice-status-text").textContent = "Tap mic or type to begin";
    } catch (_) {
      $("voice-status-text").textContent = "Voice upload failed — check server.";
    }
  }

  // --- Explorer / Test Workbench + 8x13 LED Matrix Simulator ---
  const GLYPHS_5X7 = {
    A: ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    B: ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    C: ["01111", "10000", "10000", "10000", "10000", "10000", "01111"],
    D: ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    E: ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "?": ["01110", "10001", "00010", "00100", "00100", "00000", "00100"],
    "✓": ["00001", "00010", "00100", "10100", "01000", "00000", "00000"],
    "✗": ["10001", "01010", "00100", "01010", "10001", "00000", "00000"],
  };

  function initLedMatrixGrid() {
    const grid = $("led-matrix-grid");
    if (!grid || grid.children.length === 104) return;
    grid.innerHTML = "";
    for (let i = 0; i < 8 * 13; i++) {
      const cell = document.createElement("div");
      cell.className = "sq-led-cell";
      grid.appendChild(cell);
    }
  }

  function renderLedMatrix(led = {}) {
    initLedMatrixGrid();
    const grid = $("led-matrix-grid");
    if (!grid) return;
    const cells = grid.children;
    for (let i = 0; i < cells.length; i++) {
      cells[i].className = "sq-led-cell";
    }

    const ledState = led.led_state || "IDLE";
    const isCorrect = led.is_correct;
    const opt = (led.option || "?").toUpperCase();
    const colorClass =
      isCorrect === true || ledState === "SOLVED"
        ? "on-green"
        : isCorrect === false
          ? "on-rose"
          : ledState === "SOCRATIC_NUDGE"
            ? "on-amber"
            : "on-aqua";

    const glyphKey = isCorrect === true ? "✓" : isCorrect === false ? "✗" : (GLYPHS_5X7[opt] ? opt : "?");
    const glyph = GLYPHS_5X7[glyphKey] || GLYPHS_5X7["?"];

    // Draw 5x7 primary glyph on cols 0..4, rows 0..6 (mirrors hardware/bridge/unoq_app.py)
    for (let r = 0; r < 7; r++) {
      for (let c = 0; c < 5; c++) {
        if (glyph[r][c] === "1") {
          cells[r * 13 + c].classList.add(colorClass);
        }
      }
    }

    // Draw option letter on cols 6..10 when showing ✓ / ✗
    if ((isCorrect === true || isCorrect === false) && GLYPHS_5X7[opt]) {
      const g2 = GLYPHS_5X7[opt];
      for (let r = 0; r < 7; r++) {
        for (let c = 0; c < 5; c++) {
          if (g2[r][c] === "1") {
            cells[r * 13 + (c + 6)].classList.add(colorClass);
          }
        }
      }
    } else {
      // Prefill progress bar on cols 6..10 (row 3)
      const prefill = Number(led.prefill_pct || 0);
      const litCols = Math.round((prefill / 100) * 5);
      for (let c = 0; c < litCols; c++) {
        cells[3 * 13 + (6 + c)].classList.add("on-aqua");
      }
    }

    // Draw Rung 1..4 dots on col 12 bottom-to-top (rows 6, 4, 2, 0)
    const activeRungs = led.active_rungs || [];
    const rungRows = [6, 4, 2, 0];
    [1, 2, 3, 4].forEach((rung, idx) => {
      if (activeRungs.includes(rung)) {
        cells[rungRows[idx] * 13 + 12].classList.add("on-amber");
      }
    });

    // Level progress bar across bottom row 7 (1..3 tiers mapped across cols 0..12)
    const lvl = Math.max(1, Math.min(3, Number(led.difficulty_level || 1)));
    const barCols = lvl * 4;
    for (let c = 0; c < barCols && c < 13; c++) {
      cells[7 * 13 + c].classList.add("on-aqua");
    }

    const lbl = $("exp-led-label");
    if (lbl) {
      lbl.textContent = `${ledState} · ${glyphKey}${opt !== glyphKey && GLYPHS_5X7[opt] ? " " + opt : ""} · Tier ${lvl}`;
    }
  }

  async function loadExplorerProblems() {
    try {
      const resp = await fetch(`/api/explorer/problems?lang=${encodeURIComponent(state.expLang)}`);
      if (!resp.ok) return;
      state.expProblems = await resp.json();
      const sel = $("exp-problem-select");
      if (sel) {
        sel.innerHTML = "";
        state.expProblems.forEach((p, idx) => {
          const opt = document.createElement("option");
          opt.value = p.problem_id;
          const catTag = p.has_cat ? " [CAT/ENG]" : "";
          opt.textContent = `${idx + 1}. ${p.problem_id} · ${p.category} T${p.difficulty_level}${catTag}`;
          sel.appendChild(opt);
        });
        if (state.expProblems[state.expIndex]) {
          sel.value = state.expProblems[state.expIndex].problem_id;
        }
      }
      if (state.expProblems.length > 0) {
        await runExplorerInspect(false);
      }
    } catch (_) {}
  }

  function renderExplorerInspect(data) {
    state.expLastInspect = data;
    updateHud(data);

    const probBadge = $("exp-prob-badge");
    const bridgeBadge = $("exp-bridge-badge");
    if (probBadge) {
      probBadge.textContent = `${data.problem_id} · ${data.category} · Tier ${data.difficulty_level} · Key: ${data.correct_option || "?"}`;
    }
    if (bridgeBadge) {
      bridgeBadge.textContent = data.bridged
        ? `Lang: ${data.language.toUpperCase()} (CAT→ENG bridge)`
        : `Lang: ${(data.language || "eng").toUpperCase()}`;
    }

    renderMathInText($("exp-prob-stem"), data.statement || "");

    // Options A-E with answer key & selected highlights
    const optsWrap = $("exp-options-list");
    if (optsWrap) {
      optsWrap.innerHTML = "";
      Object.entries(data.options || {}).forEach(([k, val]) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "sq-exp-opt";
        if (state.expSelectedOption === k) btn.classList.add("selected");
        if (data.correct_option === k) btn.classList.add("correct");
        else if (state.expSelectedOption === k && data.correct_option && state.expSelectedOption !== data.correct_option) {
          btn.classList.add("wrong");
        }
        const tag = data.correct_option === k ? "Answer Key" : (data.predicted_option === k ? "Model Pick" : "");
        btn.innerHTML = `<span><strong>${esc(k)})</strong> ${formatMathHtml(val)}</span><span class="sq-problem-meta">${esc(tag)}</span>`;
        btn.onclick = () => {
          state.expSelectedOption = k;
          runExplorerInspect(false);
        };
        optsWrap.appendChild(btn);
      });
    }

    // Metrics & Leak-Check
    $("exp-m-latency").textContent = `${data.latency_ms ?? 0} ms`;
    $("exp-m-promptn").textContent = String(data.prompt_n ?? 0);
    $("exp-m-toks").textContent = `${Number(data.tok_s || 0).toFixed(1)} tok/s`;
    const leakEl = $("exp-m-leak");
    const lc = data.leak_check || { safe: true, leaks: 0 };
    leakEl.textContent = lc.safe ? `SAFE (${lc.leaks || 0} leaks)` : `LEAK (${lc.leaks})`;
    leakEl.className = `sq-mono sq-exp-mval ${lc.safe ? "sq-safe-text" : "sq-leak-text"}`;

    // Raw vs Calibrated Scores + Prior
    $("exp-prior-key").textContent = data.prior_key || "";
    const barsEl = $("exp-scores-bars");
    if (barsEl) {
      barsEl.innerHTML = "";
      ["A", "B", "C", "D", "E"].forEach((k) => {
        const raw = Number((data.raw_scores || {})[k] || 0);
        const cal = Number((data.calibrated_scores || {})[k] || 0);
        const pri = Number((data.prior_probs || {})[k] || 0);
        const row = document.createElement("div");
        row.className = "sq-score-row";
        row.innerHTML = `
          <span class="sq-mono"><strong>${k}</strong></span>
          <div class="sq-bar-track" title="Raw ${(raw * 100).toFixed(1)}% · Calibrated ${(cal * 100).toFixed(1)}%">
            <div class="sq-bar-raw" style="width:${Math.min(100, Math.round(raw * 100))}%"></div>
            <div class="sq-bar-cal" style="width:${Math.min(100, Math.round(cal * 100))}%"></div>
          </div>
          <span class="sq-mono">${(raw * 100).toFixed(0)}% → ${(cal * 100).toFixed(0)}% (p${(pri * 100).toFixed(0)})</span>
        `;
        barsEl.appendChild(row);
      });
    }

    // Option-value feature breakdown
    const featWrap = $("exp-features-wrap");
    if (featWrap && data.feature_breakdown) {
      const rowsHtml = Object.entries(data.feature_breakdown)
        .map(([k, f]) => {
          return `<tr>
            <td class="sq-mono"><strong>${esc(k)}</strong></td>
            <td>${formatMathHtml(f.value ?? "")}</td>
            <td class="sq-mono">${f.is_median ? "yes" : "—"}</td>
            <td class="sq-mono">${f.appears_in_stem ? "yes" : "—"}</td>
            <td class="sq-mono">${(Number(f.prior || 0) * 100).toFixed(0)}%</td>
          </tr>`;
        })
        .join("");
      featWrap.innerHTML = `
        <table class="sq-table">
          <thead><tr><th>Opt</th><th>Value</th><th>Median</th><th>In Stem</th><th>Prior</th></tr></thead>
          <tbody>${rowsHtml}</tbody>
        </table>
      `;
    }

    // Rung 1-4 Outputs
    const rungsWrap = $("exp-rungs-out");
    if (rungsWrap && data.hint_ladder) {
      rungsWrap.innerHTML = "";
      const hl = data.hint_ladder;
      if ($("exp-toggle-r1").checked && hl.rung1) {
        const d = document.createElement("div");
        d.className = "sq-exp-rung-item";
        d.innerHTML = `<strong>R1 Easier Neighbour:</strong> ${formatMathHtml(hl.rung1.hint_text || hl.rung1.statement || hl.rung1.intuition_hook || "—")}`;
        rungsWrap.appendChild(d);
      }
      if ($("exp-toggle-r2").checked && hl.rung2) {
        const d = document.createElement("div");
        d.className = "sq-exp-rung-item";
        d.innerHTML = `<strong>R2 Topic Vote:</strong> ${formatMathHtml(hl.rung2.hint_text || ((hl.rung2.topic || "") + " · " + (hl.rung2.strategy || "")))}`;
        rungsWrap.appendChild(d);
      }
      if ($("exp-toggle-r3").checked && hl.rung3) {
        const d = document.createElement("div");
        d.className = "sq-exp-rung-item";
        d.innerHTML = `<strong>R3 Socratic Hint:</strong> ${formatMathHtml(hl.rung3.hint_text || hl.rung3.text || "Press M (Ask Model) to generate live hint.")}`;
        rungsWrap.appendChild(d);
      }
      if ($("exp-toggle-r4").checked && state.expR4Confirmed && hl.rung4) {
        const d = document.createElement("div");
        d.className = "sq-exp-rung-item";
        d.innerHTML = `<strong>R4 Full Solution (Confirmed):</strong><div style="white-space:pre-wrap;margin-top:3px;">${formatMathHtml(hl.rung4.solution_text || hl.rung4.hint_text || hl.rung4.text || "Press M (Ask Model) with R4 confirmed to compute full solution.")}</div>`;
        rungsWrap.appendChild(d);
      }
    }

    $("exp-prompt-pre").textContent = data.prompt || "";
    renderLedMatrix(data.led_matrix || {});
  }

  async function runExplorerInspect(askModel = false) {
    const prob = state.expProblems[state.expIndex];
    if (!prob) return;

    const rungs = [];
    if ($("exp-toggle-r1").checked) rungs.push(1);
    if ($("exp-toggle-r2").checked) rungs.push(2);
    if ($("exp-toggle-r3").checked) rungs.push(3);
    if ($("exp-toggle-r4").checked && state.expR4Confirmed) rungs.push(4);

    if (askModel) {
      $("exp-led-label").textContent = "THINKING…";
    }

    try {
      const resp = await fetch("/api/explorer/inspect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          problem_id: prob.problem_id,
          language: state.expLang,
          use_calibration: $("exp-toggle-cal").checked,
          use_prior: $("exp-toggle-prior").checked,
          rungs,
          ask_model: askModel,
          selected_option: state.expSelectedOption,
        }),
      });
      if (!resp.ok) return;
      const data = await resp.json();
      renderExplorerInspect(data);
    } catch (_) {}
  }

  function stepExplorerProblem(delta) {
    if (state.expProblems.length === 0) return;
    state.expIndex = (state.expIndex + delta + state.expProblems.length) % state.expProblems.length;
    state.expSelectedOption = null;
    state.expR4Confirmed = false;
    $("exp-toggle-r4").checked = false;
    $("exp-r4-gate").hidden = true;
    $("exp-problem-select").value = state.expProblems[state.expIndex].problem_id;
    runExplorerInspect(false);
  }

  function randomExplorerProblem() {
    if (state.expProblems.length <= 1) return;
    let next = state.expIndex;
    while (next === state.expIndex) {
      next = Math.floor(Math.random() * state.expProblems.length);
    }
    state.expIndex = next;
    state.expSelectedOption = null;
    state.expR4Confirmed = false;
    $("exp-toggle-r4").checked = false;
    $("exp-r4-gate").hidden = true;
    $("exp-problem-select").value = state.expProblems[state.expIndex].problem_id;
    runExplorerInspect(false);
  }

  // --- System Status (Top-Bar HUD) ---
  async function refreshSystemStatus() {
    try {
      const resp = await fetch("/api/system-status");
      if (!resp.ok) throw new Error("status");
      const d = await resp.json();
      updateHud(d);

      if (d.telemetry_events_total !== undefined) $("stat-telemetry").textContent = String(d.telemetry_events_total);
      if (d.active_interventions !== undefined) $("stat-active").textContent = String(d.active_interventions);
      if (d.agent_insights_total !== undefined) $("stat-insights").textContent = String(d.agent_insights_total);
      if (d.agent_traces_total !== undefined) $("stat-traces").textContent = String(d.agent_traces_total);
    } catch (_) {
      updateHud({ offline_mode: true });
    }
  }

  // --- Slide-Over Cortex Drawer & SSE ---
  function setDrawerOpen(open) {
    $("cortex-drawer").classList.toggle("open", open);
    $("cortex-backdrop").classList.toggle("open", open);
    $("cortex-drawer").setAttribute("aria-hidden", String(!open));
    $("btn-cortex-toggle").setAttribute("aria-expanded", String(open));
    $("btn-cortex-toggle").classList.toggle("active", open);
  }

  function renderInsightCard(ins) {
    const card = document.createElement("div");
    card.className = "sq-insight-card";
    card.id = "ins-" + ins.insight_id;
    card.innerHTML = `
      <div style="display:flex;justify-content:space-between;gap:8px;color:var(--aqua);font-weight:600;">
        <span>${esc(ins.insight_type)} · ${esc(ins.target_category)}</span>
        <span class="sq-mono">Sev ${Number(ins.severity_score || 0).toFixed(2)}</span>
      </div>
      <div style="color:var(--text-secondary);">${esc(ins.evidence_summary)}</div>
      <div style="color:var(--mint);">Nudge: "${esc(ins.recommended_nudge)}"</div>
      <button type="button" class="sq-apply-btn" data-apply-id="${esc(ins.insight_id)}">Mark Applied</button>
    `;
    const btn = card.querySelector("[data-apply-id]");
    if (btn) {
      btn.onclick = async () => {
        await fetch(`/interventions/${encodeURIComponent(ins.insight_id)}/apply`, { method: "POST" });
      };
    }
    return card;
  }

  function prependTelemetryRow(ev) {
    const tbody = $("telemetry-tbody");
    if (!tbody) return;
    const row = document.createElement("tr");
    const statusText = ev.is_correct === true ? "SOLVED" : ev.is_correct === false ? "WRONG" : "HINT";
    row.innerHTML = `
      <td class="sq-mono">${esc(ev.problem_id)}</td>
      <td>${esc(ev.event_type)}</td>
      <td class="sq-mono">${esc(ev.confidence_pct)}%</td>
      <td>${statusText}</td>
    `;
    tbody.insertBefore(row, tbody.firstChild);
    while (tbody.children.length > 20) tbody.removeChild(tbody.lastChild);
  }

  async function hydrateCortex() {
    try {
      const resp = await fetch("/api/cortex-snapshot");
      if (!resp.ok) return;
      const snap = await resp.json();

      if (snap.stats) {
        $("stat-telemetry").textContent = String(snap.stats.telemetry_events_total ?? 0);
        $("stat-active").textContent = String(snap.stats.active_interventions ?? 0);
        $("stat-insights").textContent = String(snap.stats.agent_insights_total ?? 0);
        $("stat-traces").textContent = String(snap.stats.agent_traces_total ?? 0);
      }

      const list = $("insights-list");
      list.innerHTML = "";
      const active = snap.active_insights || [];
      $("cortex-alert-dot").classList.toggle("visible", active.length > 0);
      if (active.length === 0) {
        list.innerHTML = '<div id="no-insights-msg" class="sq-problem-meta">No active bottlenecks detected yet.</div>';
      } else {
        active.forEach((ins) => list.appendChild(renderInsightCard(ins)));
      }

      const tbody = $("telemetry-tbody");
      tbody.innerHTML = "";
      (snap.recent_events || []).slice().reverse().forEach((ev) => prependTelemetryRow(ev));
    } catch (_) {}
  }

  function connectSSE() {
    const sseText = $("sse-status-text");
    const stream = new EventSource("/stream");

    stream.onopen = () => {
      sseText.textContent = "Live stream connected";
    };
    stream.onerror = () => {
      sseText.textContent = stream.readyState === EventSource.CLOSED ? "Stream offline" : "Reconnecting…";
    };
    stream.addEventListener("telemetry", (e) => {
      try {
        const ev = JSON.parse(e.data);
        $("stat-telemetry").textContent = String(parseInt($("stat-telemetry").textContent || "0", 10) + 1);
        prependTelemetryRow(ev);
      } catch (_) {}
    });
    stream.addEventListener("intervention", (e) => {
      try {
        const ins = JSON.parse(e.data);
        $("stat-active").textContent = String(parseInt($("stat-active").textContent || "0", 10) + 1);
        $("stat-insights").textContent = String(parseInt($("stat-insights").textContent || "0", 10) + 1);
        $("cortex-alert-dot").classList.add("visible");
        const noMsg = $("no-insights-msg");
        if (noMsg) noMsg.remove();
        $("insights-list").insertBefore(renderInsightCard(ins), $("insights-list").firstChild);
      } catch (_) {}
    });
    stream.addEventListener("intervention_applied", (e) => {
      try {
        const data = JSON.parse(e.data);
        const card = document.getElementById("ins-" + data.insight_id);
        if (card) card.style.opacity = "0.45";
        const cur = parseInt($("stat-active").textContent || "0", 10);
        if (cur > 0) $("stat-active").textContent = String(cur - 1);
      } catch (_) {}
    });
  }

  // --- Init ---
  function init() {
    $("mode-teach").onclick = () => setTutorMode("teach");
    $("mode-solve").onclick = () => setTutorMode("solve");
    $("mode-explorer").onclick = () => setTutorMode("explorer");

    $("problem-select").addEventListener("change", (e) => {
      selectProblemById(e.target.value);
    });

    // 4-Rung Hint Ladder interactions
    [1, 2, 3, 4].forEach((r) => {
      const btn = $(`rung-btn-${r}`);
      if (!btn) return;
      btn.onclick = () => {
        if (r === 3 && state.activeRung === 3 && !(state.hintLadder?.rung3?.hint_text)) {
          requestRung3Hint();
          return;
        }
        state.activeRung = r;
        renderHintLadder();
        if (r === 3 && !(state.hintLadder?.rung3?.hint_text) && state.activeProblemId !== "FREE-MATH") {
          requestRung3Hint();
        }
      };
    });
    $("btn-rung4-confirm").onclick = () => unlockRung4Solution();
    $("btn-rung4-cancel").onclick = () => {
      state.activeRung = 3;
      renderHintLadder();
    };

    // Explorer controls
    $("exp-problem-select").addEventListener("change", (e) => {
      const idx = state.expProblems.findIndex((p) => p.problem_id === e.target.value);
      if (idx >= 0) {
        state.expIndex = idx;
        state.expSelectedOption = null;
        state.expR4Confirmed = false;
        $("exp-toggle-r4").checked = false;
        $("exp-r4-gate").hidden = true;
        runExplorerInspect(false);
      }
    });
    $("exp-btn-next").onclick = () => stepExplorerProblem(1);
    $("exp-btn-random").onclick = () => randomExplorerProblem();
    $("exp-btn-ask").onclick = () => runExplorerInspect(true);

    $("exp-lang-eng").onclick = () => {
      state.expLang = "eng";
      $("exp-lang-eng").classList.add("active");
      $("exp-lang-cat").classList.remove("active");
      loadExplorerProblems();
    };
    $("exp-lang-cat").onclick = () => {
      state.expLang = "cat";
      $("exp-lang-cat").classList.add("active");
      $("exp-lang-eng").classList.remove("active");
      loadExplorerProblems();
    };

    ["exp-toggle-cal", "exp-toggle-prior", "exp-toggle-r1", "exp-toggle-r2", "exp-toggle-r3"].forEach((id) => {
      $(id).addEventListener("change", () => runExplorerInspect(false));
    });
    $("exp-toggle-r4").addEventListener("change", (e) => {
      if (e.target.checked && !state.expR4Confirmed) {
        $("exp-r4-gate").hidden = false;
      } else if (!e.target.checked) {
        state.expR4Confirmed = false;
        $("exp-r4-gate").hidden = true;
        runExplorerInspect(false);
      }
    });
    $("exp-btn-r4-confirm").onclick = () => {
      state.expR4Confirmed = true;
      $("exp-toggle-r4").checked = true;
      $("exp-r4-gate").hidden = true;
      runExplorerInspect(state.expLastInspect ? false : true);
    };
    $("exp-btn-r4-cancel").onclick = () => {
      state.expR4Confirmed = false;
      $("exp-toggle-r4").checked = false;
      $("exp-r4-gate").hidden = true;
    };

    // Explorer keyboard shortcuts (A-E, M, N, R)
    document.addEventListener("keydown", (e) => {
      if (state.tutorMode !== "explorer") return;
      const tag = (e.target && e.target.tagName) || "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      const key = e.key.toUpperCase();
      if (["A", "B", "C", "D", "E"].includes(key)) {
        e.preventDefault();
        state.expSelectedOption = key;
        runExplorerInspect(false);
      } else if (key === "M") {
        e.preventDefault();
        runExplorerInspect(true);
      } else if (key === "N") {
        e.preventDefault();
        stepExplorerProblem(1);
      } else if (key === "R") {
        e.preventDefault();
        randomExplorerProblem();
      }
    });

    $("mic-btn").onclick = toggleMicRecording;
    $("btn-send").onclick = () => submitTextQuery("HINT_REQUEST");
    $("btn-submit-option").onclick = () => submitTextQuery("ANSWER_SUBMIT");

    $("conf-slider").addEventListener("input", (e) => {
      $("conf-val").textContent = `${e.target.value}%`;
    });

    const inputEl = $("math-input");
    inputEl.addEventListener("input", () => {
      inputEl.style.height = "auto";
      inputEl.style.height = Math.min(inputEl.scrollHeight, 110) + "px";
    });
    inputEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        submitTextQuery("HINT_REQUEST");
      }
    });

    $("btn-cortex-toggle").onclick = () => {
      const isOpen = $("cortex-drawer").classList.contains("open");
      setDrawerOpen(!isOpen);
    };
    $("btn-cortex-close").onclick = () => setDrawerOpen(false);
    $("cortex-backdrop").onclick = () => setDrawerOpen(false);

    initLedMatrixGrid();
    loadProblems();
    hydrateCortex();
    refreshSystemStatus();
    connectSSE();
    setInterval(refreshSystemStatus, 6000);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
