#!/usr/bin/env python3
"""Parse answer keys from OCR results with improved table parsing."""

import json
import re
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).parent
JSON_DIR = BASE_DIR / "json"


def parse_answer_page(text: str) -> dict:
    """Parse a single answer page's OCR text into structured answers.

    Returns dict like: {'physics': {1: 3, 2: 3, ...}, 'chemistry': {...}, ...}
    """
    answers = defaultdict(dict)

    # Detect which subjects are on this page
    has_science = bool(re.search(r"理\s*科|Science|物理|化学|生物", text))
    has_japanese = bool(re.search(r"日本語|Japanese|読解|聴読解|聴解", text))
    has_jw = bool(re.search(r"総合科目|Japan and the World", text))
    has_math = bool(re.search(r"(?<![理])数学|Mathematics", text))

    lines = text.split("\n")
    current_subject = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Detect subject/section headers
        if re.search(r"物理\s*(Physics)?", line) and "化学" not in line:
            current_subject = "physics"
        elif re.search(r"化学\s*(Chemistry)?", line) and "物理" not in line and "生物" not in line:
            current_subject = "chemistry"
        elif re.search(r"生物\s*(Biology)?", line) and "化学" not in line:
            current_subject = "biology"
        elif re.search(r"総合科目|Japan and the World", line):
            current_subject = "jw"
        elif "数学" in line and "理" not in line:
            current_subject = "math"
        elif "読解" in line and "聴" not in line:
            current_subject = "ja_reading"
        elif "聴読解" in line:
            current_subject = "ja_listening_reading"
        elif "聴解" in line and "聴読解" not in line:
            current_subject = "ja_listening"

        if not current_subject:
            # Try to detect from context for science pages with columns
            if has_science and not current_subject:
                current_subject = "physics"  # Will be split later
            continue

        # Parse answer entries
        # Pattern 1: 問N row_num answer (e.g., "問1 1 3")
        m = re.match(r"問\s*(\d+)\s+(\d+)\s+(\d+)", line)
        if m:
            q_num = int(m.group(1))
            row_num = int(m.group(2))
            answer = int(m.group(3))
            answers[current_subject][q_num] = answer
            continue

        # Pattern 2: Roman + row + answer (e.g., "I 1 3" or "Ⅱ 2 4")
        m = re.match(r"[IⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩXVIiv]+\s+(\d+)\s+(\d+)", line)
        if m and current_subject:
            row_num = int(m.group(1))
            answer = int(m.group(2))
            answers[current_subject][row_num] = answer
            continue

        # Pattern 3: N番 row answer (Japanese listening, e.g., "1番 1 4")
        m = re.match(r"(\d+)\s*番\s+(\d+)\s+(\d+)", line)
        if m:
            q_num = int(m.group(1))
            row_num = int(m.group(2))
            answer = int(m.group(3))
            answers[current_subject][q_num] = answer
            continue

        # Pattern 4: N番 row (then answer on same line but formatted weird)
        m = re.match(r"(\d+)\s*番\s+(\d+)\s*$", line)
        if m:
            # Row number only, answer might be missing or on next column
            continue

        # Pattern 5: Just "row answer" pairs (e.g., "13 2")
        m = re.match(r"^(\d{1,2})\s+(\d)$", line)
        if m and current_subject:
            row_or_q = int(m.group(1))
            answer = int(m.group(2))
            answers[current_subject][row_or_q] = answer
            continue

    return dict(answers)


def parse_science_answer_page(text: str) -> dict:
    """Special parser for science answer pages which have 3 columns."""
    answers = {"physics": {}, "chemistry": {}, "biology": {}}

    # The science page has 3 columns side by side:
    # Physics | Chemistry | Biology
    # Each column has: 問N, 解答番号(row), 正解(answer)

    # Strategy: Find all "問N row answer" patterns and assign to subjects
    # based on whether we've seen subject headers

    lines = text.split("\n")
    current_subject = "physics"
    seen_subjects = []

    for line in lines:
        line = line.strip()

        if re.search(r"物理", line) and "化学" not in line:
            current_subject = "physics"
            if "physics" not in seen_subjects:
                seen_subjects.append("physics")
        elif re.search(r"化学", line) and "物理" not in line:
            current_subject = "chemistry"
            if "chemistry" not in seen_subjects:
                seen_subjects.append("chemistry")
        elif re.search(r"生物", line) and "化学" not in line:
            current_subject = "biology"
            if "biology" not in seen_subjects:
                seen_subjects.append("biology")

        # Parse: 問N row answer
        m = re.match(r"問\s*(\d+)\s+(\d+)\s+(\d+)", line)
        if m:
            q_num = int(m.group(1))
            answer = int(m.group(3))
            answers[current_subject][q_num] = answer
            continue

        # Multiple entries on one line (OCR may concatenate columns)
        # e.g., "問1 1 3 問1 1 2 問1 1 5"
        multi = list(re.finditer(r"問\s*(\d+)\s+(\d+)\s+(\d+)", line))
        if len(multi) >= 2:
            subjects = ["physics", "chemistry", "biology"]
            for i, m in enumerate(multi):
                if i < len(subjects):
                    q_num = int(m.group(1))
                    answer = int(m.group(3))
                    answers[subjects[i]][q_num] = answer

    return answers


