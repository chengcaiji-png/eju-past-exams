#!/usr/bin/env python3
"""Extract ALL structured questions from EJU past exam PDFs.

Reads from json/ session files, outputs json/questions.json
"""

import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).parent
JSON_DIR = BASE_DIR / "json"

COPYRIGHT_RE = re.compile(r"\s*ⓒ?\s*©?\s*\d{4}\s*Japan Student Services\s*O?\s*r?ganization\s*", re.I)
PAGE_MARKER_RE = re.compile(r"\n?\s*(?:理科|総合科目|数学|日本語)－\d+\s*\n?")


def clean(text: str) -> str:
    text = COPYRIGHT_RE.sub("", text)
    text = PAGE_MARKER_RE.sub("\n", text)
    return text.strip()


def get_full_text(file_entry: dict) -> str:
    parts = []
    for p in file_entry.get("pages", []):
        if p.get("has_text") and p.get("text"):
            parts.append(p["text"])
    return "\n\n".join(parts)


def get_page_texts(file_entry: dict) -> list[tuple[int, str]]:
    """Return list of (page_number, text) for pages with content."""
    result = []
    for p in file_entry.get("pages", []):
        if p.get("has_text") and p.get("text"):
            result.append((p["page"], p["text"]))
    return result


def is_garbled(text: str) -> bool:
    """Check if text is mostly mojibake."""
    if not text:
        return True
    greek = len(re.findall(r"[ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩαβγδεζηθικλμνξοπρστυφχψωΆΈΉΊΌΎΏ]", text))
    return greek / max(len(text), 1) > 0.03


# ────────────── 総合科目 ──────────────

def parse_jw(text: str) -> list[dict]:
    text = clean(text)
    questions = []

    # Split by 問+number (full-width or half-width)
    q_splits = re.split(r"(問\s*(?:\d+|[１-９][０-９]?))\s*\n?", text)

    blocks = []
    for i in range(len(q_splits)):
        m = re.match(r"問\s*(\d+|[１-９][０-９]?)", q_splits[i])
        if m:
            num_str = m.group(1)
            if num_str.isdigit():
                num = int(num_str)
            else:
                num = int(num_str.translate(str.maketrans("１２３４５６７８９０", "1234567890")))
            content = q_splits[i + 1] if i + 1 < len(q_splits) else ""
            blocks.append((num, content))

    # Filter out 問題 false matches (instruction text)
    blocks = [(n, t) for n, t in blocks if "問題冊子" not in t[:50] and len(t) > 30]

    for q_num, block_text in blocks:
        # Extract passage before first sub-question
        passage = ""
        if "⑴" in block_text:
            passage = clean(block_text[:block_text.index("⑴")])

        # Split sub-questions
        sub_markers = "⑴⑵⑶⑷⑸⑹⑺⑻"
        sub_splits = re.split(r"([⑴⑵⑶⑷⑸⑹⑺⑻])", block_text)

        subs = []
        for j in range(1, len(sub_splits), 2):
            marker = sub_splits[j]
            content = sub_splits[j + 1] if j + 1 < len(sub_splits) else ""
            sub_num = sub_markers.index(marker) + 1
            subs.append((sub_num, content))

        if subs:
            for sub_num, sub_text in subs:
                choices = _extract_circled_choices(sub_text)
                q_text = sub_text
                if "①" in q_text:
                    q_text = q_text[:q_text.index("①")]
                q_text = clean(q_text)

                q = {
                    "question_id": f"Q{q_num}-{sub_num}",
                    "text": q_text,
                    "choices": choices,
                }
                if sub_num == 1 and passage:
                    q["passage"] = passage
                questions.append(q)
        else:
            # No sub-questions - direct choices
            choices = _extract_circled_choices(block_text)
            q_text = block_text
            if "①" in q_text:
                q_text = q_text[:q_text.index("①")]
            q_text = clean(q_text)
            if q_text or choices:
                questions.append({
                    "question_id": f"Q{q_num}",
                    "text": q_text,
                    "choices": choices,
                })

    return questions


