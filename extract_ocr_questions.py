#!/usr/bin/env python3
"""Extract structured questions from OCR results.

Parses ocr_results.json to produce structured question data.
Handles: math, science (physics/chemistry/biology), japan_and_world, japanese.
"""

import json
import re
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).parent
JSON_DIR = BASE_DIR / "json"


def load_ocr_results():
    with open(JSON_DIR / "ocr_results.json", encoding="utf-8") as f:
        return json.load(f)


def combine_pages_text(pages: list[dict]) -> str:
    """Combine all pages' text into one string with page markers."""
    parts = []
    for p in pages:
        if p["text"].strip():
            parts.append(f"[PAGE_{p['page']}]\n{p['text']}")
    return "\n\n".join(parts)


# ─── MATH PARSER ─────────────────────────────────────────────────
def parse_math(text: str, year: int, session: int) -> list[dict]:
    """Parse math questions. Structure: 問N followed by sub-parts with answer boxes."""
    questions = []

    # Split by 問N pattern
    q_splits = re.split(r"(問\s*(\d+))", text)

    i = 0
    while i < len(q_splits):
        # Find 問N marker
        m = re.match(r"問\s*(\d+)", q_splits[i])
        if m:
            q_num = int(m.group(1))
            # Get text until next 問 or end
            q_text = q_splits[i + 1] if i + 1 < len(q_splits) else ""
            # Find next 問 to delimit
            end_idx = i + 2
            while end_idx < len(q_splits):
                if re.match(r"問\s*\d+", q_splits[end_idx]):
                    break
                q_text += q_splits[end_idx]
                end_idx += 1

            # Clean up
            q_text = q_text.strip()
            # Remove page markers and footer
            q_text = re.sub(r"\[PAGE_\d+\]", "", q_text)
            q_text = re.sub(r"©?\s*\d{4}.*?Organization", "", q_text)
            q_text = re.sub(r"[Ss]ervices\s*[Oo]rganization", "", q_text)
            q_text = re.sub(r"nization\s*$", "", q_text, flags=re.MULTILINE)
            q_text = q_text.strip()

            if len(q_text) > 20:  # Minimum viable question
                questions.append({
                    "question_number": q_num,
                    "text": f"問{q_num} {q_text}",
                    "type": "fill_in_box",
                })
            i = end_idx
        else:
            i += 1

    return questions


# ─── SCIENCE PARSER ──────────────────────────────────────────────
def detect_science_sections(text: str) -> list[tuple[int, str]]:
    """Detect physics/chemistry/biology section boundaries."""
    sections = []

    # Look for section headers like 理科-2 (physics starts), numbered Roman numerals
    # Physics typically ends with "物理の問題はこれで終わり"
    # Chemistry ends with "化学の問題はこれで終わり"

    physics_end = None
    chem_end = None

    for m in re.finditer(r"物理の問題はこれで終わり", text):
        physics_end = m.start()
    for m in re.finditer(r"化学の問題はこれで終わり", text):
        chem_end = m.start()

    # If we find end markers, use them
    if physics_end is not None and chem_end is not None:
        sections = [
            (0, physics_end, "物理"),
            (physics_end, chem_end, "化学"),
            (chem_end, len(text), "生物"),
        ]
    elif physics_end is not None:
        sections = [
            (0, physics_end, "物理"),
            (physics_end, len(text), "化学"),  # Could be chem or bio
        ]
    else:
        # Try to detect by page numbers and 理科-N headers
        # Physics is typically pages 2-9, Chemistry 10-17, Biology 18-25
        page_sections = []
        for m in re.finditer(r"理科[ー一－-](\d+)", text):
            page_num = int(m.group(1))
            page_sections.append((m.start(), page_num))

        if page_sections:
            # Physics: 理科-2 to 理科-8/9
            # Chemistry: around 理科-10 to 理科-17
            # Biology: around 理科-18+
            for start_pos, page_num in page_sections:
                if page_num <= 9:
                    sections.append((start_pos, -1, "物理"))
                elif page_num <= 17:
                    sections.append((start_pos, -1, "化学"))
                else:
                    sections.append((start_pos, -1, "生物"))

    return sections


