#!/usr/bin/env python3
"""Convert EJU past exam PDFs to structured JSON for study database.

Outputs:
  json/index.json           - master index
  json/{year}_{session}.json - per-session data (text + image references)
  json/eju_all.json         - combined (text only, no images inlined)
  images/{year}_{session}/{filename}/page_{N}.png - page images for image-based PDFs
"""

import json
import re
import sys
from pathlib import Path
import fitz  # PyMuPDF

BASE_DIR = Path(__file__).parent
JASSO_DIR = BASE_DIR / "jasso"
OUTPUT_DIR = BASE_DIR / "json"
IMAGES_DIR = BASE_DIR / "images"

# DPI for page-to-image rendering
RENDER_DPI = 150


def classify_file(filename: str) -> dict:
    """Classify a PDF by subject, type, and language from its filename."""
    fn = filename.lower()
    info = {"filename": filename}

    # Determine type (check writing_sample before answer since it contains 'answer')
    if re.search(r"answer_jafl_writing", fn):
        info["type"] = "writing_sample"
    elif re.search(r"script_", fn):
        info["type"] = "listening_script"
    elif re.search(r"answer", fn):
        info["type"] = "answer_key"
    elif re.search(r"question_", fn):
        info["type"] = "question"
    else:
        info["type"] = "unknown"

    # Determine subject
    if re.search(r"math", fn):
        info["subject"] = "math"
        info["subject_ja"] = "数学"
    elif re.search(r"science", fn):
        info["subject"] = "science"
        info["subject_ja"] = "理科"
    elif re.search(r"jw", fn):
        info["subject"] = "japan_and_world"
        info["subject_ja"] = "総合科目"
    elif re.search(r"(jafl|iafl|ifl|jaf_l)", fn):
        info["subject"] = "japanese"
        info["subject_ja"] = "日本語"
    else:
        info["subject"] = "all"
        info["subject_ja"] = "全科目"

    # Determine language
    if info["subject"] == "japanese":
        info["language"] = "ja"
    elif re.search(r"_e[\._\d]|_e$|_en\.|_e_rev", fn.replace(".pdf", "")):
        info["language"] = "en"
    else:
        info["language"] = "ja"

    return info


def is_meaningful_text(text: str) -> bool:
    """Check if extracted text has real content (not just copyright/page markers)."""
    clean = re.sub(r"ⓒ\s*\d{4}.*?Organization", "", text)
    clean = re.sub(r"[\x00-\x08\n\r\t ]", "", clean)
    return len(clean) > 10


def extract_pdf(pdf_path: Path, img_out_dir: Path | None) -> dict:
    """Extract text and optionally render images for a PDF.

    Returns {total_pages, pages: [{page, text, has_text, image_path?}]}
    """
    result = {"total_pages": 0, "pages": [], "text_pages": 0, "image_pages": 0}
    try:
        doc = fitz.open(str(pdf_path))
        result["total_pages"] = len(doc)

        for i in range(len(doc)):
            page = doc[i]
            text = page.get_text().strip()
            has_text = is_meaningful_text(text)

            page_data = {"page": i + 1}

            if has_text:
                page_data["text"] = text
                page_data["has_text"] = True
                result["text_pages"] += 1
            else:
                page_data["has_text"] = False
                result["image_pages"] += 1

            # Render page as image if it's image-based or has poor text
            if img_out_dir is not None and not has_text and i > 0:
                # Skip cover/copyright pages (page 1 usually)
                img_out_dir.mkdir(parents=True, exist_ok=True)
                img_path = img_out_dir / f"page_{i + 1:03d}.png"
                if not img_path.exists():
                    mat = fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
                    pix = page.get_pixmap(matrix=mat)
                    pix.save(str(img_path))
                page_data["image_path"] = str(img_path.relative_to(BASE_DIR))

            # Also render pages that have text but might have diagrams/formulas
            if img_out_dir is not None and has_text and i > 0:
                # Check if page has images embedded (diagrams, charts)
                if page.get_images() or result["image_pages"] > result["text_pages"]:
                    img_out_dir.mkdir(parents=True, exist_ok=True)
                    img_path = img_out_dir / f"page_{i + 1:03d}.png"
                    if not img_path.exists():
                        mat = fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
                        pix = page.get_pixmap(matrix=mat)
                        pix.save(str(img_path))
                    page_data["image_path"] = str(img_path.relative_to(BASE_DIR))

            result["pages"].append(page_data)

        doc.close()
    except Exception as e:
        print(f"  Error reading {pdf_path}: {e}", file=sys.stderr)
    return result


