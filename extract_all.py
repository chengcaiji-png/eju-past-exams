#!/usr/bin/env python3
"""Complete EJU data extraction pipeline.

1. Render answer PDFs to images
2. OCR answer keys + missing question pages (JW 2011_1, Japanese all years)
3. Parse answer keys into structured data
4. Extract Japanese 読解/聴読解/聴解 questions from existing text
5. Merge everything into questions.json with answers matched
"""

import os
os.environ["DISABLE_MODEL_SOURCE_CHECK"] = "True"

import json
import re
import time
import fitz  # PyMuPDF
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).parent
IMG_DIR = BASE_DIR / "images"
JSON_DIR = BASE_DIR / "json"
JASSO_DIR = BASE_DIR / "jasso"

# ═══════════════════════════════════════════════════════════════
# STEP 1: Render answer PDFs to images
# ═══════════════════════════════════════════════════════════════

def render_pdf_to_images(pdf_path: Path, out_dir: Path, dpi: int = 150):
    """Render all pages of a PDF to PNG images."""
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = list(out_dir.glob("*.png"))
    if existing:
        return len(existing)

    doc = fitz.open(str(pdf_path))
    count = 0
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    for page_num in range(len(doc)):
        page = doc[page_num]
        pix = page.get_pixmap(matrix=mat)
        out_path = out_dir / f"page_{page_num + 1:03d}.png"
        pix.save(str(out_path))
        count += 1
    doc.close()
    return count


def render_all_answer_pdfs():
    """Render all answer PDFs that don't have images yet."""
    print("\n=== Step 1: Render answer PDFs to images ===")
    rendered = 0
    for session_dir in sorted(JASSO_DIR.iterdir()):
        if not session_dir.is_dir():
            continue
        m = re.match(r"(\d{4})_第(\d+)回", session_dir.name)
        if not m:
            continue
        year, session = m.group(1), m.group(2)
        year_session = f"{year}_{session}"

        for pdf in sorted(session_dir.glob("*answer*.pdf")):
            if pdf.name.startswith("._"):
                continue
            # Skip English versions
            if "_e." in pdf.name or "_e_" in pdf.name or "_e2" in pdf.name:
                continue

            out_dir = IMG_DIR / year_session / pdf.stem
            if list(out_dir.glob("*.png")):
                continue

            print(f"  Rendering {year_session}/{pdf.name}...")
            count = render_pdf_to_images(pdf, out_dir)
            rendered += count

    print(f"  Rendered {rendered} new answer pages")
    return rendered


# ═══════════════════════════════════════════════════════════════
# STEP 2: Render missing question PDFs (Japanese, JW 2011_1)
# ═══════════════════════════════════════════════════════════════

def render_missing_question_pdfs():
    """Render Japanese and other missing question PDFs."""
    print("\n=== Step 2: Render missing question PDFs ===")
    rendered = 0

    for session_dir in sorted(JASSO_DIR.iterdir()):
        if not session_dir.is_dir():
            continue
        m = re.match(r"(\d{4})_第(\d+)回", session_dir.name)
        if not m:
            continue
        year, session = int(m.group(1)), int(m.group(2))
        year_session = f"{year}_{session}"

        for pdf in sorted(session_dir.glob("*.pdf")):
            if pdf.name.startswith("._"):
                continue
            fn = pdf.name.lower()

            # Skip English versions
            if "_e." in fn or "_e_" in fn or fn.endswith("_e.pdf"):
                continue
            # Skip answer/script files (handled separately)
            if "answer" in fn or "script" in fn:
                continue

            # Check if images already exist
            out_dir = IMG_DIR / year_session / pdf.stem
            if list(out_dir.glob("*.png")):
                continue

            # We need: Japanese (jafl) for all years, JW for 2011_1
            need = False
            if "jafl" in fn or "ja_" in fn:
                need = True
            elif "jw" in fn and year == 2011 and session == 1:
                need = True

            if need:
                print(f"  Rendering {year_session}/{pdf.name}...")
                count = render_pdf_to_images(pdf, out_dir)
                rendered += count

    print(f"  Rendered {rendered} new question pages")
    return rendered


# ═══════════════════════════════════════════════════════════════
# STEP 3: OCR all new images
# ═══════════════════════════════════════════════════════════════