def _extract_circled_choices(text: str) -> list[dict]:
    splits = re.split(r"([①②③④⑤⑥⑦⑧])", text)
    choices = []
    for i in range(1, len(splits), 2):
        label = splits[i]
        content = splits[i + 1].strip() if i + 1 < len(splits) else ""
        content = clean(content)
        content = re.split(r"\n\s*[⑴⑵⑶⑷]", content)[0].strip()
        if content:
            choices.append({"label": label, "text": content})
    return choices


# ────────────── 理科 ──────────────

def parse_science(text: str) -> list[dict]:
    """Parse science by splitting on 問 + 1⃝ choice blocks.

    The 問 marker may be followed by control chars (\x14-\x19) or spaces.
    Pattern: setup text → 問[\x00-\x1f]* → question stem → 1⃝ choices
    """
    text = clean(text)
    questions = []

    # Determine section boundaries using end markers
    # Science order: 物理 → 化学 → 生物
    section_boundaries = [(0, "物理")]
    physics_end = re.search(r"物理の問題はこれで終わり", text)
    if physics_end:
        section_boundaries.append((physics_end.end(), "化学"))
        # Look for chemistry end (might be garbled, so use approximate position)
        # Chemistry typically has ~7 questions, biology ~6
        # Just look for next section end marker pattern
        chem_end = re.search(r"化学の問題はこれで終わり|の問題はこれで終わり", text[physics_end.end():])
        if chem_end:
            section_boundaries.append((physics_end.end() + chem_end.end(), "生物"))

    def get_section(pos: int) -> str:
        section = "unknown"
        for bp, name in section_boundaries:
            if pos >= bp:
                section = name
        return section

    # Normalize choice markers: N\x1f → N⃝
    text = re.sub(r"(\d)\x1f", r"\1⃝", text)

    # Split by 問 markers: 問 optionally followed by control chars, then space/newline
    # Handles: 問\x14 　text..., 問\ntext..., 問 text...
    parts = re.split(r"(問[\x00-\x1f]*[\s\u3000]+)", text)

    q_count = 0
    for i in range(1, len(parts), 2):
        marker = parts[i]
        if i + 1 >= len(parts):
            break
        q_block = parts[i + 1]

        # Skip instruction text (問題冊子, 問い$)
        if "問題" in marker or "問い" in (parts[i - 1][-5:] if i > 0 else ""):
            continue

        # Must look like a real question (has choices or "選びなさい")
        has_choices = "1⃝" in q_block or "①" in q_block
        has_instruction = "選びなさい" in q_block or "選べ" in q_block or "答えよ" in q_block
        if not has_choices and not has_instruction:
            continue

        # Get setup from previous block
        setup = parts[i - 1] if i > 0 else ""
        setup = clean(setup)
        # Trim setup to just the current problem (after last choice block)
        last_choice = max(setup.rfind("⃝"), setup.rfind("①"), setup.rfind("②"))
        if last_choice > 0:
            # Find the next newline after the last choice
            nl = setup.find("\n", last_choice)
            if nl > 0:
                setup = setup[nl:].strip()

        # Determine section
        pos_in_text = sum(len(parts[j]) for j in range(i))
        section = get_section(pos_in_text)

        # Extract choices
        choices = _extract_number_circle_choices(q_block)
        if not choices:
            choices = _extract_circled_choices(q_block)

        q_text = q_block
        if "1⃝" in q_text:
            q_text = q_text[:q_text.index("1⃝")]
        elif "①" in q_text:
            q_text = q_text[:q_text.index("①")]
        q_text = clean(q_text)

        if choices or q_text:
            q_count += 1
            q = {
                "section": section,
                "text": q_text,
                "choices": choices if choices else [],
            }
            if setup and len(setup) > 20:
                q["setup"] = setup[-500:]
            questions.append(q)

    return questions