def parse_jw_math_answer_page(text: str, subject: str) -> dict:
    """Parse JW or math answer page."""
    answers = {}

    for m in re.finditer(r"問\s*(\d+)\s+(\d+)\s+(\d+)", text):
        q_num = int(m.group(1))
        answer = int(m.group(3))
        answers[q_num] = answer

    # Also try row-based: just "row answer"
    for line in text.split("\n"):
        line = line.strip()
        m = re.match(r"^(\d{1,2})\s+(\d)$", line)
        if m:
            row = int(m.group(1))
            answer = int(m.group(2))
            if row not in answers:
                answers[row] = answer

    return answers


def extract_all_answers():
    """Extract answers from all OCR'd answer pages."""
    ocr_path = JSON_DIR / "ocr_results.json"
    with open(ocr_path) as f:
        ocr_data = json.load(f)

    all_answers = {}  # (year, session) -> {subject: {q_num: answer}}

    for r in ocr_data["results"]:
        if r["subject"] != "answer":
            continue

        year, session = r["year"], r["session"]
        key = f"{year}_{session}"
        if key not in all_answers:
            all_answers[key] = {}

        dir_name = r["dir_name"].lower()

        # Combine all pages
        full_text = "\n".join(p["text"] for p in r["pages"] if p["text"])

        if not full_text.strip():
            continue

        # Determine what type of answer sheet this is
        if "writing" in dir_name:
            # Skip writing model answers for now
            continue

        # Parse based on content
        if re.search(r"理\s*科|物理|化学|生物", full_text):
            science_answers = parse_science_answer_page(full_text)
            for subj, ans in science_answers.items():
                if ans:
                    all_answers[key][subj] = ans

        if re.search(r"日本語|読解|聴", full_text):
            ja_answers = parse_answer_page(full_text)
            for subj, ans in ja_answers.items():
                if subj.startswith("ja_") and ans:
                    all_answers[key][subj] = ans

        if re.search(r"総合科目|Japan and the World", full_text):
            jw_text = full_text
            # Extract just the JW section
            jw_ans = parse_jw_math_answer_page(jw_text, "jw")
            if jw_ans:
                all_answers[key]["jw"] = jw_ans

        if re.search(r"(?<![理])数学|Mathematics", full_text):
            math_ans = parse_jw_math_answer_page(full_text, "math")
            if math_ans:
                all_answers[key]["math"] = math_ans

    # Also try direct PDF text extraction for answer files
    import fitz
    jasso_dir = BASE_DIR / "jasso"
    for session_dir in sorted(jasso_dir.iterdir()):
        if not session_dir.is_dir():
            continue
        m = re.match(r"(\d{4})_第(\d+)回", session_dir.name)
        if not m:
            continue
        year, session = int(m.group(1)), int(m.group(2))
        key = f"{year}_{session}"

        for pdf in sorted(session_dir.glob("*answer*.pdf")):
            if pdf.name.startswith("._") or "writing" in pdf.name.lower():
                continue
            if "_e" in pdf.name.lower():
                continue

            try:
                doc = fitz.open(str(pdf))
                for page in doc:
                    text = page.get_text()
                    if len(text.strip()) < 50:
                        continue

                    if key not in all_answers:
                        all_answers[key] = {}

                    parsed = parse_answer_page(text)
                    for subj, ans in parsed.items():
                        if ans and subj not in all_answers[key]:
                            all_answers[key][subj] = ans
                doc.close()
            except Exception:
                pass

    # Save
    out_path = JSON_DIR / "answer_keys.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_answers, f, ensure_ascii=False, indent=2)

    # Print summary
    total = 0
    for key, subjects in sorted(all_answers.items()):
        ans_count = sum(len(v) for v in subjects.values())
        total += ans_count
        subj_list = ", ".join(f"{s}:{len(v)}" for s, v in sorted(subjects.items()) if v)
        print(f"  {key}: {ans_count} answers ({subj_list})")

    print(f"\n  Total: {total} answers across {len(all_answers)} sessions")
    return all_answers


def match_to_questions(all_answers: dict):
    """Match answers to questions.json."""
    with open(JSON_DIR / "questions.json") as f:
        data = json.load(f)

    matched = 0
    for q in data["questions"]:
        year = q.get("year")
        session = q.get("session")
        key = f"{year}_{session}"

        if key not in all_answers:
            continue

        session_answers = all_answers[key]
        subject = q.get("subject", "")
        q_num = q.get("question_number", 0)
        detail = q.get("subject_detail", "")

        answer = None

        if subject == "science":
            subj_map = {"物理": "physics", "化学": "chemistry", "生物": "biology"}
            ans_subj = subj_map.get(detail, "")
            if ans_subj in session_answers:
                answer = session_answers[ans_subj].get(q_num)

        elif subject == "japan_and_world":
            if "jw" in session_answers:
                answer = session_answers["jw"].get(q_num)

        elif subject == "math":
            if "math" in session_answers:
                answer = session_answers["math"].get(q_num)

        elif subject == "japanese":
            section_map = {"読解": "ja_reading", "聴読解": "ja_listening_reading", "聴解": "ja_listening"}
            ans_subj = section_map.get(detail, "")
            if ans_subj in session_answers:
                answer = session_answers[ans_subj].get(q_num)

        if answer is not None:
            q["correct_answer"] = answer
            matched += 1

    with open(JSON_DIR / "questions.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n  Matched {matched}/{len(data['questions'])} questions with answers")


if __name__ == "__main__":
    print("Extracting answer keys...")
    answers = extract_all_answers()
    print("\nMatching answers to questions...")
    match_to_questions(answers)