def parse_questions_jw(pages: list[dict]) -> list[dict]:
    """Parse individual questions from Japan & World (総合科目)."""
    text_pages = [p for p in pages if p.get("has_text") and p.get("text")]
    if not text_pages:
        return []

    full_text = "\n".join(p["text"] for p in text_pages)
    questions = []

    # Split by 問N pattern (問１ or 問1 etc.)
    parts = re.split(r"(問\s*\d+|問[１-９])", full_text)

    current_q = None
    for part in parts:
        m = re.match(r"問\s*(\d+|[１-９])", part)
        if m:
            if current_q:
                questions.append(current_q)
            num_str = m.group(1)
            num = int(num_str) if num_str.isdigit() else "１２３４５６７８９".index(num_str) + 1
            current_q = {"question_number": num, "text": "", "sub_questions": []}
        elif current_q is not None:
            current_q["text"] += part

    if current_q:
        questions.append(current_q)

    # Parse sub-questions and choices
    for q in questions:
        sub_parts = re.split(r"(⑴|⑵|⑶|⑷|⑸|⑹)", q["text"])
        subs = []
        current_sub = None
        for sp in sub_parts:
            if sp in "⑴⑵⑶⑷⑸⑹":
                if current_sub:
                    subs.append(current_sub)
                sub_num = "⑴⑵⑶⑷⑸⑹".index(sp) + 1
                current_sub = {"sub_number": sub_num, "text": "", "choices": []}
            elif current_sub is not None:
                current_sub["text"] += sp
        if current_sub:
            subs.append(current_sub)

        for sub in subs:
            choice_parts = re.split(r"(①|②|③|④|⑤|⑥)", sub["text"])
            choices = []
            for j in range(1, len(choice_parts), 2):
                if j + 1 < len(choice_parts):
                    label = choice_parts[j]
                    content = choice_parts[j + 1].strip()
                    content = re.sub(r"\s*ⓒ.*?Organization\s*", "", content).strip()
                    if content:
                        choices.append({"label": label, "text": content})
            if choices:
                sub["choices"] = choices
            if "①" in sub["text"]:
                sub["text"] = sub["text"][:sub["text"].index("①")].strip()

        if subs:
            q["sub_questions"] = subs
        if "⑴" in q["text"]:
            q["text"] = q["text"][:q["text"].index("⑴")].strip()

    return questions