def _extract_number_circle_choices(text: str) -> list[dict]:
    """Extract choices in formats: 1⃝, 1\x1f (unit separator), or plain N. patterns."""
    # Normalize: replace N\x1f with N⃝
    normalized = re.sub(r"(\d)\x1f", r"\1⃝", text)

    splits = re.split(r"([1-9]⃝)", normalized)
    choices = []
    for i in range(1, len(splits), 2):
        label = splits[i]
        content = splits[i + 1].strip() if i + 1 < len(splits) else ""
        # Take first meaningful line
        lines = content.split("\n")
        content = lines[0].strip() if lines else ""
        content = clean(content)
        if content:
            choices.append({"label": label, "text": content})
    return choices


# ────────────── 数学 ──────────────

def parse_math(text: str) -> list[dict]:
    text = clean(text)
    if is_garbled(text):
        return []

    questions = []

    # Detect course sections
    courses = []
    if "コース1" in text or "コース 1" in text:
        c2_match = re.search(r"コース\s*2", text)
        if c2_match:
            courses.append(("course1", text[:c2_match.start()]))
            courses.append(("course2", text[c2_match.start():]))
        else:
            courses.append(("course1", text))
    else:
        courses.append(("", text))

    for course_label, course_text in courses:
        # Split by 問N
        q_splits = re.split(r"(問\s*\d+)", course_text)

        for i in range(len(q_splits)):
            m = re.match(r"問\s*(\d+)", q_splits[i])
            if not m:
                continue
            q_num = int(m.group(1))
            content = q_splits[i + 1] if i + 1 < len(q_splits) else ""
            content = clean(content)

            if len(content) < 20 or is_garbled(content):
                continue

            # Remove memo/calculation space markers
            content = re.split(r"計算欄|memo", content)[0].strip()

            # Extract sub-parts
            sub_parts = []
            part_splits = re.split(r"\n\((\d+)\)\s*\n?", content)
            if len(part_splits) > 2:
                main_text = clean(part_splits[0])
                for j in range(1, len(part_splits), 2):
                    p_num = int(part_splits[j])
                    p_text = clean(part_splits[j + 1]) if j + 1 < len(part_splits) else ""
                    if p_text:
                        sub_parts.append({"part": p_num, "text": p_text})
            else:
                main_text = content

            # Check for selection choices (0⃝-9⃝)
            choices = []
            choice_splits = re.split(r"([0-9]⃝)", content)
            if len(choice_splits) > 4:
                for j in range(1, len(choice_splits), 2):
                    label = choice_splits[j]
                    c_text = choice_splits[j + 1].strip() if j + 1 < len(choice_splits) else ""
                    c_text = c_text.split("\n")[0].strip()
                    c_text = clean(c_text)
                    if c_text:
                        choices.append({"label": label, "text": c_text})

            # Extract blank markers
            blanks = sorted(set(re.findall(r"\b[A-Z]{1,3}\b", content)))

            q = {
                "question_id": f"Q{q_num}",
                "course": course_label,
                "type": "fill_in_blank",
                "text": main_text,
            }
            if sub_parts:
                q["sub_parts"] = sub_parts
            if blanks:
                q["blanks"] = blanks
            if choices:
                q["choices"] = choices
            questions.append(q)

    return questions


# ────────────── 日本語 ──────────────

def parse_japanese(text: str) -> list[dict]:
    text = clean(text)
    questions = []

    # 1. 記述 (Writing themes)
    writing_match = re.search(r"記述問題(.+?)(?=読解|聴読解|$)", text, re.DOTALL)
    if writing_match:
        writing_text = writing_match.group(1)
        themes = re.split(r"[①②]", writing_text)
        for i, theme in enumerate(themes[1:], 1):  # Skip text before ①
            theme = clean(theme)
            if theme and len(theme) > 20:
                questions.append({
                    "section": "記述",
                    "question_id": f"Writing-{i}",
                    "type": "essay",
                    "text": theme[:500],
                })

    # 2. 読解 (Reading comprehension)
    reading_match = re.search(r"(?:読\s*解|Ｉ\s*\n)(.*?)(?=聴読解|聴\s*解|$)", text, re.DOTALL)
    if reading_match:
        reading_text = reading_match.group(1)
        _parse_japanese_reading(reading_text, questions)

    # 3. 聴読解 (Listening-reading)
    lr_match = re.search(r"聴読解(.*?)(?=聴\s*解\s*\n|$)", text, re.DOTALL)
    if lr_match:
        lr_text = lr_match.group(1)
        _parse_japanese_listening_reading(lr_text, questions)

    return questions