def parse_science(text: str, year: int, session: int) -> list[dict]:
    """Parse science questions with section detection.

    Section detection strategy:
    1. PHYSICS_END marker splits physics from rest
    2. CHEM_END marker (if present) splits chemistry from biology
    3. If no CHEM_END, look for '化学' or '生物' section headers
    4. Fall back to page number heuristic: first half after physics = chemistry, second = biology
    """
    questions = []

    # Normalize choice markers
    text = re.sub(r"(\d)\x1f", r"\1⃝", text)

    # Find section boundaries
    phys_end_pos = text.find("物理の問題はこれで終わり")
    chem_end_pos = text.find("化学の問題はこれで終わり")
    bio_end_pos = text.find("生物の問題はこれで終わり")

    # Look for explicit section headers (e.g., "化学\n" or "生物\n")
    chem_header_pos = -1
    bio_header_pos = -1
    for m in re.finditer(r"\n\s*化学\s*\n", text):
        if phys_end_pos < 0 or m.start() > phys_end_pos:
            chem_header_pos = m.start()
            break
    for m in re.finditer(r"\n\s*生物\s*\n", text):
        if phys_end_pos < 0 or m.start() > phys_end_pos:
            bio_header_pos = m.start()
            break

    # Determine chemistry/biology boundary
    # Priority: explicit end markers > section headers > page-based heuristic
    if chem_end_pos >= 0:
        chem_bio_boundary = chem_end_pos
    elif bio_header_pos >= 0:
        chem_bio_boundary = bio_header_pos
    elif phys_end_pos >= 0:
        # Heuristic: split remaining text roughly in half
        remaining = len(text) - phys_end_pos
        chem_bio_boundary = phys_end_pos + remaining // 2
    else:
        chem_bio_boundary = len(text) * 2 // 3

    # Split by 問N
    q_pattern = re.compile(r"問\s*(\d+)")
    matches = list(q_pattern.finditer(text))

    for idx, m in enumerate(matches):
        q_num = int(m.group(1))
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        q_text = text[start:end].strip()

        # Clean up
        q_text = re.sub(r"\[PAGE_\d+\]", "", q_text)
        q_text = re.sub(r"©?\s*\d{4}.*?Organization", "", q_text)
        q_text = re.sub(r"[Ss]ervices\s*[Oo]rganization", "", q_text)
        q_text = re.sub(r"nization\s*$", "", q_text, flags=re.MULTILINE)
        q_text = q_text.strip()

        # Extract choices (①②③④⑤⑥)
        choices = []
        choice_pattern = re.compile(r"([①②③④⑤⑥⑦⑧])\s*(.+?)(?=[①②③④⑤⑥⑦⑧]|$)", re.DOTALL)
        choice_matches = list(choice_pattern.finditer(q_text))
        if choice_matches:
            for cm in choice_matches:
                choices.append({
                    "marker": cm.group(1),
                    "text": cm.group(2).strip(),
                })

        # Determine section by position
        pos = m.start()
        if phys_end_pos >= 0 and pos < phys_end_pos:
            section = "物理"
        elif pos >= chem_bio_boundary:
            section = "生物"
        elif phys_end_pos >= 0:
            section = "化学"
        else:
            # No physics end marker — use page number heuristic
            section = "物理"
            before = text[max(0, pos - 1000):pos]
            page_nums = re.findall(r"理科[ー一－-](\d+)", before)
            if page_nums:
                last_page = int(page_nums[-1])
                if last_page >= 30:
                    section = "生物"
                elif last_page >= 20:
                    section = "化学"

        if len(q_text) > 10:
            questions.append({
                "question_number": q_num,
                "text": f"問{q_num} {q_text}",
                "type": "multiple_choice" if choices else "fill_in",
                "choices": choices if choices else None,
                "section": section,
            })

    return questions


# ─── JW (Japan and World) PARSER ────────────────────────────────
def parse_jw(text: str, year: int, session: int) -> list[dict]:
    """Parse Japan and World (総合科目) questions."""
    questions = []

    q_pattern = re.compile(r"問\s*(\d+)")
    matches = list(q_pattern.finditer(text))

    for idx, m in enumerate(matches):
        q_num = int(m.group(1))
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        q_text = text[start:end].strip()

        # Clean
        q_text = re.sub(r"\[PAGE_\d+\]", "", q_text)
        q_text = re.sub(r"©?\s*\d{4}.*?Organization", "", q_text)
        q_text = re.sub(r"[Ss]ervices\s*[Oo]rganization", "", q_text)
        q_text = re.sub(r"nization\s*$", "", q_text, flags=re.MULTILINE)
        q_text = q_text.strip()

        choices = []
        choice_pattern = re.compile(r"([①②③④])\s*(.+?)(?=[①②③④]|$)", re.DOTALL)
        for cm in choice_pattern.finditer(q_text):
            choices.append({
                "marker": cm.group(1),
                "text": cm.group(2).strip(),
            })

        if len(q_text) > 10:
            questions.append({
                "question_number": q_num,
                "text": f"問{q_num} {q_text}",
                "type": "multiple_choice" if choices else "fill_in",
                "choices": choices if choices else None,
            })

    return questions


