#!/usr/bin/env python3
"""Extract Japanese (日本語) reading questions from EJU PDFs.

Only 読解 (Reading) can be reliably extracted from PDFs.
聴読解 and 聴解 require audio.

Reading section structure:
- Sections I-X: single questions, each = 1 answer row
- Sections XI-XVII: passages with 問1, 問2 (sometimes 問3) sub-questions
  Each sub-question = 1 answer row

Answer row numbers = section number for I-X.
For XI+: row = 10 + (sum of sub-questions in previous XI+ sections) + sub-question index.
"""

import json
import re
from pathlib import Path
from collections import Counter

import fitz

BASE_DIR = Path(__file__).parent
JSON_DIR = BASE_DIR / "json"
JASSO_DIR = BASE_DIR / "jasso"


def roman_to_int(s: str) -> int | None:
    """Convert Roman numeral string to integer."""
    mapping = {
        "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5,
        "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
        "XI": 11, "XII": 12, "XIII": 13, "XIV": 14, "XV": 15,
        "XVI": 16, "XVII": 17, "XVIII": 18,
        "Ⅰ": 1, "Ⅱ": 2, "Ⅲ": 3, "Ⅳ": 4, "Ⅴ": 5,
        "Ⅵ": 6, "Ⅶ": 7, "Ⅷ": 8, "Ⅸ": 9, "Ⅹ": 10,
    }
    return mapping.get(s)


def detect_roman_numeral(line: str) -> int | None:
    """Detect Roman numeral at the start of a line."""
    line = line.strip()
    # Standalone Roman numeral
    m = re.match(r"^([IⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩXVvi]{1,6})\s*$", line)
    if m:
        return roman_to_int(m.group(1))
    # Roman numeral followed by text
    m = re.match(r"^([IⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩXVvi]{1,6})\s+", line)
    if m:
        return roman_to_int(m.group(1))
    return None


def extract_choices(text: str) -> list[str]:
    """Extract MC choices from page text."""
    choices = []

    # Pattern 1: "N．choice text" (full-width dot)
    for m in re.finditer(r"^(\d)\s*[．.]\s*(.+?)$", text, re.MULTILINE):
        num = int(m.group(1))
        if 1 <= num <= 6:
            choices.append(m.group(2).strip())

    if len(choices) >= 3:
        return choices[:4]

    # Pattern 2: "N choice text" (space separated, choice > 5 chars)
    choices = []
    for m in re.finditer(r"^(\d)\s+(.{5,})$", text, re.MULTILINE):
        num = int(m.group(1))
        if 1 <= num <= 6:
            choices.append(m.group(2).strip())

    return choices[:4]


def extract_reading_questions(doc, year: int, session: int) -> list[dict]:
    """Extract 読解 questions from a Japanese PDF.

    Returns questions with question_number = answer row number.
    """
    questions = []

    # Find reading section boundaries
    reading_start = None
    reading_end = None

    for i in range(doc.page_count):
        text = doc[i].get_text()
        if reading_start is None:
            # Reading starts with Roman numeral I section
            lines = text.strip().split("\n")
            for line in lines[:3]:
                if detect_roman_numeral(line) == 1:
                    reading_start = i
                    break
        if reading_start is not None and "聴読解" in text:
            reading_end = i
            break

    if reading_start is None:
        # Fallback: look in TOC
        for i in range(min(2, doc.page_count)):
            text = doc[i].get_text()
            m = re.search(r"読解\s*(\d+)", text)
            if m:
                target_page = int(m.group(1))
                # PDF page numbers don't always match
                reading_start = max(3, target_page - 2)
                break

    if reading_start is None:
        return []

    if reading_end is None:
        reading_end = doc.page_count

    # Parse reading section page by page
    current_section = 0
    section_sub_count = {}  # section_num -> number of sub-questions found

    for page_idx in range(reading_start, reading_end):
        text = doc[page_idx].get_text()
        if not text.strip():
            continue

        lines = text.strip().split("\n")

        # Detect section (Roman numeral)
        for line in lines[:5]:
            sec = detect_roman_numeral(line)
            if sec is not None:
                current_section = sec
                break

        if current_section == 0:
            continue

        # Skip non-question pages (instruction pages, etc.)
        if not re.search(r"次の|どれですか|どのように|どういう|筆者|合っている|問\d", text):
            continue

        # Extract choices
        choices = extract_choices(text)
        if len(choices) < 2:
            continue

        # Extract question prompt
        prompt = ""
        for line in lines:
            ls = line.strip()
            if re.search(r"次の|どれですか|どのように|どういう|筆者は|合っている|下線部", ls):
                prompt = ls
                break

        # Extract passage
        passage_lines = []
        in_passage = False
        for line in lines:
            ls = line.strip()
            if prompt and ls.startswith(prompt[:10]):
                in_passage = True
                continue
            if in_passage:
                if re.match(r"^\d\s*[．.]", ls) or re.match(r"^\d\s+.{5,}", ls):
                    break
                if ls and not re.match(r"^[ⓒ©]", ls):
                    passage_lines.append(ls)

        passage = "\n".join(passage_lines[:30])

        # Detect sub-questions (問1, 問2) for sections XI+
        sub_qs = list(re.finditer(r"問\s*(\d+)", text))
        has_sub = current_section >= 11 and len(sub_qs) > 0

        if has_sub:
            # This page has sub-questions within a section
            for sq in sub_qs:
                sub_num = int(sq.group(1))
                # Only add if we have unique choices nearby
                # (simplification: add each sub-question we find)
                if current_section not in section_sub_count:
                    section_sub_count[current_section] = 0
                section_sub_count[current_section] = max(
                    section_sub_count[current_section], sub_num
                )

            # Add one question per sub-question found on this page
            # Find the actual sub-question being asked
            sub_num = max(int(sq.group(1)) for sq in sub_qs)
            row = compute_row(current_section, sub_num)

            q = {
                "year": year,
                "session": session,
                "subject": "japanese",
                "subject_detail": "読解",
                "question_number": row,
                "question_text": prompt,
                "passage": passage,
                "choices": choices,
                "source": "text",
            }
            questions.append(q)
        else:
            # Simple single-question section (I-X)
            # Row = section number
            row = current_section

            q = {
                "year": year,
                "session": session,
                "subject": "japanese",
                "subject_detail": "読解",
                "question_number": row,
                "question_text": prompt,
                "passage": passage,
                "choices": choices,
                "source": "text",
            }
            questions.append(q)

    return questions