def _parse_japanese_reading(text: str, questions: list):
    """Parse 読解 section. Format varies but typically:
    - Passage (I, II etc.) followed by questions
    - Questions: numbered (1-25) with 4 choices each
    """
    text = clean(text)

    # Find passages with questions
    # Pattern: passage text → question (e.g., "筆者は...と言っているか") → choices 1. 2. 3. 4.
    # Or: "次の文章で..." introducing a question about a passage

    # Split by Roman numeral passage markers or 次の文章/次の～
    passage_splits = re.split(r"\n\s*((?:I{1,3}|IV|V|Ⅰ|Ⅱ|Ⅲ|Ⅳ|Ⅴ)\s*\n)", text)

    # Simpler approach: find all numbered questions (N．or N.)
    # These appear as standalone questions with 4 choices
    blocks = re.split(r"\n\s*(\d{1,2})\s*[．.]\s*", text)

    for i in range(1, len(blocks), 2):
        q_num = int(blocks[i])
        q_content = blocks[i + 1] if i + 1 < len(blocks) else ""

        if len(q_content.strip()) < 10:
            continue

        # Extract 4 choices (1. 2. 3. 4.)
        choices = []
        choice_splits = re.split(r"\n\s*([1-4])\s*[．.]\s*", q_content)

        if len(choice_splits) >= 3:
            q_text = clean(choice_splits[0])
            for j in range(1, len(choice_splits), 2):
                label = choice_splits[j]
                c_text = choice_splits[j + 1].strip() if j + 1 < len(choice_splits) else ""
                c_text = c_text.split("\n")[0].strip()
                c_text = clean(c_text)
                if c_text:
                    choices.append({"label": label, "text": c_text})
        else:
            q_text = clean(q_content[:200])

        if q_text:
            questions.append({
                "section": "読解",
                "question_id": f"Reading-{q_num}",
                "type": "multiple_choice",
                "text": q_text,
                "choices": choices,
            })


def _parse_japanese_listening_reading(text: str, questions: list):
    """Parse 聴読解 questions — typically have images/charts, less text."""
    # Usually questions 26-39 or similar
    blocks = re.split(r"\n\s*(\d{1,2})\s*[．.]\s*", clean(text))
    for i in range(1, len(blocks), 2):
        q_num = int(blocks[i])
        q_content = blocks[i + 1] if i + 1 < len(blocks) else ""
        if len(q_content.strip()) < 5:
            continue

        choices = []
        choice_splits = re.split(r"\n\s*([1-4])\s*[．.]\s*", q_content)
        if len(choice_splits) >= 3:
            q_text = clean(choice_splits[0])
            for j in range(1, len(choice_splits), 2):
                label = choice_splits[j]
                c_text = choice_splits[j + 1].strip().split("\n")[0].strip()
                c_text = clean(c_text)
                if c_text:
                    choices.append({"label": label, "text": c_text})
        else:
            q_text = clean(q_content[:200])

        questions.append({
            "section": "聴読解",
            "question_id": f"ListeningReading-{q_num}",
            "type": "multiple_choice",
            "text": q_text,
            "choices": choices,
        })


# ────────────── Image-only files tracking ──────────────

def collect_image_only(file_entry: dict) -> list[dict]:
    """For PDFs where text extraction fails, create image-reference entries."""
    results = []
    for p in file_entry.get("pages", []):
        if p.get("image_path") and not p.get("has_text"):
            if p["page"] > 2:  # Skip cover pages
                results.append({
                    "type": "image_only",
                    "image_path": p["image_path"],
                    "page": p["page"],
                })
    return results


# ────────────── Main ──────────────