def parse_questions_science(pages: list[dict]) -> list[dict]:
    """Parse individual questions from Science (理科).

    Science has three sections: 物理 (Physics), 化学 (Chemistry), 生物 (Biology).
    Questions use numbered circles (1⃝ 2⃝ etc.) for choices.
    """
    text_pages = [p for p in pages if p.get("has_text") and p.get("text")]
    if not text_pages:
        return []

    full_text = "\n".join(p["text"] for p in text_pages)
    sections = []

    # Split by subject headers
    section_markers = re.split(r"(物理|化学|生物)", full_text)
    current_section = None
    for part in section_markers:
        if part in ("物理", "化学", "生物"):
            if current_section and current_section["text"]:
                sections.append(current_section)
            current_section = {"section": part, "text": ""}
        elif current_section is not None:
            current_section["text"] += part

    if current_section and current_section["text"]:
        sections.append(current_section)

    # For each section, split by 問 markers
    questions = []
    for section in sections:
        q_parts = re.split(r"\n(問\s)", section["text"])
        for k in range(1, len(q_parts), 2):
            q_text = q_parts[k] + (q_parts[k + 1] if k + 1 < len(q_parts) else "")
            q_text = re.sub(r"\s*ⓒ.*?Organization\s*", "", q_text).strip()
            if len(q_text) > 20:
                # Extract circled number choices
                choices = []
                choice_parts = re.split(r"([1-6]⃝)", q_text)
                for j in range(1, len(choice_parts), 2):
                    if j + 1 < len(choice_parts):
                        label = choice_parts[j]
                        content = choice_parts[j + 1].strip().split("\n")[0].strip()
                        if content:
                            choices.append({"label": label, "text": content})

                questions.append({
                    "section": section["section"],
                    "text": q_text[:q_text.index("1⃝")].strip() if "1⃝" in q_text else q_text,
                    "choices": choices
                })

    return questions


def process_session(session_dir: Path) -> dict:
    """Process all PDFs in a session directory."""
    dir_name = session_dir.name
    m = re.match(r"(\d{4})_第(\d)回", dir_name)
    if not m:
        return None

    year = int(m.group(1))
    session = int(m.group(2))

    if year <= 2018:
        era_year = year - 1988
        exam_name = f"平成{era_year}年度（{year}年度）日本留学試験"
    else:
        era_year = year - 2018
        exam_name = f"令和{era_year}年度（{year}年度）日本留学試験"

    session_data = {
        "year": year,
        "session": session,
        "session_label": dir_name,
        "exam_name": exam_name,
        "subjects": {}
    }

    pdf_files = sorted(session_dir.glob("*.pdf"))
    print(f"  Processing {dir_name}: {len(pdf_files)} PDFs")

    for pdf_path in pdf_files:
        info = classify_file(pdf_path.name)

        # Image output directory
        img_dir = IMAGES_DIR / f"{year}_{session}" / pdf_path.stem

        extracted = extract_pdf(pdf_path, img_dir)

        entry = {
            "filename": pdf_path.name,
            "type": info["type"],
            "language": info["language"],
            "total_pages": extracted["total_pages"],
            "text_pages": extracted["text_pages"],
            "image_pages": extracted["image_pages"],
            "pages": extracted["pages"],
        }

        # Parse individual questions where possible
        if info["type"] == "question" and info["language"] == "ja":
            if info["subject"] == "japan_and_world":
                parsed = parse_questions_jw(extracted["pages"])
                if parsed:
                    entry["parsed_questions"] = parsed
            elif info["subject"] == "science":
                parsed = parse_questions_science(extracted["pages"])
                if parsed:
                    entry["parsed_questions"] = parsed

        subject = info["subject"]
        if subject not in session_data["subjects"]:
            session_data["subjects"][subject] = {
                "subject": subject,
                "subject_ja": info.get("subject_ja", ""),
                "files": []
            }
        session_data["subjects"][subject]["files"].append(entry)

    return session_data


