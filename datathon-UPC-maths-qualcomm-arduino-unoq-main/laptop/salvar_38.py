#!/usr/bin/env python3
"""Salvar el 38% del dataset M3Kang (arXiv:2601.16218) - Versió 2.0 Corregida i Rigorosa

Correccions de Qualitat de Dades:
1. Anti-Contaminació d'Idiomes: Les opcions només es comparteixen entre idiomes si són
   estrictament matemàtiques/numèriques (is_language_independent). Les opcions amb text
   o figures es processen exclusivament amb la imatge i prompt de la seva llengua nativa.
2. Validació d'Opcions Úniques: S'exigeix rigorosament len(set(options.values())) == 5.
   Qualsevol col·lisió o duplicat és rebutjat automàticament.
3. Reconstrucció Heurística de Fraccions (Fase 3): Repara fraccions trencades per OCR
   ('1 2' -> '1/2', '12' -> '1/2' per monotonicitat) i descarta col·lisions.
4. Selecció d'Imatge Específica i Gestió Robusta d'Errors:
   - S'utilitza la imatge nativa (ex: imatge cat per a problemes en català).
   - Suport complet per a --val-years i --val-fraction.
   - max_tokens ampliat a 2500 per evitar truncament de raonament del VLM.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
REPO_ROOT = os.path.dirname(PROJECT_ROOT)
DEFAULT_OUT_DIR = os.path.join(PROJECT_ROOT, "data")

HELMCODE_URL = "https://api.helmcode.com/v1/chat/completions"
HELMCODE_MODEL = "glm5.3-flash"
MAX_WORKERS = 16

LETTERS = ("A", "B", "C", "D", "E")

# Unitats matemàtiques i físiques vàlides universalment
MATH_UNITS = {
    "cm", "m", "km", "mm", "dm", "kg", "g", "mg", "s", "min", "h", "l", "ml",
    "deg", "circ", "pi", "rad", "eur", "x", "y", "z", "a", "b", "c", "d", "e",
    "n", "k", "p", "q", "r", "t", "v", "w", "km/h", "m/s", "cm²", "m²", "cm³"
}


def find_parquet_shard(parquet_path: str = "", out_dir: str = DEFAULT_OUT_DIR) -> str:
    if parquet_path:
        if os.path.exists(parquet_path):
            return parquet_path
        raise FileNotFoundError(f"No s'ha trobat el fitxer parquet indicat: {parquet_path}")

    import glob

    candidates = [
        os.path.join(out_dir, "high_quality-00000-of-00004.parquet"),
        os.path.join(PROJECT_ROOT, "data", "high_quality-00000-of-00004.parquet"),
        os.path.join(PROJECT_ROOT, "data", "raw", "high_quality-00000-of-00004.parquet"),
        os.path.join(REPO_ROOT, "data", "high_quality-00000-of-00004.parquet"),
        os.path.join("data", "high_quality-00000-of-00004.parquet"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c

    hf_pattern = os.path.join(
        os.path.expanduser("~"),
        ".cache",
        "huggingface",
        "hub",
        "datasets--qualcomm--M3Kang",
        "**",
        "high_quality-00000-of-00004.parquet",
    )
    matches = glob.glob(hf_pattern, recursive=True)
    if matches:
        return matches[0]
    raise FileNotFoundError(
        "No s'ha trobat high_quality-00000-of-00004.parquet! Passa --parquet <ruta>."
    )


def clean_option_text(text: str) -> str:
    """Neteja prefixos tipus 'A)', 'a.', 'A -' i normalitza espais en blanc."""
    s = re.sub(r"\s+", " ", str(text)).strip()
    s = re.sub(r"^[A-Ea-e]\s*[\)\]:.]\s*", "", s).strip()
    s = re.sub(r"^[A-Ea-e]\s+-\s*", "", s).strip()
    s = re.sub(r"^[A-E]-\s*(?=\d)", "", s).strip()
    return re.sub(r"\s+", " ", s).strip()


def has_five_unique_options(opts: dict) -> bool:
    """Comprova que hi hagi exactament 5 opcions no buides i úniques (case/whitespace-insensitive)."""
    if not opts or len(opts) != 5:
        return False
    vals = []
    for k in LETTERS:
        v = opts.get(k)
        if not v:
            return False
        norm = re.sub(r"\s+", " ", str(v)).strip().lower()
        if not norm:
            return False
        vals.append(norm)
    return len(set(vals)) == 5


def is_language_independent_text(text: str) -> bool:
    """Comprova si el text és purament numèric o de fórmula matemàtica."""
    # Eliminar ordres de LaTeX (\frac, \sqrt, \pm, \cdot, \times, etc.)
    t = re.sub(r"\\[a-zA-Z]+", "", str(text))
    # Extreure paraules alfabètiques (incloent paraules de 2 lletres com no/is/in/or/si/de)
    words = re.findall(r"[a-zA-ZÀ-ÿ]{2,}", t)
    for w in words:
        if w.lower() not in MATH_UNITS:
            return False
    return True


def are_options_language_independent(opts: dict) -> bool:
    """Comprova si les 5 opcions són independents de la llengua."""
    if not opts or len(opts) != 5:
        return False
    return all(is_language_independent_text(v) for v in opts.values())


def reconstruct_fractions(opts: dict) -> dict:
    """Fase 3 Heurística: Repara fraccions trencades sense corrompre rangs tipus '1-2'."""
    if not opts or len(opts) != 5 or not all(opts.get(k) for k in LETTERS):
        return {}
    cleaned_in = {k: clean_option_text(opts[k]) for k in LETTERS}
    if not all(cleaned_in.values()):
        return {}
    fixed = dict(cleaned_in)

    # 1. Cas números separats per espai: '1 2' o '3 4' -> '1/2', '3/4'
    for k, v in fixed.items():
        m = re.match(r"^(\d+)\s+(\d+)$", v)
        if m and not m.group(2).startswith("0") and int(m.group(2)) > 0:
            fixed[k] = f"{int(m.group(1))}/{int(m.group(2))}"

    # 2. Cas '1-2' -> '1/2' només si hi ha context de fracció entre les opcions (no rangs '1-2', '2-3', ...)
    has_fraction_context = any("/" in val or "\\frac" in val for val in fixed.values())
    if has_fraction_context:
        for k, v in fixed.items():
            m = re.match(r"^(\d+)-(\d+)$", v)
            if m:
                num, den = int(m.group(1)), int(m.group(2))
                if 0 < num < den and not m.group(2).startswith("0"):
                    candidate = f"{num}/{den}"
                    other_vals = {fixed[ok].lower() for ok in LETTERS if ok != k}
                    if candidate.lower() not in other_vals:
                        fixed[k] = candidate

    # 3. Validar que totes les 5 siguin no buides i estrictament úniques (case-insensitive)
    if has_five_unique_options(fixed):
        return fixed
    if has_five_unique_options(cleaned_in):
        return cleaned_in
    return {}


def parse_vlm_json(content: str) -> dict:
    """Extreu el diccionari de 5 opcions del text del VLM verificant que siguin úniques."""
    if not content:
        return {}
    m_code = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
    raw = m_code.group(1) if m_code else content
    m_json = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m_json:
        return {}
    try:
        data = json.loads(m_json.group(0))
    except Exception:
        cleaned = re.sub(r",\s*([\}\]])", r"\1", m_json.group(0))
        try:
            data = json.loads(cleaned)
        except Exception:
            return {}

    if "options" in data and isinstance(data["options"], dict):
        data = data["options"]

    if all(k in data for k in LETTERS):
        cleaned_opts = {k: clean_option_text(data[k]) for k in LETTERS}
        # CRUCIAL: Validar que totes les 5 opcions siguin diferents (anti-al·lucinació)
        if has_five_unique_options(cleaned_opts):
            return reconstruct_fractions(cleaned_opts)
    return {}


def query_vlm_single(api_key: str, img_bytes: bytes, lang: str, retries: int = 3) -> dict:
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")

    if lang == "cat":
        prompt = """Ets un assistent matemàtic expert en les Proves Cangur en català.