# ─── JAPANESE PARSER ─────────────────────────────────────────────
def parse_japanese(text: str, year: int, session: int) -> list[dict]:
    """Parse Japanese (日本語) questions - mostly 読解 and 記述."""
    questions = []

    q_pattern = re.compile(r"問\s*(\d+)")
    matches = list(q_pattern.finditer(text))

    for idx, m in enumerate(matches):
        q_num = int(m.group(1))
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        q_text = text[start:end].strip()

        q_text = re.sub(r"\[PAGE_\d+\]", "", q_text)
        q_text = re.sub(r"©?\s*\d{4}.*?Organization", "", q_text)
        q_text = re.sub(r"[Ss]ervices\s*[Oo]rganization", "", q_text)
        q_text = q_text.strip()

        if len(q_text) > 10:
            questions.append({
                "question_number": q_num,
                "text": f"問{q_num} {q_text}",
                "type": "reading_comprehension",
            })

    return questions


# ─── MAIN ────────────────────────────────────────────────────────
PARSERS = {
    "math": parse_math,
    "science": parse_science,
    "japan_and_world": parse_jw,
    "japanese": parse_japanese,
}


def extract_all():
    data = load_ocr_results()
    all_questions = []
    stats = defaultdict(int)

    for result in data["results"]:
        year = result["year"]
        session = result["session"]
        subject = result["subject"]
        subject_ja = result["subject_ja"]
        dir_name = result["dir_name"]

        # Combine pages
        full_text = combine_pages_text(result["pages"])

        if not full_text.strip():
            continue

        parser = PARSERS.get(subject)
        if not parser:
            continue

        questions = parser(full_text, year, session)

        for q in questions:
            q["year"] = year
            q["session"] = session
            q["session_label"] = f"{year}_第{session}回"
            q["subject"] = subject
            q["subject_ja"] = subject_ja
            q["source"] = "ocr"
            q["source_dir"] = dir_name
            q["id"] = f"ocr_{year}_{session}_{subject}_{q['question_number']}"

            # Add section info for science
            if subject == "science" and "section" in q:
                q["subject_detail"] = q.pop("section")

        all_questions.extend(questions)
        stats[(year, session, subject)] += len(questions)

        if questions:
            print(f"  {dir_name}: {len(questions)} questions")

    # Sort
    all_questions.sort(key=lambda q: (q["year"], q["session"], q["subject"], q.get("question_number", 0)))

    # Summary
    print(f"\n{'='*60}")
    print(f"OCR Question Extraction Summary")
    print(f"{'='*60}")

    by_subject = defaultdict(int)
    by_year = defaultdict(int)
    for q in all_questions:
        by_subject[q["subject"]] += 1
        by_year[q["year"]] += 1

    print(f"Total: {len(all_questions)} questions")
    print(f"\nBy subject:")
    for s, c in sorted(by_subject.items(), key=lambda x: -x[1]):
        print(f"  {s}: {c}")
    print(f"\nBy year:")
    for y, c in sorted(by_year.items()):
        print(f"  {y}: {c}")

    # Save
    output = {
        "exam": "EJU",
        "source": "OCR (PaddleOCR v3.3.3)",
        "total_questions": len(all_questions),
        "by_subject": dict(by_subject),
        "questions": all_questions,
    }

    out_path = JSON_DIR / "ocr_questions.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\nSaved to {out_path} ({out_path.stat().st_size // 1024} KB)")

    # Also merge with existing questions
    merge_with_existing(all_questions)


def merge_with_existing(ocr_questions: list[dict]):
    """Merge OCR questions with existing text-extracted questions."""
    existing_path = JSON_DIR / "questions.json"
    if not existing_path.exists():
        return

    with open(existing_path, encoding="utf-8") as f:
        existing = json.load(f)

    existing_qs = existing["questions"]
    existing_ids = {q["id"] for q in existing_qs}

    # Add OCR questions that don't overlap with existing
    new_count = 0
    for q in ocr_questions:
        # Check for overlap by year/session/subject/question_number
        text_id = f"{q['year']}_{q['session']}_{q['subject']}_{q['question_number']}"
        ocr_id = q["id"]

        # Check if we already have this question from text extraction
        has_overlap = False
        for eq in existing_qs:
            eq_key = f"{eq.get('year','')}_{eq.get('session','')}_{eq.get('subject','')}_{eq.get('question_id','')}"
            if text_id == eq_key:
                has_overlap = True
                break

        if not has_overlap:
            existing_qs.append(q)
            new_count += 1

    # Sort
    existing_qs.sort(key=lambda q: (
        q.get("year", 0), q.get("session", 0),
        q.get("subject", ""), q.get("question_number", q.get("question_id", 0))
    ))

    existing["total_questions"] = len(existing_qs)
    existing["by_subject"] = {}
    for q in existing_qs:
        s = q.get("subject", "unknown")
        existing["by_subject"][s] = existing["by_subject"].get(s, 0) + 1

    with open(existing_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print(f"\nMerged: {new_count} new OCR questions added to questions.json")
    print(f"Total in questions.json: {existing['total_questions']}")


if __name__ == "__main__":
    extract_all()