def compute_row(section: int, sub_num: int) -> int:
    """Compute answer row number for a given section and sub-question.

    Standard EJU Japanese reading structure:
    - Sections I-X: rows 1-10 (1 question each)
    - Sections XI+: rows 11+ (2 sub-questions each typically)
    - XI → rows 11, 12
    - XII → rows 13, 14
    - XIII → rows 15, 16
    - XIV → rows 17, 18
    - XV → rows 19, 20
    - XVI → rows 21, 22 (sometimes 23)
    - XVII → rows 23, 24, 25
    """
    if section <= 10:
        return section

    # For sections 11+, each section typically has 2 sub-questions
    # Base row for section 11 = 11
    base = 10 + (section - 11) * 2 + 1  # 11 for XI, 13 for XII, etc.
    return base + sub_num - 1


def extract_all_japanese():
    """Extract Japanese questions from all available PDFs."""
    all_questions = []

    for session_dir in sorted(JASSO_DIR.iterdir()):
        if not session_dir.is_dir():
            continue
        m = re.match(r"(\d{4})_第(\d+)回", session_dir.name)
        if not m:
            continue
        year, session = int(m.group(1)), int(m.group(2))

        # Find Japanese question PDF
        ja_pdfs = []
        for pdf in sorted(session_dir.glob("*question_jafl*")):
            if pdf.name.startswith("._"):
                continue
            # Skip English versions
            fn = pdf.name.lower()
            if "_e" in fn and "rev" not in fn:
                continue
            ja_pdfs.append(pdf)

        if not ja_pdfs:
            continue

        pdf_path = ja_pdfs[0]

        try:
            doc = fitz.open(str(pdf_path))
            total_text = sum(len(doc[i].get_text().strip()) for i in range(min(5, doc.page_count)))
            if total_text < 200:
                print(f"  {year}_{session}: Image-only PDF, skip")
                doc.close()
                continue

            questions = extract_reading_questions(doc, year, session)
            doc.close()

            if questions:
                all_questions.extend(questions)
                print(f"  {year}_{session}: {len(questions)} reading questions")
            else:
                print(f"  {year}_{session}: No questions extracted")

        except Exception as e:
            print(f"  {year}_{session}: Error — {e}")

    return all_questions


def merge_and_match(new_questions: list[dict]):
    """Merge new Japanese questions and match all answers."""
    with open(JSON_DIR / "questions.json") as f:
        data = json.load(f)

    # Remove old Japanese 読解 text-extracted questions
    kept = [q for q in data["questions"]
            if not (q.get("subject") == "japanese" and
                    q.get("subject_detail") == "読解" and
                    q.get("source") == "text")]

    # Add new
    kept.extend(new_questions)
    data["questions"] = kept

    # Now match answers
    with open(JSON_DIR / "answer_keys.json") as f:
        answers = json.load(f)

    ja_matched = 0
    for q in data["questions"]:
        if q.get("subject") != "japanese" or q.get("subject_detail") != "読解":
            continue

        key = f"{q['year']}_{q['session']}"
        if key not in answers or "ja_reading" not in answers[key]:
            continue

        row = q.get("question_number")
        ja_ans = answers[key]["ja_reading"]
        if row is not None:
            # Keys might be strings or ints
            if row in ja_ans:
                q["correct_answer"] = ja_ans[row]
                ja_matched += 1
            elif str(row) in ja_ans:
                q["correct_answer"] = ja_ans[str(row)]
                ja_matched += 1

    with open(JSON_DIR / "questions.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n  Japanese reading questions matched: {ja_matched}")
    print(f"  Total questions: {len(data['questions'])}")
    by_subj = Counter(q.get("subject") for q in data["questions"])
    for s, c in sorted(by_subj.items()):
        print(f"    {s}: {c}")


if __name__ == "__main__":
    print("Extracting Japanese reading questions...")
    questions = extract_all_japanese()
    print(f"\n  Extracted {len(questions)} total")
    print("\nMerging and matching answers...")
    merge_and_match(questions)