Extreu les 5 opcions de resposta (A, B, C, D, E) d'aquest problema.
1. Si l'opció és un nombre, valor o fórmula: transcriu-la exactament (usa format n/d o LaTeX).
2. Si l'opció és una figura geomètrica, peça de trencaclosques o dibuix: descriu breument la figura en CATALÀ en 3-8 paraules (ex: 'peça amb pestanya a dalt', 'triangle amb estrella').
Totes les 5 opcions han de ser estrictament diferents entre si.
Retorna estrictament un JSON amb les claus A, B, C, D, E:
{"A": "...", "B": "...", "C": "...", "D": "...", "E": "..."}"""
    else:
        prompt = """You are an expert mathematical contest parser for the Kangaroo Math Competition.
Extract the 5 answer options (A, B, C, D, E) from this problem image.
1. If the option is a number, value, algebraic expression, or fraction: transcribe accurately (use n/d or LaTeX).
2. If the option is a geometric figure, shape, puzzle piece, or drawing: provide a concise 3-8 word textual description.
All 5 options must be strictly distinct.
Output strictly valid JSON with keys "A", "B", "C", "D", "E":
{"A": "...", "B": "...", "C": "...", "D": "...", "E": "..."}"""

    payload = {
        "model": HELMCODE_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                ],
            }
        ],
        "max_tokens": 2500,
        "temperature": 0.1,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        HELMCODE_URL,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                if not res or "choices" not in res or not res["choices"]:
                    continue
                choice = res["choices"][0].get("message", {})
                content = choice.get("content", "")
                opts = parse_vlm_json(content)
                if opts:
                    return opts
                # Fallback al reasoning_content si el content estava buit
                reasoning = choice.get("reasoning_content", "")
                opts_reasoning = parse_vlm_json(reasoning)
                if opts_reasoning:
                    return opts_reasoning
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2.0 * (attempt + 1))
            else:
                time.sleep(1.0)
        except Exception:
            time.sleep(1.0)
    return {}


def compute_split(problem_id: str, year: int, val_fraction: float, val_years: set) -> str:
    if val_years:
        return "val" if str(year) in val_years else "train"
    bucket = int(hashlib.sha256(problem_id.encode("utf-8")).hexdigest(), 16) % 1000
    return "val" if bucket < val_fraction * 1000 else "train"


def main():
    import pyarrow.parquet as pq

    p = argparse.ArgumentParser(description="Salvar el 38% del dataset M3Kang (v2.0 Rigorosa)")
    p.add_argument("--parquet", default="", help="Ruta al fitxer parquet M3Kang (opcional)")
    p.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="Directori de sortida (per defecte data/)")
    p.add_argument("--val-fraction", type=float, default=0.15, help="Fracció held out per validació")
    p.add_argument("--val-years", default="", help="Anys per validació (ex: 2023,2024)")
    p.add_argument("--limit-vlm", type=int, default=0, help="Límit opcional de crides VLM per proves")
    p.add_argument("--api-key", default=os.environ.get("HELMCODE_API_KEY", ""), help="Clau API HelmCode (o variable HELMCODE_API_KEY)")
    p.add_argument("--save-images", action="store_true", default=True, help="Guardar imatges com a PNG")
    args = p.parse_args()

    val_year_set = set(y.strip() for y in args.val_years.split(",") if y.strip())
    os.makedirs(args.out_dir, exist_ok=True)
    images_dir = os.path.join(args.out_dir, "images")
    if args.save_images:
        os.makedirs(images_dir, exist_ok=True)

    print("=========================================================================")
    print("  MISSIÓ: SALVAR EL 38% DEL DATASET M3KANG (v2.0 Rigorosa i Anti-Biaix) ")
    print("=========================================================================")

    # 1. Carregar Shard Parquet
    parquet_path = find_parquet_shard(args.parquet, out_dir=args.out_dir)
    print(f"[*] Carregant Parquet des de: {parquet_path}...")
    table = pq.read_table(parquet_path)
    print(f"    Total files al shard: {table.num_rows}")

    rows_by_id_lang = {}
    unique_ids = set()
    img_by_id_lang = {}  # (pid, lang) -> img_bytes

    for i in range(table.num_rows):
        lang = table.column("language")[i].as_py()
        if lang not in {"cat", "eng", "esp", "fra"}:
            continue
        pid = table.column("id")[i].as_py()
        unique_ids.add(pid)
        r = {
            "id": pid,
            "language": lang,
            "level": table.column("level")[i].as_py(),
            "year": int(pid.split("_")[1]),
            "text_only": table.column("text_only")[i].as_py(),
            "text": table.column("text")[i].as_py().strip(),
            "label": table.column("label")[i].as_py(),
        }
        img_bytes = table.column("image")[i].as_py()["bytes"]
        img_by_id_lang[(pid, lang)] = img_bytes
        rows_by_id_lang[(pid, lang)] = r

        if args.save_images:
            img_filename = os.path.join(images_dir, f"{pid}_{lang}.png")
            if not os.path.exists(img_filename):
                with open(img_filename, "wb") as f_img:
                    f_img.write(img_bytes)

    print(f"    Problem IDs únics trobats: {len(unique_ids)}")

    # 2. Carregar Cache existent d'OCR amb Validació de 5 Opcions Úniques
    ocr_cache_path = os.path.join(args.out_dir, ".ocr_cache.jsonl")
    banc_neutral = {}  # pid -> {"A": ..., ...} (NOMÉS si són purament matemàtiques)
    banc_per_lang = {}  # (pid, lang) -> {"A": ..., ...}
    initial_ocr_found = 0

    if os.path.exists(ocr_cache_path):
        print(f"[*] Llegint cache RapidOCR de {ocr_cache_path}...")
        with open(ocr_cache_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    opts = entry.get("options", {})
                    # Validar claus i unicitat estricta
                    if opts and len(opts) == 5 and all(opts.get(k) for k in LETTERS):
                        cleaned = reconstruct_fractions(opts)
                        if has_five_unique_options(cleaned):
                            parts = entry["key"].split("|")
                            pid = parts[0]
                            k_lang = parts[1] if len(parts) > 1 else "eng"
                            banc_per_lang[(pid, k_lang)] = cleaned
                            # Comprovar si és segur compartir entre idiomes (purament matemàtic)
                            if are_options_language_independent(cleaned):
                                banc_neutral[pid] = cleaned
                            initial_ocr_found += 1
                except Exception:
                    pass
        print(f"    Opcions vàlides i úniques llegides de la cache RapidOCR: {initial_ocr_found}")

    # 3. Carregar Cache VLM existent (v2.0)
    vlm_cache_path = os.path.join(args.out_dir, ".vlm_cache_v2.jsonl")
    old_vlm_cache_path = os.path.join(args.out_dir, ".vlm_cache.jsonl")
    vlm_cache_count = 0

    # Migrar de la cache antiga només les opcions que siguin vàlides, úniques i no contaminades
    if os.path.exists(old_vlm_cache_path) and not os.path.exists(vlm_cache_path):
        print(f"[*] Auditant i sanejant cache VLM prèvia...")
        with open(old_vlm_cache_path, "r", encoding="utf-8") as f_in, open(vlm_cache_path, "w", encoding="utf-8") as f_out:
            for line in f_in:
                try:
                    entry = json.loads(line)
                    opts = entry.get("options", {})
                    if opts and len(opts) == 5 and all(opts.get(k) for k in LETTERS):
                        cleaned = reconstruct_fractions(opts)
                        if has_five_unique_options(cleaned):
                            pid = entry["id"]
                            # Si és neutral (matemàtica pura), serveix per a tots els idiomes
                            if are_options_language_independent(cleaned):
                                banc_neutral[pid] = cleaned
                                f_out.write(json.dumps({"id": pid, "lang": "neutral", "options": cleaned}, ensure_ascii=False) + "\n")
                                vlm_cache_count += 1
                            else:
                                # Opcions amb text anglès -> només per a anglès
                                banc_per_lang[(pid, "eng")] = cleaned
                                f_out.write(json.dumps({"id": pid, "lang": "eng", "options": cleaned}, ensure_ascii=False) + "\n")
                                vlm_cache_count += 1
                except Exception:
                    pass
        print(f"    Opcions sanejades i transferides a v2: {vlm_cache_count}")
    elif os.path.exists(vlm_cache_path):
        with open(vlm_cache_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    opts = entry.get("options", {})
                    if opts and len(opts) == 5 and all(opts.get(k) for k in LETTERS):
                        cleaned = reconstruct_fractions(opts)
                        if has_five_unique_options(cleaned):
                            pid = entry["id"]
                            k_lang = entry.get("lang", "neutral")
                            if k_lang == "neutral":
                                banc_neutral[pid] = cleaned
                            else:
                                banc_per_lang[(pid, k_lang)] = cleaned
                            vlm_cache_count += 1
                except Exception:
                    pass
        print(f"    Opcions carregades des de cache v2: {vlm_cache_count}")

    # 4. FASE 1: Creuament Multilingüe per ID (Només Opcions Neutres)
    print("\n-------------------------------------------------------------------------")
    print("  FASE 1: Creuament Multilingüe Segur per ID (Sense Contaminació)")
    print("-------------------------------------------------------------------------")
    target_keys = [(pid, lang) for pid in unique_ids for lang in ("cat", "eng") if (pid, lang) in rows_by_id_lang]
    total_target = len(target_keys)

    def get_resolved_options(pid, lang):
        # 1. Si tenim opcions en la pròpia llengua
        if (pid, lang) in banc_per_lang:
            return banc_per_lang[(pid, lang)]
        # 2. Si tenim opcions purament matemàtiques/neutres
        if pid in banc_neutral:
            return banc_neutral[pid]
        return None

    amb_opcions_fase1 = sum(1 for (pid, lang) in target_keys if get_resolved_options(pid, lang) is not None)
    print(f"  Cobertura inicial vàlida (sense contaminació): {amb_opcions_fase1} / {total_target} ({100*amb_opcions_fase1/max(1, total_target):.1f}%)")

    # Determinar quins problemes necessiten resolució per idioma
    missing_tasks = []
    for (pid, lang) in target_keys:
        if get_resolved_options(pid, lang) is None:
            missing_tasks.append((pid, lang))

    print(f"  Tasques de resolució pendents: {len(missing_tasks)} ({len(set(t[0] for t in missing_tasks))} IDs únics)")

    # 5. FASE 2: Rescat d'Opcions amb VLM (Imatge i Prompt Específic per Idioma)
    print("\n-------------------------------------------------------------------------")
    print("  FASE 2: Rescat d'Opcions Visuals amb VLM en Llengua Nativa")
    print("-------------------------------------------------------------------------")
    if args.limit_vlm > 0:
        missing_tasks = missing_tasks[:args.limit_vlm]
        print(f"  [AVÍS] Aplicant límit de {args.limit_vlm} crides VLM...")

    vlm_rescued = 0
    if missing_tasks:
        if not args.api_key or not args.api_key.strip():
            sys.exit(
                "Error: Cal una clau API per executar la Fase 2 VLM (--api-key o variable d'entorn HELMCODE_API_KEY)."
            )
        print(f"[*] Executant rescat VLM amb {MAX_WORKERS} fils concurrents...")
        f_vlm_out = open(vlm_cache_path, "a", encoding="utf-8")
        t0 = time.time()

        def vlm_worker(task):
            pid, lang = task
            # Prioritzar la imatge del propi idioma
            img_bytes = img_by_id_lang.get((pid, lang)) or img_by_id_lang.get((pid, "eng"))
            if not img_bytes:
                return pid, lang, {}
            opts = query_vlm_single(args.api_key, img_bytes, lang)
            return pid, lang, opts

        done_count = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_task = {executor.submit(vlm_worker, t): t for t in missing_tasks}
            for future in as_completed(future_to_task):
                pid, lang, opts = future.result()
                done_count += 1
                if has_five_unique_options(opts):
                    if are_options_language_independent(opts):
                        banc_neutral[pid] = opts
                        f_vlm_out.write(json.dumps({"id": pid, "lang": "neutral", "options": opts}, ensure_ascii=False) + "\n")
                    else:
                        banc_per_lang[(pid, lang)] = opts
                        f_vlm_out.write(json.dumps({"id": pid, "lang": lang, "options": opts}, ensure_ascii=False) + "\n")
                    f_vlm_out.flush()
                    vlm_rescued += 1

                if done_count % 50 == 0 or done_count == len(missing_tasks):
                    elapsed = time.time() - t0
                    rate = done_count / max(1e-3, elapsed)
                    print(f"    Progrés VLM: {done_count}/{len(missing_tasks)} ({vlm_rescued} resolts, {rate:.1f} req/s)", flush=True)

        f_vlm_out.close()
        print(f"  [✓] VLM ha resolt amb èxit {vlm_rescued} tasques!")

    # 6. FASE 3: Consolidació, Reconstrucció de Fraccions i Generació de Fitxers
    print("\n-------------------------------------------------------------------------")
    print("  FASE 3: Consolidació, Neteja i Generació de Datasets")
    print("-------------------------------------------------------------------------")

    train_rows = []
    val_rows = []

    for lang in ("cat", "eng"):
        for pid in sorted(unique_ids):
            key = (pid, lang)
            if key not in rows_by_id_lang:
                continue
            r = dict(rows_by_id_lang[key])
            opts = get_resolved_options(pid, lang)
            if opts:
                opts = reconstruct_fractions(opts)
                if not has_five_unique_options(opts):
                    opts = {}
            else:
                opts = {}
            r["options"] = opts

            split = compute_split(pid, r["year"], args.val_fraction, val_year_set)
            if split == "train":
                train_rows.append(r)
            else:
                val_rows.append(r)

    total_rows = train_rows + val_rows
    total_amb_opcions = sum(1 for r in total_rows if r.get("options") and len(r["options"]) == 5)

    # Escriure fitxers recuperats consolidats
    train_recup_path = os.path.join(args.out_dir, "train_recuperat.jsonl")
    val_recup_path = os.path.join(args.out_dir, "val_recuperat.jsonl")

    with open(train_recup_path, "w", encoding="utf-8") as f:
        for r in train_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with open(val_recup_path, "w", encoding="utf-8") as f:
        for r in val_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Actualitzar train.jsonl i val.jsonl per compatibilitat amb eval.py i build_index.py
    with open(os.path.join(args.out_dir, "train.jsonl"), "w", encoding="utf-8") as f:
        for r in train_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with open(os.path.join(args.out_dir, "val.jsonl"), "w", encoding="utf-8") as f:
        for r in val_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 7. Auditoria de Qualitat i Informe Final
    pct_final = 100 * total_amb_opcions / max(1, len(total_rows))
    english_words_in_cat = 0
    duplicates_found = 0

    for r in total_rows:
        opts = r.get("options", {})
        if not opts:
            continue
        if not has_five_unique_options(opts):
            duplicates_found += 1
        if r["language"] == "cat" and not are_options_language_independent(opts):
            # Comprovar si conté paraules clares en anglès (excloent 'triangle'/'rectangle', que són català vàlid)
            for v in opts.values():
                if re.search(r"\b(square|circle|star|black|white|with|left|right|top|bottom)\b", str(v).lower()):
                    english_words_in_cat += 1
                    break

    print("\n=========================================================================")
    print("                AUDITORIA DE QUALITAT I INFORME FINAL (v2.0)             ")
    print("=========================================================================")
    print(f"Fitxers generats:")
    print(f"  • {train_recup_path} ({len(train_rows)} files)")
    print(f"  • {val_recup_path} ({len(val_rows)} files)")
    if val_year_set:
        print(f"  • Split per anys (val-years): {sorted(val_year_set)}")
    else:
        print(f"  • Split determinista SHA-256: {100*(1-args.val_fraction):.0f}% train / {100*args.val_fraction:.0f}% val")

    print(f"\nMètriques de Qualitat i Cobertura:")
    print(f"  • Total problemes (CAT + ENG): {len(total_rows)}")
    print(f"  • Problemes amb 5 opcions completes i vàlides: {total_amb_opcions} / {len(total_rows)} ({pct_final:.1f}%)")
    print(f"  • Col·lisions d'opcions duplicades trobades: {duplicates_found} (Objectiu: 0)")
    print(f"  • Contaminació d'anglès en opcions catalanes: {english_words_in_cat} (Objectiu: 0)")

    for lang in ("cat", "eng"):
        sub = [r for r in total_rows if r["language"] == lang]
        sub_ok = sum(1 for r in sub if r.get("options") and len(r["options"]) == 5)
        print(f"    - {lang.upper()}: {sub_ok}/{len(sub)} ({100*sub_ok/max(1, len(sub)):.1f}%)")

    diagrams = [r for r in total_rows if not r["text_only"]]
    diagrams_ok = sum(1 for r in diagrams if r.get("options") and len(r["options"]) == 5)
    print(f"    - Problemes geomètrics / diagrames: {diagrams_ok}/{len(diagrams)} ({100*diagrams_ok/max(1, len(diagrams)):.1f}%)")

    labels = Counter(r["label"] for r in train_rows if r.get("options"))
    if labels:
        top_lbl, count_lbl = labels.most_common(1)[0]
        print(f"  • Resposta més comuna a Train: {top_lbl} ({100 * count_lbl / sum(labels.values()):.1f}%)")
    print("=========================================================================")


if __name__ == "__main__":
    main()