def process_all():
    all_questions = []
    image_only_pages = []

    session_files = sorted(JSON_DIR.glob("[0-9]*_[0-9]*.json"))

    for sf in session_files:
        with open(sf) as f:
            session = json.load(f)

        year = session["year"]
        session_num = session["session"]
        session_label = session["session_label"]

        for subj_key, subj_data in session["subjects"].items():
            if subj_key == "all":
                continue

            # Track which question_ids we've seen for this session+subject (dedup _1 files)
            seen_qids = set()

            for file_entry in subj_data["files"]:
                if file_entry["type"] != "question":
                    continue
                if file_entry["language"] != "ja":
                    continue

                full_text = get_full_text(file_entry)

                # Skip entirely garbled files
                if is_garbled(full_text):
                    # Collect image references instead
                    for img_ref in collect_image_only(file_entry):
                        img_ref.update({
                            "year": year, "session": session_num,
                            "session_label": session_label,
                            "subject": subj_key,
                            "subject_ja": subj_data["subject_ja"],
                            "source_file": file_entry["filename"],
                        })
                        image_only_pages.append(img_ref)
                    continue

                if not full_text or len(full_text) < 50:
                    continue

                # Parse
                parsed = []
                if subj_key == "japan_and_world":
                    parsed = parse_jw(full_text)
                elif subj_key == "science":
                    parsed = parse_science(full_text)
                elif subj_key == "math":
                    parsed = parse_math(full_text)
                elif subj_key == "japanese":
                    parsed = parse_japanese(full_text)

                for q in parsed:
                    q["year"] = year
                    q["session"] = session_num
                    q["session_label"] = session_label
                    q["subject"] = subj_key
                    q["subject_ja"] = subj_data["subject_ja"]
                    q["source_file"] = file_entry["filename"]

                    # Dedup across _1 duplicate files
                    dedup_key = f"{year}_{session_num}_{subj_key}_{q.get('question_id', '')}_{q.get('section', '')}_{q.get('text', '')[:60]}"
                    if dedup_key not in seen_qids:
                        seen_qids.add(dedup_key)
                        all_questions.append(q)

    # Assign global IDs
    for i, q in enumerate(all_questions):
        q["id"] = i + 1

    # Stats
    stats = {}
    for q in all_questions:
        subj = q["subject"]
        stats[subj] = stats.get(subj, 0) + 1

    # Per-session stats
    year_stats = {}
    for q in all_questions:
        key = f"{q['year']}_{q['session']}"
        year_stats.setdefault(key, {})
        subj = q["subject"]
        year_stats[key][subj] = year_stats[key].get(subj, 0) + 1

    # Write questions.json
    output = {
        "exam": "日本留学試験（EJU）",
        "total_questions": len(all_questions),
        "by_subject": stats,
        "questions": all_questions,
    }
    out_path = JSON_DIR / "questions.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Write image-only references
    img_out = {
        "description": "Pages where text extraction failed - use PNG images",
        "total_pages": len(image_only_pages),
        "pages": image_only_pages,
    }
    img_path = JSON_DIR / "image_only_pages.json"
    with open(img_path, "w", encoding="utf-8") as f:
        json.dump(img_out, f, ensure_ascii=False, indent=2)

    # Print results
    print(f"Extracted {len(all_questions)} questions -> {out_path}")
    print(f"Image-only pages: {len(image_only_pages)} -> {img_path}")
    print(f"\nBy subject: {json.dumps(stats, ensure_ascii=False)}")

    print("\nPer session:")
    for key in sorted(year_stats.keys()):
        subjects = ", ".join(f"{k}:{v}" for k, v in sorted(year_stats[key].items()))
        total = sum(year_stats[key].values())
        print(f"  {key}: {total} questions ({subjects})")

    # Show sessions with 0 questions
    all_session_keys = set()
    for sf in session_files:
        name = sf.stem
        all_session_keys.add(name)
    missing = all_session_keys - set(year_stats.keys())
    if missing:
        print(f"\nSessions with 0 questions: {sorted(missing)}")

    print(f"\nFile size: {out_path.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    process_all()