def build_database():
    """Build the complete study database."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    all_sessions = []
    session_dirs = sorted(JASSO_DIR.iterdir())

    for session_dir in session_dirs:
        if not session_dir.is_dir():
            continue
        session_data = process_session(session_dir)
        if session_data:
            all_sessions.append(session_data)

            # Write individual session JSON
            session_file = OUTPUT_DIR / f"{session_data['year']}_{session_data['session']}.json"
            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(session_data, f, ensure_ascii=False, indent=2)

    # Write master index
    index = {
        "exam": "日本留学試験（EJU）",
        "exam_en": "Examination for Japanese University Admission for International Students",
        "source": "JASSO (Japan Student Services Organization)",
        "total_sessions": len(all_sessions),
        "year_range": f"{all_sessions[0]['year']}-{all_sessions[-1]['year']}",
        "subjects": {
            "japanese": {"ja": "日本語", "sections": ["記述(Writing)", "読解(Reading)", "聴読解(Listening-Reading)", "聴解(Listening)"]},
            "math": {"ja": "数学", "sections": ["コース1(Basic)", "コース2(Advanced)"]},
            "science": {"ja": "理科", "sections": ["物理(Physics)", "化学(Chemistry)", "生物(Biology)"]},
            "japan_and_world": {"ja": "総合科目", "sections": ["政治・経済・社会", "地理", "歴史"]},
        },
        "sessions": []
    }

    total_text_pages = 0
    total_image_pages = 0
    total_parsed_questions = 0

    for s in all_sessions:
        subjects_summary = {}
        for subj_key, subj_data in s["subjects"].items():
            q_files = [f for f in subj_data["files"] if f["type"] == "question"]
            tp = sum(f["text_pages"] for f in subj_data["files"])
            ip = sum(f["image_pages"] for f in subj_data["files"])
            pq = sum(len(f.get("parsed_questions", [])) for f in subj_data["files"])
            total_text_pages += tp
            total_image_pages += ip
            total_parsed_questions += pq
            subjects_summary[subj_key] = {
                "subject_ja": subj_data["subject_ja"],
                "question_files": len(q_files),
                "text_pages": tp,
                "image_pages": ip,
                "parsed_questions": pq,
                "languages": sorted(set(f["language"] for f in q_files)) if q_files else []
            }

        index["sessions"].append({
            "year": s["year"],
            "session": s["session"],
            "session_label": s["session_label"],
            "exam_name": s["exam_name"],
            "file": f"{s['year']}_{s['session']}.json",
            "subjects": subjects_summary,
            "total_files": sum(len(sd["files"]) for sd in s["subjects"].values())
        })

    with open(OUTPUT_DIR / "index.json", "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    # Write combined database
    combined = {
        "exam": "日本留学試験（EJU）",
        "source": "JASSO",
        "sessions": all_sessions
    }
    with open(OUTPUT_DIR / "eju_all.json", "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)

    # Print summary
    print("\n" + "=" * 60)
    print("Conversion Summary")
    print("=" * 60)
    print(f"Sessions: {len(all_sessions)}")
    print(f"Text pages extracted: {total_text_pages}")
    print(f"Image pages rendered: {total_image_pages}")
    print(f"Parsed questions: {total_parsed_questions}")
    print(f"\nOutput:")
    print(f"  {OUTPUT_DIR}/index.json")
    print(f"  {OUTPUT_DIR}/eju_all.json")

    for s in all_sessions:
        subjects = []
        for subj_key, subj_data in s["subjects"].items():
            tp = sum(f["text_pages"] for f in subj_data["files"])
            ip = sum(f["image_pages"] for f in subj_data["files"])
            pq = sum(len(f.get("parsed_questions", [])) for f in subj_data["files"])
            label = f"{subj_key}({tp}t/{ip}i"
            if pq:
                label += f"/{pq}q"
            label += ")"
            subjects.append(label)
        total_files = sum(len(sd["files"]) for sd in s["subjects"].values())
        print(f"  {s['year']}_{s['session']}.json — {total_files} files — {', '.join(subjects)}")

    all_file = OUTPUT_DIR / "eju_all.json"
    size_mb = all_file.stat().st_size / (1024 * 1024)
    print(f"\neju_all.json: {size_mb:.1f} MB")

    # Count images
    img_count = sum(1 for _ in IMAGES_DIR.rglob("*.png"))
    img_size = sum(f.stat().st_size for f in IMAGES_DIR.rglob("*.png"))
    print(f"Images: {img_count} PNGs, {img_size / (1024*1024):.0f} MB")


if __name__ == "__main__":
    build_database()
