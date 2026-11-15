/* SocraticQ chat — vanilla JS, offline, talks to POST /api/text-query (FastAPI or chat_server.py). */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, html) => { const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; };
  const icon = (name) => `<svg><use href="#i-${name}"/></svg>`;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const uid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  const store = { get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } }, set: (k, v) => localStorage.setItem(k, JSON.stringify(v)) };

  const I18N = {
    eng: {
      newChat: "New chat", searchChats: "Search chats", library: "Problem library", workbench: "Workbench",
      recent: "Recent", problemBtn: "Problem", searchProblems: "Search problems", allCats: "All",
      noChats: "Your conversations will appear here", noMatchChats: "No matching chats", noMatchProbs: "No problems match.",
      tapOpt: "tap an option to answer", thinking: "Thinking", solving: "Solving", onDevice: "on-device…",
      finalAnswer: "Final answer:", answerPrefix: "My answer is",
      ttsOn: "Voice readback on", ttsOff: "Voice readback off",
      modes: {
        teach: {
          name: "Learn", tag: "Socratic", placeholder: "Ask anything — I'll help you think it through…",
          title: "What do you want to figure out?", sub: "I'll guide you with questions so you discover the answer yourself.",
          disclaimer: "Learn mode never reveals the final answer · runs 100% offline on-device",
          suggestions: [
            { t: "Why is odd + odd always even?", s: "Build intuition for parity", q: "Why is the sum of two odd numbers always even?" },
            { t: "Counting handshakes", s: "10 people all shake hands once", q: "10 people each shake hands with everyone else once. How should I think about counting the handshakes?" },
            { t: "Give me a Cangur problem", s: "Random competition problem", random: "Help me get started on this problem." },
            { t: "Fractions keep tripping me up", s: "Find where it goes wrong", q: "I keep getting fraction problems wrong, especially adding fractions with different denominators." },
          ],
          followups: [
            { t: "Another hint", q: "Can you give me another hint?" },
            { t: "I'm stuck", q: "I'm still stuck. Can you break it into a smaller step?" },
            { t: "Check my reasoning", fill: "Here's my reasoning so far: " },
            { t: "Show the solution", q: "Please show me the full solution.", mode: "solve" },
          ],
        },
        solve: {
          name: "Solve", tag: "Solver", placeholder: "Paste a problem to solve step by step…",
          title: "What should we solve?", sub: "I'll work it out step by step and give you the final answer.",
          disclaimer: "Solve mode shows full solutions · switch to Learn to practise on your own",
          suggestions: [
            { t: "Solve 3x + 7 = 22", s: "Linear equation", q: "Solve 3x + 7 = 22." },
            { t: "Hexagon angles", s: "Sum of interior angles", q: "What is the sum of the interior angles of a hexagon?" },
            { t: "Solve a Cangur problem", s: "Random competition problem", random: "Solve this problem step by step." },
            { t: "Decagon diagonals", s: "How many are there?", q: "How many diagonals does a decagon have?" },
          ],
          followups: [
            { t: "Explain more simply", q: "Can you explain that more simply?" },
            { t: "Why does that work?", q: "Why does that method work?" },
            { t: "Teach me instead", q: "Guide me through it myself instead, one hint at a time.", mode: "teach" },
          ],
        },
      },
    },
    cat: {
      newChat: "Nou xat", searchChats: "Cerca xats", library: "Banc de problemes", workbench: "Banc de proves",
      recent: "Recents", problemBtn: "Problema", searchProblems: "Cerca problemes", allCats: "Tots",
      noChats: "Les teves converses apareixeran aquí", noMatchChats: "Cap xat coincideix", noMatchProbs: "Cap problema coincideix.",
      tapOpt: "toca una opció per respondre", thinking: "Pensant", solving: "Resolent", onDevice: "al dispositiu…",
      finalAnswer: "Resposta final:", answerPrefix: "La meva resposta és",
      ttsOn: "Lectura en veu alta activada", ttsOff: "Lectura en veu alta desactivada",
      modes: {
        teach: {
          name: "Ensenyar", tag: "Socràtic", placeholder: "Pregunta el que vulguis — t'ajudaré a pensar-ho…",
          title: "Què vols descobrir?", sub: "Et guiaré amb preguntes perquè trobis la resposta tu mateix.",
          disclaimer: "El mode Ensenyar mai revela la resposta final · funciona 100% sense connexió",
          suggestions: [
            { t: "Per què senar + senar sempre és parell?", s: "Intuïció de paritat", q: "Per què la suma de dos nombres senars sempre és un nombre parell?" },
            { t: "Comptar encaixades de mans", s: "10 persones es donen la mà un cop", q: "10 persones es donen la mà entre elles una vegada. Com hauria de pensar per comptar les encaixades?" },
            { t: "Dona'm un problema Cangur", s: "Problema aleatori de concurs", random: "Ajuda'm a començar aquest problema." },
            { t: "Les fraccions em costen", s: "Troba on falla el pas", q: "Sempre m'equivoco amb les fraccions, sobretot sumant fraccions amb denominadors diferents." },
          ],
          followups: [
            { t: "Una altra pista", q: "Em pots donar una altra pista?" },
            { t: "Estic encallat", q: "Encara estic encallat. Ho pots dividir en un pas més petit?" },
            { t: "Revisa el meu raonament", fill: "Aquest és el meu raonament fins ara: " },
            { t: "Mostra la solució", q: "Si us plau, mostra'm la solució completa.", mode: "solve" },
          ],
        },
        solve: {
          name: "Resoldre", tag: "Resolutor", placeholder: "Enganxa un problema per resoldre'l pas a pas…",
          title: "Què hem de resoldre?", sub: "Ho resoldré pas a pas i et donaré la resposta final.",
          disclaimer: "El mode Resoldre mostra la solució completa · canvia a Ensenyar per practicar",
          suggestions: [
            { t: "Resol 3x + 7 = 22", s: "Equació lineal", q: "Resol 3x + 7 = 22." },
            { t: "Angles d'un hexàgon", s: "Suma dels angles interiors", q: "Quina és la suma dels angles interiors d'un hexàgon?" },
            { t: "Resol un problema Cangur", s: "Problema aleatori de concurs", random: "Resol aquest problema pas a pas." },
            { t: "Diagonals d'un decàgon", s: "Quantes n'hi ha?", q: "Quantes diagonals té un decàgon?" },
          ],
          followups: [
            { t: "Explica-ho més senzill", q: "Ho pots explicar d'una manera més senzilla?" },
            { t: "Per què funciona això?", q: "Per què funciona aquest mètode?" },
            { t: "Ensenya'm en comptes de resoldre", q: "Guia'm pas a pas amb pistes en comptes de donar-me la solució.", mode: "teach" },
          ],
        },
      },
    },
  };
  const T = () => I18N[S.lang] || I18N.eng;
  const MODES = new Proxy({}, { get: (_, k) => T().modes[k] });

  const S = {
    chats: store.get("sq.chats", []),
    currentId: null,
    mode: store.get("sq.mode", "teach"),
    lang: store.get("sq.lang", "eng") === "cat" ? "cat" : "eng",
    tts: Boolean(store.get("sq.tts", false)),
    problems: [],
    busy: false,
    ctrl: null,
    reveal: null,
    libCat: "All",
  };
  const cur = () => S.chats.find((c) => c.id === S.currentId) || null;
  const save = () => store.set("sq.chats", S.chats.filter((c) => c.messages.length).slice(0, 100));

  /* ---------------- Offline Math/LaTeX-to-HTML ---------------- */
  const TEX_SYM = {
    "\\equiv": "≡", "\\times": "×", "\\cdot": "·", "\\div": "÷",
    "\\leq": "≤", "\\le": "≤", "\\geq": "≥", "\\ge": "≥",
    "\\neq": "≠", "\\ne": "≠", "\\approx": "≈", "\\pm": "±", "\\mp": "∓",
    "\\pi": "π", "\\theta": "θ", "\\alpha": "α", "\\beta": "β",
    "\\gamma": "γ", "\\delta": "δ", "\\Delta": "Δ", "\\infty": "∞",
    "\\rightarrow": "→", "\\to": "→", "\\Rightarrow": "⇒", "\\leftarrow": "←",
    "\\degree": "°", "\\circ": "°", "\\ldots": "…", "\\dots": "…", "\\cdots": "⋯",
  };
  function renderTexExpr(escapedTex) {
    let out = escapedTex.trim();
    out = out.replace(/\\(?:text|mathrm|mathbf|mathit)\{([^{}]*)\}/g, "$1");
    out = out.replace(/\\pmod\{([^{}]*)\}/g, " (mod $1)").replace(/\\bmod\b/g, " mod ");
    for (let i = 0; i < 2; i++) {
      out = out.replace(/\\frac\{([^{}]*)\}\{([^{}]*)\}/g, '<span class="math-frac"><span class="math-num">$1</span><span class="math-den">$2</span></span>');
    }
    out = out.replace(/\\sqrt\{([^{}]*)\}/g, '√<span class="math-sqrt">$1</span>');
    Object.entries(TEX_SYM).forEach(([k, v]) => { out = out.split(k).join(v); });
    out = out.replace(/\^\{([^{}]+)\}/g, "<sup>$1</sup>").replace(/_\{([^{}]+)\}/g, "<sub>$1</sub>");
    out = out.replace(/\^([A-Za-z0-9°+\-])/g, "<sup>$1</sup>").replace(/_([A-Za-z0-9])/g, "<sub>$1</sub>");
    return out;
  }
  function formatMathHtml(rawText) {
    let text = esc(rawText);
    const mathTokens = [];
    const stash = (html) => { mathTokens.push(html); return `\u0001M${mathTokens.length - 1}\u0001`; };
    text = text
      .replace(/\$\$([\s\S]+?)\$\$/g, (_, m) => stash(`<span class="math-display">${renderTexExpr(m)}</span>`))
      .replace(/\\\[([\s\S]+?)\\\]/g, (_, m) => stash(`<span class="math-display">${renderTexExpr(m)}</span>`))
      .replace(/\\\(([\s\S]+?)\\\)/g, (_, m) => stash(`<span class="math">${renderTexExpr(m)}</span>`))
      .replace(/\\\$([^$\n]+?)\\\$/g, (_, m) => stash(`<span class="math">${renderTexExpr(m)}</span>`))
      .replace(/\$([^$\n]+?)\$/g, (_, m) => stash(`<span class="math">${renderTexExpr(m)}</span>`));
    // Also clean bare LaTeX commands outside $...$
    text = text
      .replace(/\\pmod\{([^{}]*)\}/g, " (mod $1)").replace(/\\bmod\b/g, " mod ")
      .replace(/\\frac\{([^{}]*)\}\{([^{}]*)\}/g, '<span class="math-frac"><span class="math-num">$1</span><span class="math-den">$2</span></span>')
      .replace(/\\sqrt\{([^{}]*)\}/g, '√<span class="math-sqrt">$1</span>');
    Object.entries(TEX_SYM).forEach(([k, v]) => { text = text.split(k).join(v); });
    text = text.replace(/([A-Za-z0-9)])\^\{([^{}]+)\}/g, "$1<sup>$2</sup>").replace(/([A-Za-z0-9)])\^([0-9A-Za-z]+)/g, "$1<sup>$2</sup>");
    text = text.replace(/([A-Za-z0-9)])_\{([^{}]+)\}/g, "$1<sub>$2</sub>");
    return text.replace(/\u0001M(\d+)\u0001/g, (_, i) => mathTokens[+i] || "");
  }

  /* ---------------- Markdown-lite ---------------- */
  function md(src) {
    const blocks = [];
    const mathTokens = [];
    let s = esc(src).replace(/```(\w*)\n?([\s\S]*?)(```|$)/g, (_, _l, code) => { blocks.push(code); return `\u0000${blocks.length - 1}\u0000`; });
    const stashM = (html) => { mathTokens.push(html); return `\u0001M${mathTokens.length - 1}\u0001`; };
    s = s
      .replace(/\$\$([\s\S]+?)\$\$/g, (_, m) => stashM(`<span class="math-display">${renderTexExpr(m)}</span>`))
      .replace(/\\\[([\s\S]+?)\\\]/g, (_, m) => stashM(`<span class="math-display">${renderTexExpr(m)}</span>`))
      .replace(/\\\(([\s\S]+?)\\\)/g, (_, m) => stashM(`<span class="math">${renderTexExpr(m)}</span>`))
      .replace(/\\\$([^$\n]+?)\\\$/g, (_, m) => stashM(`<span class="math">${renderTexExpr(m)}</span>`))
      .replace(/\$([^$\n]+?)\$/g, (_, m) => stashM(`<span class="math">${renderTexExpr(m)}</span>`));
    const inline = (t) => {
      let r = t
        .replace(/^(Step \d+[^:]{0,30}:|Final Answer:)/i, "<strong>$1</strong>")
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
      r = r
        .replace(/\\pmod\{([^{}]*)\}/g, " (mod $1)").replace(/\\bmod\b/g, " mod ")
        .replace(/\\frac\{([^{}]*)\}\{([^{}]*)\}/g, '<span class="math-frac"><span class="math-num">$1</span><span class="math-den">$2</span></span>')
        .replace(/\\sqrt\{([^{}]*)\}/g, '√<span class="math-sqrt">$1</span>');
      Object.entries(TEX_SYM).forEach(([k, v]) => { r = r.split(k).join(v); });
      r = r.replace(/([A-Za-z0-9)])\^\{([^{}]+)\}/g, "$1<sup>$2</sup>").replace(/([A-Za-z0-9)])\^([0-9]+)/g, "$1<sup>$2</sup>");
      r = r.replace(/([A-Za-z0-9)])_\{([^{}]+)\}/g, "$1<sub>$2</sub>");
      return r.replace(/\u0001M(\d+)\u0001/g, (_, i) => mathTokens[+i] || "");
    };
    const out = [];
    let list = null;
    const flush = () => { if (list) { out.push(`<${list.t}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.t}>`); list = null; } };
    for (const para of s.split(/\n{2,}/)) {
      for (const line of para.split("\n")) {
        const ul = line.match(/^\s*[-*•]\s+(.*)/), ol = line.match(/^\s*(?:\d+[.)]|Step \d+:)\s+(.*)/i);
        if (ul || ol) {
          const t = ul ? "ul" : "ol";
          if (!list || list.t !== t) { flush(); list = { t, items: [] }; }
          list.items.push((ul || ol)[1]);
        } else if (/^\u0000\d+\u0000$/.test(line.trim())) {
          flush(); out.push(line.trim());
        } else if (line.trim()) {
          if (list) { list.items[list.items.length - 1] += " " + line.trim(); continue; }
          const last = out[out.length - 1];
          if (last && last.startsWith("<p>") && !last.endsWith("</p>\n")) out[out.length - 1] = last.replace(/<\/p>$/, `<br>${inline(line)}</p>`);
          else out.push(`<p>${inline(line)}</p>`);
        }
      }
      flush();
      out.push("\n");
    }
    return out.join("").replace(/\u0000(\d+)\u0000/g, (_, i) => `<pre><code>${blocks[+i]}</code></pre>`);
  }

  const MODE_ICONS = { teach: "bulb", solve: "target" };

  /* ---------------- Language & TTS ---------------- */
  function probStmt(p) { return (S.lang === "cat" && p?.statement_cat) ? p.statement_cat : (p?.statement || ""); }
  function probOpts(p) { return (S.lang === "cat" && p?.options_cat && Object.keys(p.options_cat).length) ? p.options_cat : (p?.options || {}); }

  function setLanguage(lang) {
    S.lang = lang === "cat" ? "cat" : "eng";
    store.set("sq.lang", S.lang);
    document.documentElement.lang = S.lang === "cat" ? "ca" : "en";
    document.querySelectorAll(".lang-btn").forEach((b) => {
      const on = b.dataset.lang === S.lang;
      b.classList.toggle("on", on);
      b.setAttribute("aria-checked", String(on));
    });
    const t = T();
    if ($("sb-new-label")) $("sb-new-label").textContent = t.newChat;
    if ($("chat-search")) $("chat-search").placeholder = t.searchChats;
    if ($("sb-lib-label")) $("sb-lib-label").textContent = t.library;
    if ($("sb-wb-label")) $("sb-wb-label").textContent = t.workbench;
    if ($("sb-recent-label")) $("sb-recent-label").textContent = t.recent;
    if ($("lib-title")) $("lib-title").textContent = t.library;
    if ($("lib-search")) $("lib-search").placeholder = t.searchProblems;
    if ($("btn-attach-label")) $("btn-attach-label").textContent = t.problemBtn;
    if ($("seg-teach-label")) $("seg-teach-label").textContent = t.modes.teach.name;
    if ($("seg-solve-label")) $("seg-solve-label").textContent = t.modes.solve.name;
    setMode(S.mode, { persistChat: false });
    renderProblem();
    renderChatList();
    if ($("lib")?.open) renderLibrary();
  }

  function setTts(on) {
    S.tts = Boolean(on);
    store.set("sq.tts", S.tts);
    const btn = $("btn-tts");
    if (btn) {
      btn.classList.toggle("on", S.tts);
      btn.setAttribute("aria-pressed", String(S.tts));
    }
    if (!S.tts && "speechSynthesis" in window) {
      try { window.speechSynthesis.cancel(); } catch {}
    }
  }

  function speakText(text) {
    if (!S.tts || !("speechSynthesis" in window) || !text) return;
    try {
      window.speechSynthesis.cancel();
      const utter = new SpeechSynthesisUtterance(text);
      const targetLang = S.lang === "cat" ? "ca-ES" : "en-US";
      utter.lang = targetLang;
      const voices = window.speechSynthesis.getVoices() || [];
      const match = voices.find((v) => v.lang && v.lang.toLowerCase().startsWith(S.lang === "cat" ? "ca" : "en"));
      if (match) utter.voice = match;
      utter.rate = 1.02;
      window.speechSynthesis.speak(utter);
    } catch {}
  }

  /* ---------------- Mode ---------------- */
  function setMode(mode, { persistChat = true } = {}) {
    S.mode = mode;
    store.set("sq.mode", mode);
    document.body.dataset.mode = mode;
    const m = MODES[mode];
    $("model-name").textContent = m.name;
    $("model-tag").textContent = m.tag;
    $("input").placeholder = m.placeholder;
    $("hero-title").textContent = m.title;
    $("hero-sub").textContent = m.sub;
    $("disclaimer").textContent = m.disclaimer;
    document.querySelectorAll(".seg-btn").forEach((b) => { b.classList.toggle("on", b.dataset.mode === mode); b.setAttribute("aria-checked", b.dataset.mode === mode); });
    document.querySelectorAll(".model-opt").forEach((b) => { b.classList.toggle("sel", b.dataset.mode === mode); b.setAttribute("aria-checked", b.dataset.mode === mode); });
    renderSuggestions();
    const c = cur();
    if (c && persistChat) { c.mode = mode; save(); renderChatList(); }
  }

  function renderSuggestions() {
    const box = $("suggestions");
    if (!box) return;
    box.innerHTML = "";
    MODES[S.mode].suggestions.forEach((sg) => {
      const b = el("button", "sugg", `<strong>${esc(sg.t)}</strong><span>${esc(sg.s)}</span>`);
      b.onclick = () => {
        if (sg.random) {
          if (!S.problems.length) return toast("Problem library not loaded");
          attachProblem(S.problems[Math.floor(Math.random() * S.problems.length)]);
          send(sg.random);
        } else send(sg.q);
      };
      box.appendChild(b);
    });
  }

  /* ---------------- Chats ---------------- */
  function newChat() {
    stop();
    S.currentId = null;
    renderThread();
    renderProblem();
    renderChatList();
    $("input").focus();
    if (matchMedia("(max-width: 860px)").matches) setSidebar(false);
  }
  function ensureChat() {
    let c = cur();
    if (!c) {
      c = { id: uid(), title: "New chat", mode: S.mode, problem: S.pendingProblem || null, messages: [], tried: [], solved: false, updated: Date.now() };
      S.pendingProblem = null;
      S.chats.unshift(c);
      S.currentId = c.id;
    }
    return c;
  }
  function openChat(id) {
    stop();
    S.currentId = id;
    const c = cur();
    if (c) setMode(c.mode, { persistChat: false });
    renderThread();
    renderProblem();
    renderChatList();
    if (matchMedia("(max-width: 860px)").matches) setSidebar(false);
  }
  function deleteChat(id) {
    S.chats = S.chats.filter((c) => c.id !== id);
    save();
    if (S.currentId === id) newChat(); else renderChatList();
    toast("Chat deleted");
  }
  function renderChatList() {
    const q = $("chat-search").value.trim().toLowerCase();
    const list = $("chat-list");
    list.innerHTML = "";
    const items = S.chats.filter((c) => c.messages.length && (!q || c.title.toLowerCase().includes(q) || c.messages.some((m) => (m.text || "").toLowerCase().includes(q))));
    if (!items.length) { list.appendChild(el("li", "chat-empty", q ? T().noMatchChats : T().noChats)); return; }
    items.sort((a, b) => b.updated - a.updated).forEach((c) => {
      const li = el("li", "chat-item" + (c.id === S.currentId ? " active" : ""), `<span class="ci-dot ${c.mode}"></span><span class="ci-title">${esc(c.title)}</span>`);
      const del = el("button", "ci-del", icon("trash"));
      del.title = "Delete chat";
      del.onclick = (e) => { e.stopPropagation(); deleteChat(c.id); };
      li.appendChild(del);
      li.onclick = () => openChat(c.id);
      list.appendChild(li);
    });
  }

  /* ---------------- Problem ---------------- */
  function attachProblem(p) {
    const c = cur();
    if (c) { c.problem = p; c.tried = []; c.solved = false; if (!c.messages.length) c.title = `${p.category} · ${p.problem_id}`; save(); }
    else S.pendingProblem = p;
    renderProblem();
    $("input").focus();
  }
  function activeProblem() { const c = cur(); return c ? c.problem : S.pendingProblem || null; }
  function renderProblem() {
    const p = activeProblem();
    const card = $("problem-card");
    card.hidden = !p;
    if (!p) return;
    const c = cur() || { tried: [], solved: false };
    $("pc-badge").textContent = p.category || T().problemBtn;
    $("pc-meta").textContent = `${p.problem_id} · ${p.year || ""} · level ${p.difficulty_level ?? "?"} · ${T().tapOpt}`;
    $("pc-stem").innerHTML = formatMathHtml(probStmt(p));
    const box = $("pc-options");
    box.innerHTML = "";
    Object.entries(probOpts(p)).forEach(([k, v]) => {
      const b = el("button", "opt", `<b>${esc(k)}</b>${formatMathHtml(v)}`);
      b.type = "button";
      if (c.solved === k) b.classList.add("right");
      else if ((c.tried || []).includes(k)) b.classList.add("tried");
      b.onclick = () => send(`${T().answerPrefix} ${k}`, { option: k });
      box.appendChild(b);
    });
  }

  /* ---------------- Thread rendering ---------------- */
  function renderThread() {
    const c = cur();
    const box = $("messages");
    box.innerHTML = "";
    $("empty").hidden = !!(c && c.messages.length);
    if (!c) return;
    c.messages.forEach((m, i) => box.appendChild(m.role === "user" ? userNode(m, i) : botNode(m, i, i === c.messages.length - 1)));
    scrollDown(true);
  }

  function userNode(m, i) {
    const n = el("div", "msg user");
    const text = m.option ? `${esc(m.text.replace(/\s*[A-E]$/, ""))} <span class="answer-tag">${esc(m.option)}</span>` : esc(m.text);
    const wrap = el("div", "bubble-wrap", `<div class="bubble">${text}</div>`);
    const acts = el("div", "actions");
    acts.append(actBtn("copy", "Copy", () => copy(m.text)));
    if (!m.option) acts.append(actBtn("pencil", "Edit", () => editFrom(i)));
    wrap.appendChild(acts);
    n.appendChild(wrap);
    return n;
  }

  function botNode(m, i, isLast) {
    const mode = m.mode || "teach";
    const n = el("div", "msg bot");
    n.appendChild(el("div", `avatar ${mode}`, icon(MODE_ICONS[mode] || "bulb")));
    const body = el("div", "body");
    body.appendChild(el("div", "bot-head", `<strong>${MODES[mode].name}</strong>${m.meta?.model_id ? `<span class="muted">${esc(m.meta.model_id)}</span>` : ""}`));
    const content = el("div", "content");
    if (m.pending) {
      content.innerHTML = `<div class="thinking"><span class="dots"><i></i><i></i><i></i></span><span class="shimmer">${mode === "solve" ? T().solving : T().thinking} ${T().onDevice}</span><span class="elapsed">0s</span></div>`;
      body.appendChild(content);
      n.appendChild(body);
      return n;
    }
    content.innerHTML = m.error ? "" : md(m.text);
    body.appendChild(content);
    body.appendChild(extrasNode(m));
    if (!m.error) {
      const acts = el("div", "actions");
      acts.append(actBtn("copy", "Copy", () => copy(m.text)));
      if (isLast) acts.append(actBtn("refresh", "Regenerate", regenerate));
      const up = actBtn("up", "Good response", () => rate(i, 1)), down = actBtn("down", "Bad response", () => rate(i, -1));
      if (m.rating === 1) up.classList.add("on");
      if (m.rating === -1) down.classList.add("on");
      acts.append(up, down);
      body.appendChild(acts);
      if (isLast && !S.busy) body.appendChild(followupsNode(mode));
    }
    n.appendChild(body);
    return n;
  }

  function extrasNode(m) {
    const f = document.createDocumentFragment();
    const meta = m.meta || {};
    if (m.error) {
      const e = el("div", "callout error guard", `${icon("shield")}<div><b>Couldn't reach the on-device model.</b><br>${esc(m.error)}</div>`);
      const retry = el("button", "followup", "Retry");
      retry.style.marginLeft = "auto";
      retry.onclick = regenerate;
      e.appendChild(retry);
      f.appendChild(e);
      return f;
    }
    if (meta.guard) f.appendChild(el("div", "callout guard", `${icon("shield")}<div><b>Guardrail engaged</b> — ${esc(String(meta.guardReason || "redirected to keep you learning").replace(/_/g, " ").toLowerCase())}</div>`));
    if (meta.is_correct === true) f.appendChild(el("div", "callout correct", `${icon("check")}<div><b>Correct!</b> Nice work — you solved it.</div>`));
    else if (meta.is_correct === false) f.appendChild(el("div", "callout wrong", `${icon("bulb")}<div><b>Not quite.</b> Keep going — that option is ruled out.</div>`));
    if (meta.final && m.mode === "solve") f.appendChild(el("div", "callout final", `${icon("target")}<div>${esc(T().finalAnswer)} <b>${esc(meta.final)}</b></div>`));
    const tags = el("div", "tags");
    if (meta.dimension) tags.appendChild(el("span", "tag acc", esc(meta.dimension)));
    if (meta.latency_ms != null) tags.appendChild(el("span", "tag", `${(meta.latency_ms / 1000).toFixed(1)}s`));
    if (meta.tok_s) tags.appendChild(el("span", "tag", `${(+meta.tok_s).toFixed(1)} tok/s`));
    if (meta.offline !== false) tags.appendChild(el("span", "tag", "offline"));
    if (tags.children.length) f.appendChild(tags);
    return f;
  }

  function followupsNode(mode) {
    const box = el("div", "followups");
    MODES[mode].followups.forEach((fu) => {
      if (fu.mode === "solve" && !activeProblem() && mode === "teach") return;
      const b = el("button", "followup", esc(fu.t));
      b.onclick = () => {
        if (fu.mode) setMode(fu.mode);
        if (fu.fill) { const inp = $("input"); inp.value = fu.fill; autosize(); inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); updateSend(); }
        else send(fu.q);
      };
      box.appendChild(b);
    });
    return box;
  }

  function actBtn(name, title, fn) { const b = el("button", "icon-btn", icon(name)); b.title = title; b.setAttribute("aria-label", title); b.onclick = fn; return b; }

  /* ---------------- Sending ---------------- */
  async function send(text, { option = null, regen = false } = {}) {
    text = (text || "").trim();
    if (S.busy || (!text && !option)) return;
    const c = ensureChat();
    if (!regen) {
      c.messages.push({ role: "user", text, option });
      if (c.title === "New chat") c.title = c.problem ? `${c.problem.category} · ${c.problem.problem_id}` : text.slice(0, 48);
    }
    $("input").value = "";
    autosize();
    const mode = S.mode;
    const bot = { role: "bot", mode, pending: true };
    c.messages.push(bot);
    c.updated = Date.now();
    setBusy(true);
    renderThread();
    renderChatList();

    const t0 = performance.now();
    const ticker = setInterval(() => { const e = document.querySelector(".msg:last-child .elapsed"); if (e) e.textContent = `${Math.round((performance.now() - t0) / 1000)}s`; }, 500);
    S.ctrl = new AbortController();
    const timeout = setTimeout(() => S.ctrl && S.ctrl.abort("timeout"), 120000);
    const userTurns = c.messages.filter((m) => m.role === "user").length;
    const p = c.problem;
    try {
      const res = await fetch("/api/text-query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: S.ctrl.signal,
        body: JSON.stringify({
          problem_id: p ? p.problem_id : "GENERAL-MATH",
          text: option ? "" : text,
          event_type: option ? "ANSWER_SUBMIT" : "HINT_REQUEST",
          attempt_num: Math.max(1, userTurns),
          confidence_pct: 50,
          selected_option: option,
          session_id: c.id,
          mode,
          language: S.lang,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      const r = data.socratic_response || {};
      let answer = mode === "solve" ? (data.solution_text || r.hint_text) : r.hint_text;
      if (data.intervention_applied) answer = "> ⚡ **Agent intervention applied** — RawTree closed-loop nudge\n\n" + answer;
      Object.assign(bot, {
        pending: false,
        text: answer || "…",
        meta: {
          model_id: r.model_id, latency_ms: r.latency_ms ?? Math.round(performance.now() - t0), tok_s: data.tok_s,
          dimension: data.socratic_dimension, guard: data.guardrail_triggered, guardReason: data.guardrail_reason,
          final: data.final_answer || (mode === "solve" && ((r.hint_text || "").match(/^Final Answer: ([A-E])/i) || [])[1]) || null,
          is_correct: option ? r.is_correct : null, offline: data.offline_mode,
        },
      });
      if (option && r.is_correct === true) c.solved = option;
      else if (option && r.is_correct === false) c.tried = [...new Set([...(c.tried || []), option])];
    } catch (err) {
      const aborted = S.ctrl?.signal.aborted;
      const reason = S.ctrl?.signal.reason;
      if (aborted && reason !== "timeout") {
        c.messages.pop();
        finish();
        return;
      }
      Object.assign(bot, { pending: false, text: "", error: reason === "timeout" ? "The request timed out after 120 s." : String(err.message || err) });
    } finally {
      clearInterval(ticker);
      clearTimeout(timeout);
    }
    save();
    renderProblem();
    if (!bot.error) {
      speakText(bot.text);
      await typeOut(c, bot);
    }
    finish();

    function finish() { S.ctrl = null; setBusy(false); renderThread(); renderChatList(); }
  }

  function typeOut(c, bot) {
    const full = bot.text;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches || full.length < 4) return Promise.resolve();
    renderThread();
    const node = document.querySelector("#messages .msg:last-child .content");
    const extras = [...document.querySelectorAll("#messages .msg:last-child .body > :not(.bot-head):not(.content)")];
    extras.forEach((x) => (x.style.visibility = "hidden"));
    return new Promise((resolve) => {
      let i = 0;
      const step = Math.max(2, Math.ceil(full.length / 70));
      node.classList.add("caret");
      S.reveal = () => { i = full.length; };
      const tick = () => {
        if (S.currentId !== c.id) { S.reveal = null; return resolve(); }
        i = Math.min(full.length, i + step);
        node.innerHTML = md(full.slice(0, i));
        scrollDown();
        if (i < full.length) requestAnimationFrame(tick);
        else { node.classList.remove("caret"); S.reveal = null; resolve(); }
      };
      requestAnimationFrame(tick);
    });
  }

  function stop() {
    if (S.ctrl) S.ctrl.abort("user");
    if (S.reveal) S.reveal();
  }
  function regenerate() {
    const c = cur();
    if (!c || S.busy) return;
    while (c.messages.length && c.messages[c.messages.length - 1].role === "bot") c.messages.pop();
    const last = c.messages[c.messages.length - 1];
    if (!last) return;
    send(last.text, { option: last.option, regen: true });
  }
  function editFrom(i) {
    const c = cur();
    if (!c || S.busy) return;
    const text = c.messages[i].text;
    c.messages = c.messages.slice(0, i);
    save();
    renderThread();
    const inp = $("input");
    inp.value = text;
    autosize();
    updateSend();
    inp.focus();
  }
  function rate(i, v) {
    const c = cur();
    if (!c) return;
    c.messages[i].rating = c.messages[i].rating === v ? 0 : v;
    save();
    renderThread();
    toast(v > 0 ? "Thanks for the feedback!" : "Thanks — we'll use this to improve");
  }

  /* ---------------- UI helpers ---------------- */
  function setBusy(b) { S.busy = b; document.body.classList.toggle("busy", b); updateSend(); $("btn-send").title = b ? "Stop (Esc)" : "Send (Enter)"; }
  function updateSend() { $("btn-send").disabled = !S.busy && !$("input").value.trim(); }
  function autosize() { const t = $("input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 220) + "px"; }
  function nearBottom() { const t = $("thread"); return t.scrollHeight - t.scrollTop - t.clientHeight < 120; }
  function scrollDown(force) { const t = $("thread"); if (force || nearBottom()) t.scrollTop = t.scrollHeight; }
  function copy(text) {
    const done = () => toast("Copied to clipboard");
    if (navigator.clipboard && isSecureContext) navigator.clipboard.writeText(text).then(done);
    else { const ta = el("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); done(); }
  }
  let toastT;
  function toast(msg) { const t = $("toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show"), 1800); }
  function setSidebar(open) { document.body.classList.toggle("sb-collapsed", !open); store.set("sq.sb", open); }
  function setTheme(t) { document.body.dataset.theme = t; store.set("sq.theme", t); $("btn-theme").innerHTML = icon(t === "dark" ? "sun" : "moon"); }

  function exportChat() {
    const c = cur();
    if (!c || !c.messages.length) return toast("Nothing to export yet");
    const lines = [`# ${c.title}`, ""];
    if (c.problem) lines.push(`> **${c.problem.problem_id}** — ${c.problem.statement}`, "", ...Object.entries(c.problem.options || {}).map(([k, v]) => `> ${k}) ${v}`), "");
    c.messages.forEach((m) => lines.push(m.role === "user" ? `**You:** ${m.text}` : `**${MODES[m.mode || "teach"].name}:** ${m.text || m.error || ""}`, ""));
    const a = el("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/markdown" }));
    a.download = `socraticq-${c.title.replace(/[^\w]+/g, "-").slice(0, 40)}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  /* ---------------- Library ---------------- */
  function renderLibrary() {
    const cats = ["All", ...new Set(S.problems.map((p) => p.category))];
    const fbox = $("lib-filters");
    fbox.innerHTML = "";
    cats.forEach((cat) => {
      const label = cat === "All" ? T().allCats : cat;
      const b = el("button", cat === S.libCat ? "on" : "", esc(label));
      b.onclick = () => { S.libCat = cat; renderLibrary(); };
      fbox.appendChild(b);
    });
    const q = $("lib-search").value.trim().toLowerCase();
    const list = $("lib-list");
    list.innerHTML = "";
    S.problems
      .filter((p) => (S.libCat === "All" || p.category === S.libCat) && (!q || `${p.problem_id} ${probStmt(p)} ${p.statement} ${p.category}`.toLowerCase().includes(q)))
      .forEach((p) => {
        const lvl = Math.max(1, Math.min(5, +p.difficulty_level || 1));
        const b = el("button", "lib-item", `<div class="li-meta"><span class="tag acc">${esc(p.category)}</span><span class="tag">${esc(p.problem_id)}</span><span class="diff" title="Difficulty">${"●".repeat(lvl)}${"○".repeat(5 - lvl)}</span></div><p>${esc(probStmt(p))}</p>`);
        b.onclick = () => { $("lib").close(); if (cur() && cur().messages.length) newChat(); attachProblem(p); };
        list.appendChild(b);
      });
    if (!list.children.length) list.appendChild(el("div", "chat-empty", T().noMatchProbs));
  }
  function openLibrary() { renderLibrary(); $("lib").showModal(); $("lib-search").focus(); }

  async function loadProblems() {
    try {
      const r = await fetch("/problems");
      S.problems = await r.json();
      $("lib-count").textContent = S.problems.length;
    } catch { /* offline UI still works for free chat */ }
  }

  async function pollStatus() {
    try {
      const r = await fetch("/api/system-status");
      const d = await r.json();
      $("dev-dot").className = "dot " + (d.slm_reachable ? "ok" : "warn");
      $("dev-name").textContent = d.device_name || "Edge device";
      const bits = [d.model_id || "slm", d.slm_reachable ? "live" : "fallback"];
      if (d.ram_free_mb) bits.push(`${d.ram_free_mb} MB free`);
      $("dev-sub").textContent = bits.join(" · ");
      $("menu-model-id").textContent = d.model_id || "on-device SLM";
      $("pill-offline").querySelector(".dot").className = "dot " + (d.offline_mode === false ? "warn" : "ok");
    } catch {
      $("dev-dot").className = "dot bad";
      $("dev-name").textContent = "Server unreachable";
      $("dev-sub").textContent = "retrying…";
    }
  }

  /* ---------------- Mic (Whisper /api/voice-query + Web Speech preview) ---------------- */
  function encodeWav16kMono(chunks, srcRate) {
    let total = 0;
    chunks.forEach((c) => (total += c.length));
    const merged = new Float32Array(total);
    let offset = 0;
    chunks.forEach((c) => { merged.set(c, offset); offset += c.length; });
    const ratio = srcRate / 16000;
    const outLen = Math.max(1, Math.floor(merged.length / ratio));
    const buf = new ArrayBuffer(44 + outLen * 2);
    const v = new DataView(buf);
    const wr = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    wr(0, "RIFF"); v.setUint32(4, 36 + outLen * 2, true); wr(8, "WAVE");
    wr(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true);
    v.setUint16(22, 1, true); v.setUint32(24, 16000, true); v.setUint32(28, 32000, true);
    v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    wr(36, "data"); v.setUint32(40, outLen * 2, true);
    for (let i = 0; i < outLen; i++) {
      const pos = i * ratio;
      const idx = Math.floor(pos);
      const frac = pos - idx;
      const s0 = merged[Math.min(idx, merged.length - 1)];
      const s1 = merged[Math.min(idx + 1, merged.length - 1)];
      const s = Math.max(-1, Math.min(1, s0 + (s1 - s0) * frac));
      v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Blob([buf], { type: "audio/wav" });
  }

  async function sendVoiceBlob(blob, fallbackTranscript) {
    if (S.busy) return;
    const c = ensureChat();
    const mode = S.mode;
    const p = c.problem;
    const userTurns = c.messages.filter((m) => m.role === "user").length + 1;
    const placeholderUser = { role: "user", text: fallbackTranscript || "🎙️ …" };
    const bot = { role: "bot", mode, pending: true };
    c.messages.push(placeholderUser, bot);
    c.updated = Date.now();
    setBusy(true);
    renderThread();
    renderChatList();

    const fd = new FormData();
    fd.append("audio", blob, "utterance.wav");
    fd.append("problem_id", p ? p.problem_id : "GENERAL-MATH");
    fd.append("attempt_num", String(Math.max(1, userTurns)));
    fd.append("confidence_pct", "50");
    fd.append("session_id", c.id);
    fd.append("mode", mode);
    fd.append("language", S.lang);
    if (fallbackTranscript) fd.append("fallback_transcript", fallbackTranscript);

    const t0 = performance.now();
    try {
      const res = await fetch("/api/voice-query", { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      if (data.transcription) {
        placeholderUser.text = data.transcription;
        if (c.title === "New chat") c.title = p ? `${p.category} · ${p.problem_id}` : data.transcription.slice(0, 48);
      }
      const r = data.socratic_response || {};
      let answer = mode === "solve" ? (data.solution_text || r.hint_text) : r.hint_text;
      if (data.intervention_applied) answer = "> ⚡ **Agent intervention applied** — RawTree closed-loop nudge\n\n" + answer;
      Object.assign(bot, {
        pending: false,
        text: answer || "…",
        meta: {
          model_id: r.model_id, latency_ms: r.latency_ms ?? Math.round(performance.now() - t0), tok_s: data.tok_s,
          dimension: data.socratic_dimension, guard: data.guardrail_triggered, guardReason: data.guardrail_reason,
          final: data.final_answer || null, offline: data.offline_mode,
        },
      });
    } catch (err) {
      Object.assign(bot, { pending: false, text: "", error: String(err.message || err) });
    }
    save();
    renderProblem();
    if (!bot.error) {
      speakText(bot.text);
      await typeOut(c, bot);
    }
    setBusy(false);
    renderThread();
    renderChatList();
  }

  function initMic() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const hasMedia = Boolean(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    if (!hasMedia && (!SR || !isSecureContext)) return;
    const btn = $("btn-mic");
    btn.hidden = false;
    let rec = null;
    let actx = null;
    let proc = null;
    let stream = null;
    let pcmChunks = [];
    let liveText = "";

    const stopAll = async () => {
      btn.classList.remove("rec");
      if (rec) { try { rec.stop(); } catch {} rec = null; }
      if (proc) { proc.disconnect(); proc = null; }
      if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
      if (actx) {
        const rate = actx.sampleRate || 48000;
        try { await actx.close(); } catch {}
        actx = null;
        if (pcmChunks.length) {
          const wavBlob = encodeWav16kMono(pcmChunks, rate);
          pcmChunks = [];
          await sendVoiceBlob(wavBlob, liveText);
        }
      }
    };

    btn.onclick = async () => {
      if (actx || rec) return stopAll();
      liveText = "";
      if (SR && isSecureContext) {
        try {
          rec = new SR();
          rec.interimResults = true;
          rec.lang = S.lang === "cat" ? "ca-ES" : "en-US";
          rec.onresult = (e) => {
            liveText = [...e.results].map((x) => x[0].transcript).join("").trim();
            if (!actx) { $("input").value = liveText; autosize(); updateSend(); }
          };
          rec.onend = () => { if (!actx) { rec = null; btn.classList.remove("rec"); } };
          rec.start();
        } catch { rec = null; }
      }
      if (hasMedia) {
        try {
          stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          const AudioCtx = window.AudioContext || window.webkitAudioContext;
          actx = new AudioCtx();
          const src = actx.createMediaStreamSource(stream);
          proc = actx.createScriptProcessor(4096, 1, 1);
          pcmChunks = [];
          proc.onaudioprocess = (ev) => { pcmChunks.push(new Float32Array(ev.inputBuffer.getChannelData(0))); };
          src.connect(proc);
          proc.connect(actx.destination);
          btn.classList.add("rec");
          return;
        } catch (err) {
          if (!rec) toast(S.lang === "cat" ? "Micròfon no disponible (cal HTTPS o localhost)" : "Mic unavailable (requires HTTPS or localhost)");
        }
      }
      if (rec) btn.classList.add("rec");
    };
  }

  /* ---------------- Wire up ---------------- */
  function init() {
    setTheme(store.get("sq.theme", matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));
    setSidebar(store.get("sq.sb", !matchMedia("(max-width: 860px)").matches));
    setTts(S.tts);
    setLanguage(S.lang);

    document.querySelectorAll(".lang-btn").forEach((b) => {
      b.addEventListener("click", () => setLanguage(b.dataset.lang));
    });
    if ($("btn-tts")) $("btn-tts").onclick = () => {
      setTts(!S.tts);
      toast(S.tts ? T().ttsOn : T().ttsOff);
    };

    const inp = $("input");
    inp.addEventListener("input", () => { autosize(); updateSend(); });
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(inp.value); } });
    $("composer").addEventListener("submit", (e) => { e.preventDefault(); S.busy ? stop() : send(inp.value); });

    document.querySelectorAll(".seg-btn, .model-opt").forEach((b) => b.addEventListener("click", () => { setMode(b.dataset.mode); closeMenu(); inp.focus(); }));
    const menu = $("model-menu"), mbtn = $("model-btn");
    const closeMenu = () => { menu.hidden = true; mbtn.setAttribute("aria-expanded", "false"); };
    mbtn.onclick = (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; mbtn.setAttribute("aria-expanded", String(!menu.hidden)); };
    document.addEventListener("click", (e) => { if (!menu.hidden && !menu.contains(e.target)) closeMenu(); });

    $("btn-new").onclick = newChat;
    $("btn-new-top").onclick = newChat;
    $("btn-collapse").onclick = () => setSidebar(false);
    $("btn-open-sb").onclick = () => setSidebar(true);
    $("scrim").onclick = () => setSidebar(false);
    $("chat-search").oninput = renderChatList;
    $("btn-theme").onclick = () => setTheme(document.body.dataset.theme === "dark" ? "light" : "dark");
    $("btn-export").onclick = exportChat;
    $("btn-library").onclick = openLibrary;
    $("btn-attach").onclick = openLibrary;
    $("lib-close").onclick = () => $("lib").close();
    $("lib").addEventListener("click", (e) => { if (e.target === $("lib")) $("lib").close(); });
    $("lib-search").oninput = renderLibrary;
    $("pc-close").onclick = () => { const c = cur(); if (c) { c.problem = null; save(); } S.pendingProblem = null; renderProblem(); };

    const thread = $("thread");
    thread.addEventListener("scroll", () => { $("btn-jump").hidden = nearBottom(); });
    $("btn-jump").onclick = () => scrollDown(true);

    document.addEventListener("keydown", (e) => {
      const k = e.key.toLowerCase();
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && k === "o") { e.preventDefault(); newChat(); }
      else if (e.key === "Escape" && S.busy) stop();
      else if (e.key === "/" && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); inp.focus(); }
    });

    initMic();
    renderThread();
    renderChatList();
    loadProblems();
    pollStatus();
    setInterval(pollStatus, 8000);
    inp.focus();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