def ocr_new_images():
    """OCR all newly rendered images (answers + missing questions)."""
    print("\n=== Step 3: OCR new images ===")

    from paddleocr import PaddleOCR
    ocr = PaddleOCR(use_textline_orientation=True, lang="japan")

    # Find all image dirs that aren't in ocr_results.json yet
    existing_ocr = set()
    ocr_path = JSON_DIR / "ocr_results.json"
    if ocr_path.exists():
        with open(ocr_path) as f:
            existing = json.load(f)
        for r in existing["results"]:
            existing_ocr.add(r["dir_name"])

    new_results = []
    total_pages = 0

    for year_dir in sorted(IMG_DIR.iterdir()):
        if not year_dir.is_dir():
            continue
        m = re.match(r"(\d{4})_(\d+)", year_dir.name)
        if not m:
            continue
        year, session = int(m.group(1)), int(m.group(2))

        for sub_dir in sorted(year_dir.iterdir()):
            if not sub_dir.is_dir():
                continue
            dn = sub_dir.name
            if dn in existing_ocr:
                continue

            fn = dn.lower()
            # Skip English versions and duplicates
            if re.search(r"_e(_\d+)?$", fn) or "_en" in fn:
                continue
            if re.search(r"_e\d", fn):
                continue

            # We want: answer keys, Japanese questions, JW 2011_1
            is_answer = "answer" in fn
            is_japanese = "jafl" in fn
            is_jw = "jw" in fn
            need = is_answer or is_japanese or (is_jw and year == 2011 and session == 1)

            if not need:
                continue

            pngs = sorted(sub_dir.glob("*.png"))
            if not pngs:
                continue

            print(f"  OCR {dn} ({len(pngs)} pages)...", flush=True)
            pages = []
            for png in pngs:
                page_num = int(re.search(r"(\d+)", png.stem).group(1))
                results = list(ocr.predict(str(png)))
                lines = []
                for r in results:
                    for text, score, poly in zip(r["rec_texts"], r["rec_scores"], r["rec_polys"]):
                        y_center = sum(p[1] for p in poly) / 4
                        x_center = sum(p[0] for p in poly) / 4
                        lines.append({"text": text, "confidence": float(score),
                                      "y": float(y_center), "x": float(x_center)})
                lines.sort(key=lambda l: (l["y"], l["x"]))

                # Convert to text
                text = _lines_to_text(lines)
                avg_conf = sum(l["confidence"] for l in lines) / len(lines) if lines else 0

                pages.append({
                    "page": page_num,
                    "image": str(png.relative_to(BASE_DIR)),
                    "text": text,
                    "line_count": len(lines),
                    "avg_confidence": round(avg_conf, 3),
                })
                total_pages += 1

            subject = "answer"
            if is_japanese:
                subject = "japanese"
            elif is_jw:
                subject = "japan_and_world"

            new_results.append({
                "year": year,
                "session": session,
                "subject": subject,
                "dir_name": dn,
                "page_count": len(pages),
                "pages": pages,
            })

    # Append to existing OCR results
    if new_results and ocr_path.exists():
        with open(ocr_path) as f:
            existing = json.load(f)
        existing["results"].extend(new_results)
        existing["total_directories"] += len(new_results)
        existing["total_pages"] += total_pages
        with open(ocr_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

    print(f"  OCR'd {total_pages} new pages across {len(new_results)} directories")
    return new_results


def _lines_to_text(lines, min_conf=0.3):
    if not lines:
        return ""
    filtered = [l for l in lines if l["confidence"] >= min_conf]
    if not filtered:
        return ""
    rows = []
    current_row = [filtered[0]]
    for l in filtered[1:]:
        if abs(l["y"] - current_row[-1]["y"]) < 15:
            current_row.append(l)
        else:
            rows.append(current_row)
            current_row = [l]
    rows.append(current_row)
    text_lines = []
    for row in rows:
        row.sort(key=lambda l: l["x"])
        text_lines.append(" ".join(l["text"] for l in row))
    return "\n".join(text_lines)


# ═══════════════════════════════════════════════════════════════
# STEP 4: Extract answer keys
# ═══════════════════════════════════════════════════════════════

def extract_answer_keys():
    """Extract answer keys from OCR results and text-extracted JSON."""
    print("\n=== Step 4: Extract answer keys ===")
    all_answers = {}  # (year, session) -> {subject: {question_num: answer}}

    # Method A: Parse from text-extracted JSON (answer5 files have tables)
    for jf in sorted(JSON_DIR.glob("[0-9]*_[0-9]*.json")):
        with open(jf) as f:
            data = json.load(f)
        year = data.get("year")
        session = data.get("session")
        if not year:
            continue

        subjects = data.get("subjects", {})
        for subj_key, subj_data in subjects.items():
            for fi in subj_data.get("files", []):
                fn = fi.get("filename", "").lower()
                if "answer" not in fn:
                    continue
                # Parse answer table from text
                for page in fi.get("pages", []):
                    text = page.get("text", "")
                    answers = _parse_answer_table(text, year, session)
                    if answers:
                        key = (year, session)
                        if key not in all_answers:
                            all_answers[key] = {}
                        all_answers[key].update(answers)

    # Method B: Parse from OCR results
    ocr_path = JSON_DIR / "ocr_results.json"
    if ocr_path.exists():
        with open(ocr_path) as f:
            ocr_data = json.load(f)
        for r in ocr_data["results"]:
            if r["subject"] != "answer":
                continue
            full_text = "\n".join(p["text"] for p in r["pages"] if p["text"])
            answers = _parse_answer_table(full_text, r["year"], r["session"])
            if answers:
                key = (r["year"], r["session"])
                if key not in all_answers:
                    all_answers[key] = {}
                all_answers[key].update(answers)

    # Method C: OCR answer PDFs directly if no text available
    for session_dir in sorted(JASSO_DIR.iterdir()):
        if not session_dir.is_dir():
            continue
        m = re.match(r"(\d{4})_第(\d+)回", session_dir.name)
        if not m:
            continue
        year, session = int(m.group(1)), int(m.group(2))
        key = (year, session)

        if key in all_answers and len(all_answers[key]) > 5:
            continue  # Already have answers

        for pdf in sorted(session_dir.glob("*answer*.pdf")):
            if pdf.name.startswith("._") or "_e" in pdf.name.lower():
                continue
            if "writing" in pdf.name.lower():
                continue  # Skip writing model answers

            # Try text extraction first
            try:
                doc = fitz.open(str(pdf))
                for page in doc:
                    text = page.get_text()
                    if text.strip():
                        answers = _parse_answer_table(text, year, session)
                        if answers:
                            if key not in all_answers:
                                all_answers[key] = {}
                            all_answers[key].update(answers)
                doc.close()
            except Exception as e:
                print(f"  Error reading {pdf.name}: {e}")

    # Save answer keys
    answers_output = {}
    for (year, session), answers in sorted(all_answers.items()):
        label = f"{year}_{session}"
        answers_output[label] = answers

    out_path = JSON_DIR / "answer_keys.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(answers_output, f, ensure_ascii=False, indent=2)

    total = sum(len(a) for a in all_answers.values())
    print(f"  Extracted {total} answers across {len(all_answers)} sessions")
    print(f"  Saved to {out_path}")

    return all_answers


def _parse_answer_table(text: str, year: int, session: int) -> dict:
    """Parse answer key table from text. Returns {subject_qnum: answer}."""
    answers = {}

    # Detect subject sections
    current_subject = None

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Detect subject headers
        if "物理" in line and ("Physics" in line or "物理" in line):
            current_subject = "physics"
        elif "化学" in line and ("Chemistry" in line or "化学" in line):
            current_subject = "chemistry"
        elif "生物" in line and ("Biology" in line or "生物" in line):
            current_subject = "biology"
        elif "理" in line and "科" in line and "Science" in line:
            current_subject = "science_header"
        elif "総合科目" in line or "Japan and the World" in line:
            current_subject = "jw"
        elif "日本語" in line or "Japanese" in line:
            current_subject = "japanese"
        elif "数学" in line and ("Math" in line or "数学" in line):
            current_subject = "math"
        elif "読解" in line:
            current_subject = "ja_reading"
        elif "聴読解" in line:
            current_subject = "ja_listening_reading"
        elif "聴解" in line:
            current_subject = "ja_listening"

        # Parse answer rows: 問N [row_num] answer
        # Various formats:
        # "問1 1 3" or "問 1 1 3" or "問1 1 ③"
        m = re.match(r"問\s*(\d+)\s+(\d+)\s+(\d+)", line)
        if m and current_subject:
            q_num = int(m.group(1))
            row_num = int(m.group(2))
            answer = int(m.group(3))
            subj = current_subject
            if subj == "science_header":
                subj = "physics"  # Default to physics if just "Science" header
            answers[f"{subj}_q{q_num}"] = answer
            answers[f"{subj}_row{row_num}"] = answer
            continue

        # Alternative: just row_num and answer (e.g., "1 3")
        m = re.match(r"^(\d{1,2})\s+(\d)$", line)
        if m and current_subject:
            row_num = int(m.group(1))
            answer = int(m.group(2))
            subj = current_subject
            answers[f"{subj}_row{row_num}"] = answer

        # Format: "N番 row_num" for Japanese listening
        m = re.match(r"(\d+)\s*番\s+(\d+)", line)
        if m and current_subject:
            q_num = int(m.group(1))
            row_num = int(m.group(2))
            # Next number on the line might be the answer
            rest = line[m.end():]
            m2 = re.search(r"(\d)", rest)
            if m2:
                answer = int(m2.group(1))
                answers[f"{current_subject}_q{q_num}"] = answer

    return answers


# ═══════════════════════════════════════════════════════════════
# STEP 5: Extract Japanese questions from text
# ═══════════════════════════════════════════════════════════════

def extract_japanese_questions():
    """Extract Japanese reading/listening questions from text-extracted JSON."""
    print("\n=== Step 5: Extract Japanese questions ===")
    all_questions = []

    for jf in sorted(JSON_DIR.glob("[0-9]*_[0-9]*.json")):
        with open(jf) as f:
            data = json.load(f)
        year = data.get("year")
        session = data.get("session")
        if not year:
            continue

        subjects = data.get("subjects", {})
        ja_data = subjects.get("japanese", {})
        if not ja_data:
            continue

        for fi in ja_data.get("files", []):
            fn = fi.get("filename", "").lower()
            if "answer" in fn or "script" in fn:
                continue

            pages = fi.get("pages", [])
            full_text = "\n\n".join(
                f"[PAGE_{p.get('page_num', i)}]\n{p.get('text', '')}"
                for i, p in enumerate(pages) if p.get("text", "").strip()
            )

            if not full_text.strip() or len(full_text) < 100:
                continue

            questions = _parse_japanese_full(full_text, year, session, fn)
            all_questions.extend(questions)

    # Also extract from OCR results
    ocr_path = JSON_DIR / "ocr_results.json"
    if ocr_path.exists():
        with open(ocr_path) as f:
            ocr_data = json.load(f)
        for r in ocr_data["results"]:
            if r["subject"] != "japanese":
                continue
            if "answer" in r["dir_name"].lower() or "script" in r["dir_name"].lower():
                continue
            full_text = "\n\n".join(
                f"[PAGE_{p['page']}]\n{p['text']}"
                for p in r["pages"] if p["text"].strip()
            )
            if len(full_text) < 100:
                continue
            questions = _parse_japanese_full(full_text, r["year"], r["session"], r["dir_name"])
            for q in questions:
                q["source"] = "ocr"
            all_questions.extend(questions)

    print(f"  Extracted {len(all_questions)} Japanese questions")
    return all_questions


def _parse_japanese_full(text: str, year: int, session: int, source: str) -> list:
    """Parse Japanese exam with 読解, 聴読解, 聴解 sections."""
    questions = []

    # Detect section boundaries
    reading_start = 0
    listening_reading_start = None
    listening_start = None

    # Look for section markers
    for m in re.finditer(r"聴読解", text):
        listening_reading_start = m.start()
        break
    for m in re.finditer(r"聴解\s*(?:Listening)", text):
        listening_start = m.start()
        break
    if listening_start is None:
        for m in re.finditer(r"聴\s*解", text):
            if listening_reading_start and m.start() > listening_reading_start:
                listening_start = m.start()
                break

    # Section boundaries
    sections = []
    if listening_reading_start:
        sections.append(("読解", reading_start, listening_reading_start))
        if listening_start:
            sections.append(("聴読解", listening_reading_start, listening_start))
            sections.append(("聴解", listening_start, len(text)))
        else:
            sections.append(("聴読解", listening_reading_start, len(text)))
    else:
        sections.append(("読解", 0, len(text)))

    for section_name, start, end in sections:
        section_text = text[start:end]

        # Find questions using Roman numeral sections (I, II, III... or Ⅰ, Ⅱ, Ⅲ...)
        # Japanese reading has passages labeled with Roman numerals, each with sub-questions

        # Find 問 markers with their numbers
        q_pattern = re.compile(r"問\s*(\d+)")
        matches = list(q_pattern.finditer(section_text))

        for idx, match in enumerate(matches):
            q_num = int(match.group(1))
            q_start = match.end()
            q_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(section_text)
            q_text = section_text[q_start:q_end].strip()

            # Clean up
            q_text = re.sub(r"\[PAGE_\d+\]", "", q_text)
            q_text = re.sub(r"©?\s*\d{4}.*?Organization", "", q_text)
            q_text = re.sub(r"[Ss]ervices\s*[Oo]rganization", "", q_text)
            q_text = q_text.strip()

            if len(q_text) < 10:
                continue

            # Check for copyright block
            if "著作権上の都合" in q_text and len(q_text) < 100:
                continue

            # Extract choices
            choices = []
            for cm in re.finditer(r"([①②③④])\s*(.+?)(?=[①②③④]|$)", q_text, re.DOTALL):
                choices.append({"marker": cm.group(1), "text": cm.group(2).strip()})

            questions.append({
                "question_number": q_num,
                "text": f"問{q_num} {q_text}",
                "type": "multiple_choice" if choices else "reading_comprehension",
                "choices": choices if choices else None,
                "section": section_name,
                "year": year,
                "session": session,
                "session_label": f"{year}_第{session}回",
                "subject": "japanese",
                "subject_ja": "日本語",
                "subject_detail": section_name,
                "source": "text",
                "source_file": source,
                "id": f"ja_{year}_{session}_{section_name}_{q_num}",
            })

    return questions


# ═══════════════════════════════════════════════════════════════
# STEP 6: Extract JW questions from new OCR
# ═══════════════════════════════════════════════════════════════

def extract_new_jw_questions(new_ocr_results: list) -> list:
    """Extract JW questions from newly OCR'd pages."""
    print("\n=== Step 6: Extract new JW/Japanese OCR questions ===")
    questions = []

    for r in new_ocr_results:
        if r["subject"] not in ("japan_and_world", "japanese"):
            continue
        if "answer" in r["dir_name"].lower():
            continue

        full_text = "\n\n".join(
            f"[PAGE_{p['page']}]\n{p['text']}"
            for p in r["pages"] if p["text"].strip()
        )
        if len(full_text) < 100:
            continue

        if r["subject"] == "japanese":
            qs = _parse_japanese_full(full_text, r["year"], r["session"], r["dir_name"])
            for q in qs:
                q["source"] = "ocr"
            questions.extend(qs)
        else:
            # JW parser
            q_pattern = re.compile(r"問\s*(\d+)")
            matches = list(q_pattern.finditer(full_text))
            for idx, m in enumerate(matches):
                q_num = int(m.group(1))
                start = m.end()
                end = matches[idx + 1].start() if idx + 1 < len(matches) else len(full_text)
                q_text = full_text[start:end].strip()
                q_text = re.sub(r"\[PAGE_\d+\]", "", q_text)
                q_text = re.sub(r"©?\s*\d{4}.*?Organization", "", q_text)
                q_text = re.sub(r"[Ss]ervices\s*[Oo]rganization", "", q_text)
                q_text = q_text.strip()
                if len(q_text) < 10:
                    continue

                choices = []
                for cm in re.finditer(r"([①②③④])\s*(.+?)(?=[①②③④]|$)", q_text, re.DOTALL):
                    choices.append({"marker": cm.group(1), "text": cm.group(2).strip()})

                questions.append({
                    "question_number": q_num,
                    "text": f"問{q_num} {q_text}",
                    "type": "multiple_choice" if choices else "fill_in",
                    "choices": choices if choices else None,
                    "year": r["year"],
                    "session": r["session"],
                    "session_label": f"{r['year']}_第{r['session']}回",
                    "subject": "japan_and_world",
                    "subject_ja": "総合科目",
                    "source": "ocr",
                    "source_dir": r["dir_name"],
                    "id": f"ocr_{r['year']}_{r['session']}_jw_{q_num}",
                })

    print(f"  Extracted {len(questions)} new questions")
    return questions


# ═══════════════════════════════════════════════════════════════
# STEP 7: Match answers to questions
# ═══════════════════════════════════════════════════════════════

def match_answers_to_questions(all_answers: dict):
    """Match answer keys to questions in questions.json."""
    print("\n=== Step 7: Match answers to questions ===")

    with open(JSON_DIR / "questions.json") as f:
        data = json.load(f)

    matched = 0
    for q in data["questions"]:
        year = q.get("year")
        session = q.get("session")
        key = (year, session)

        if key not in all_answers:
            continue

        answers = all_answers[key]
        subject = q.get("subject", "")
        q_num = q.get("question_number", 0)
        detail = q.get("subject_detail", "")

        # Try to find matching answer
        answer = None

        if subject == "science":
            # Map subject_detail to answer key subject
            subj_map = {"物理": "physics", "化学": "chemistry", "生物": "biology"}
            ans_subj = subj_map.get(detail, "physics")
            answer = answers.get(f"{ans_subj}_q{q_num}")

        elif subject == "japan_and_world":
            answer = answers.get(f"jw_q{q_num}") or answers.get(f"jw_row{q_num}")

        elif subject == "math":
            answer = answers.get(f"math_q{q_num}") or answers.get(f"math_row{q_num}")

        elif subject == "japanese":
            section_map = {"読解": "ja_reading", "聴読解": "ja_listening_reading", "聴解": "ja_listening"}
            ans_subj = section_map.get(detail, "ja_reading")
            answer = answers.get(f"{ans_subj}_q{q_num}")

        if answer is not None:
            q["correct_answer"] = answer
            matched += 1

    # Save
    with open(JSON_DIR / "questions.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"  Matched {matched}/{len(data['questions'])} questions with answers")


# ═══════════════════════════════════════════════════════════════
# STEP 8: Merge all new questions
# ═══════════════════════════════════════════════════════════════

def merge_new_questions(japanese_qs: list, new_ocr_qs: list):
    """Add new questions to questions.json, avoiding duplicates."""
    print("\n=== Step 8: Merge new questions ===")

    with open(JSON_DIR / "questions.json") as f:
        data = json.load(f)

    existing_ids = {q.get("id", "") for q in data["questions"]}
    added = 0

    for q in japanese_qs + new_ocr_qs:
        qid = q.get("id", "")
        if qid and qid not in existing_ids:
            data["questions"].append(q)
            existing_ids.add(qid)
            added += 1

    # Sort
    data["questions"].sort(key=lambda q: (
        q.get("year", 0), q.get("session", 0),
        q.get("subject", ""), q.get("question_number", 0)
    ))
    data["total_questions"] = len(data["questions"])

    # Update by_subject
    by_subj = defaultdict(int)
    for q in data["questions"]:
        by_subj[q.get("subject", "unknown")] += 1
    data["by_subject"] = dict(by_subj)

    with open(JSON_DIR / "questions.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"  Added {added} new questions")
    print(f"  Total: {data['total_questions']} questions")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    t0 = time.time()
    print("=" * 60)
    print("EJU Complete Data Extraction Pipeline")
    print("=" * 60)

    # Step 1-2: Render PDFs
    render_all_answer_pdfs()
    render_missing_question_pdfs()

    # Step 3: OCR new images
    new_ocr = ocr_new_images()

    # Step 4: Extract answer keys
    all_answers = extract_answer_keys()

    # Step 5: Extract Japanese questions from text
    japanese_qs = extract_japanese_questions()

    # Step 6: Extract questions from new OCR
    new_ocr_qs = extract_new_jw_questions(new_ocr)

    # Step 7: Merge new questions
    merge_new_questions(japanese_qs, new_ocr_qs)

    # Step 8: Match answers
    match_answers_to_questions(all_answers)

    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"Pipeline complete in {elapsed / 60:.1f} min")
    print("=" * 60)

    # Final summary
    with open(JSON_DIR / "questions.json") as f:
        data = json.load(f)
    print(f"\nFinal: {data['total_questions']} questions")
    for s, c in sorted(data["by_subject"].items(), key=lambda x: -x[1]):
        print(f"  {s}: {c}")

    answered = sum(1 for q in data["questions"] if "correct_answer" in q)
    print(f"\nWith answers: {answered}/{data['total_questions']}")


if __name__ == "__main__":
    main()
