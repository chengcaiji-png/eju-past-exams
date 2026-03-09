#!/usr/bin/env python3
"""Parse answer keys from OCR text — V2 with proper column handling.

Answer pages have these formats:
- Japanese (日本語): 3 columns — 読解, 聴読解, 聴解
- Science (理科): 3 columns — Physics, Chemistry, Biology
- JW (総合科目): 1-2 columns of questions
- Math (数学): Letter-coded fill-in answers (Course 1 & 2) — SKIP for now

Key insight: OCR reads columns left-to-right, so rows contain data from
all 3 columns interleaved. E.g. "問1 1 3 問1 1 2 問1 1 5" means
Physics Q1=3, Chemistry Q1=2, Biology Q1=5.
"""

import json
import re
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).parent
JSON_DIR = BASE_DIR / "json"

# Expected answer counts per subject
EXPECTED = {
    "physics": (17, 20),   # 17-20 questions
    "chemistry": (18, 21),
    "biology": (16, 19),
    "jw": (30, 40),
    "ja_reading": (15, 26),
    "ja_listening_reading": (8, 14),
    "ja_listening": (18, 28),
}


def parse_science_page(text: str) -> dict:
    """Parse science answer page with 3 side-by-side columns.

    OCR produces lines like:
      問1 1 4 問1 1 4 問1 1 3
    meaning Physics row1=4, Chemistry row1=4, Biology row1=3
    """
    physics, chemistry, biology = {}, {}, {}

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Skip headers
        if re.search(r"理科|Science|Physics|Chemistry|Biology|解答番号|解答欄|正解|row", line):
            if "問" not in line or "正解" in line:
                continue

        # Find all "問N row answer" triples on this line
        # Pattern: 問{N} {row} {answer}  where answer is single digit
        triples = list(re.finditer(r"問\s*(\d+)\s+(\d+)\s+(\d)", line))

        if len(triples) >= 3:
            # Three columns: physics, chemistry, biology
            physics[int(triples[0].group(2))] = int(triples[0].group(3))
            chemistry[int(triples[1].group(2))] = int(triples[1].group(3))
            biology[int(triples[2].group(2))] = int(triples[2].group(3))
        elif len(triples) == 2:
            # Two columns visible — could be any 2 of 3
            # Use row numbers to determine: physics rows start at 1,
            # chem at 1, biology at 1. If first triple has high row and
            # second has same range, likely they're adjacent columns.
            r1, r2 = int(triples[0].group(2)), int(triples[1].group(2))
            a1, a2 = int(triples[0].group(3)), int(triples[1].group(3))

            # Assign based on what we've already seen
            if r1 <= 19 and r2 <= 20:
                # Guess: if row numbers are close, they're from adjacent columns
                # Default to first two columns that need this row
                if r1 not in physics:
                    physics[r1] = a1
                if r2 not in chemistry:
                    chemistry[r2] = a2
                elif r2 not in biology:
                    biology[r2] = a2
        elif len(triples) == 1:
            r = int(triples[0].group(2))
            a = int(triples[0].group(3))
            # Single entry — assign to first subject missing this row
            if r not in physics:
                physics[r] = a
            elif r not in chemistry:
                chemistry[r] = a
            elif r not in biology:
                biology[r] = a

        # Also handle lines without 問 prefix but with row numbers
        # Some OCR lines: "4 3" or "10 3" for biology continuation
        if not triples:
            m = re.match(r"^(\d{1,2})\s+(\d)\s*$", line)
            if m:
                r, a = int(m.group(1)), int(m.group(2))
                if 1 <= a <= 9 and 1 <= r <= 20:
                    if r not in physics:
                        physics[r] = a
                    elif r not in chemistry:
                        chemistry[r] = a
                    elif r not in biology:
                        biology[r] = a

    return {"physics": physics, "chemistry": chemistry, "biology": biology}


def parse_jw_page(text: str) -> dict:
    """Parse JW (総合科目) answer page.

    Returns TWO things in the dict:
    - 'by_row': {row_number: answer} — for matching by row
    - 'by_question': {問number: [(row, answer), ...]} — for matching by question_id
    """
    by_row = {}
    by_question = defaultdict(list)  # 問N -> [(row, answer), ...]
    current_mon = None  # Current 問 number

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Skip headers
        if re.search(r"総合科目|Japan|解答番号|解答欄|正解|row|ウェブ|掲載", line):
            if "問" not in line or "正解" in line:
                continue

        # Find 問N patterns to track current question group
        mon_matches = list(re.finditer(r"問\s*(\d+)", line))

        # Find "問N row answer" triples
        triples = list(re.finditer(r"問\s*(\d+)\s+(\d{1,2})\s+(\d)\b", line))
        for t in triples:
            mon = int(t.group(1))
            row = int(t.group(2))
            answer = int(t.group(3))
            if 1 <= answer <= 9 and 1 <= row <= 40:
                by_row[row] = answer
                by_question[mon].append((row, answer))
                current_mon = mon

        # "row answer" without 問 prefix (sub-questions of current 問)
        # Lines like "1 4" or "3 1"
        if not triples:
            # Check for 問N at start without answer (just sets current_mon)
            for mm in mon_matches:
                current_mon = int(mm.group(1))

            pairs = list(re.finditer(r"(?:^|\s)(\d{1,2})\s+(\d)(?:\s|$)", line))
            for p in pairs:
                row = int(p.group(1))
                answer = int(p.group(2))
                if 1 <= row <= 40 and 1 <= answer <= 9:
                    by_row[row] = answer
                    if current_mon is not None:
                        by_question[current_mon].append((row, answer))

    return by_row


def parse_japanese_page(text: str) -> dict:
    """Parse Japanese (日本語) answer page with 読解, 聴読解, 聴解 columns.

    Format: 3 side-by-side columns, OCR reads them interleaved per line.
    - 読解 (Reading): Roman numerals (I-XVI) + 問N, rows 1-25
    - 聴読解 (Listening-Reading): N番 format, rows 1-12
    - 聴解 (Listening): N番 format, rows 13-27

    OCR lines look like:
      "I 1 3 1番 1 13番 13 2"
    = reading row1=3, listening_reading row1=?, listening row13=2
    """
    reading = {}           # 読解
    listening_reading = {} # 聴読解
    listening = {}         # 聴解

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Skip pure header/footer lines
        if re.search(r"日本語|Japanese|解答番号|解答欄|正解|row|ウェブ|掲載|正解表|Correct", line):
            if "番" not in line and "問" not in line:
                continue

        # Skip writing-related content
        if "記述" in line:
            continue

        # Strategy: On each line, find:
        # 1. Roman/問 entries (読解) — leftmost column
        # 2. N番 entries — 聴読解 (small N, 1-12) and 聴解 (large N, 13-27)

        # Extract 読解 answers: Roman numeral + row + answer OR 問N + row + answer
        # Pattern: Roman/問 prefix then row answer
        roman_match = re.match(r"[IⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩXVvi]+\s+(\d{1,2})\s+(\d)\b", line)
        if roman_match:
            row = int(roman_match.group(1))
            answer = int(roman_match.group(2))
            if 1 <= answer <= 9 and 1 <= row <= 30:
                reading[row] = answer

        # 問N row answer patterns for 読解 (XI 問1 11 4, etc.)
        q_matches = list(re.finditer(r"問\s*(\d+)\s+(\d{1,2})\s+(\d)\b", line))
        for m in q_matches:
            row = int(m.group(2))
            answer = int(m.group(3))
            if 1 <= answer <= 9 and 1 <= row <= 30:
                # 読解 questions have rows 1-25
                if row <= 25 and row not in reading:
                    reading[row] = answer

        # Extract 聴読解 and 聴解: N番 row answer
        ban_matches = list(re.finditer(r"(\d+)\s*番\s+(\d{1,2})\s+(\d)\b", line))
        for m in ban_matches:
            ban_num = int(m.group(1))
            row = int(m.group(2))
            answer = int(m.group(3))
            if 1 <= answer <= 9:
                # 聴読解 = rows 1-12 (番 1-12), 聴解 = rows 13-27 (番 13-27)
                if row <= 12:
                    listening_reading[row] = answer
                else:
                    listening[row] = answer

        # Also: N番 row (no answer) — answer might be on the row number itself
        # "1番 1" could be ban_num=1, row=1 without answer
        ban_only = list(re.finditer(r"(\d+)\s*番\s+(\d{1,2})\s*$", line))
        # These are incomplete, skip

        # Simple standalone: "row answer" without any prefix
        if not roman_match and not q_matches and not ban_matches:
            simple = re.match(r"^(\d{1,2})\s+(\d)\s*$", line)
            if simple:
                row = int(simple.group(1))
                answer = int(simple.group(2))
                if 1 <= answer <= 9 and 1 <= row <= 30:
                    if row <= 25 and row not in reading:
                        reading[row] = answer

    result = {}
    if reading:
        result["ja_reading"] = reading
    if listening_reading:
        result["ja_listening_reading"] = listening_reading
    if listening:
        result["ja_listening"] = listening
    return result


def parse_answer_pages(pages_text: list[str], year: int, session: int) -> dict:
    """Parse all answer pages for a session.

    pages_text: list of OCR text for each page
    Returns: {subject: {row_num: answer}}
    """
    all_answers = {}

    for text in pages_text:
        if not text or len(text.strip()) < 30:
            continue

        # Skip writing model answers
        if "記述" in text and "問題解答例" in text:
            continue

        # Detect page type
        is_science = bool(re.search(r"理\s*科|物理|化学|生物", text))
        is_japanese = bool(re.search(r"日本語|読解|聴読解|聴解", text))
        is_jw = bool(re.search(r"総合科目|Japan and the World", text))
        is_math = bool(re.search(r"数\s*学|Mathematics|コース", text))

        if is_science:
            sci = parse_science_page(text)
            for subj, ans in sci.items():
                if ans and subj not in all_answers:
                    all_answers[subj] = ans
                elif ans and subj in all_answers:
                    all_answers[subj].update(ans)

        if is_japanese:
            ja = parse_japanese_page(text)
            for subj, ans in ja.items():
                if ans and subj not in all_answers:
                    all_answers[subj] = ans
                elif ans and subj in all_answers:
                    all_answers[subj].update(ans)

        if is_jw:
            jw = parse_jw_page(text)
            if jw:
                if "jw" not in all_answers:
                    all_answers["jw"] = jw
                else:
                    all_answers["jw"].update(jw)

        # Math is fill-in-the-blank with letter codes — skip for multiple choice matching
        # (math questions extracted from OCR use different numbering)

    return all_answers


def extract_all_answers():
    """Extract answers from OCR results and PDF text."""
    ocr_path = JSON_DIR / "ocr_results.json"
    with open(ocr_path) as f:
        ocr_data = json.load(f)

    all_answers = {}  # key -> {subject: {row: answer}}

    # Group OCR results by (year, session)
    session_pages = defaultdict(list)

    for r in ocr_data["results"]:
        dn = r["dir_name"].lower()
        if "answer" not in dn:
            continue
        if "writing" in dn:
            continue

        year, session = r["year"], r["session"]
        key = f"{year}_{session}"

        for p in r["pages"]:
            if p["text"] and len(p["text"].strip()) > 30:
                session_pages[key].append({
                    "text": p["text"],
                    "year": year,
                    "session": session,
                    "dir_name": r["dir_name"],
                    "page": p["page"],
                })

    # Also extract from PDF text directly
    try:
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
                # Skip English versions
                if "_e" in pdf.name.lower() and not pdf.name.lower().startswith("eju"):
                    continue

                try:
                    doc = fitz.open(str(pdf))
                    for page in doc:
                        text = page.get_text()
                        if len(text.strip()) > 50:
                            session_pages[key].append({
                                "text": text,
                                "year": year,
                                "session": session,
                                "dir_name": pdf.name,
                                "page": page.number,
                            })
                    doc.close()
                except Exception:
                    pass
    except ImportError:
        print("  Warning: PyMuPDF not available, using OCR text only")

    # Parse each session
    for key in sorted(session_pages.keys()):
        pages = session_pages[key]
        year = pages[0]["year"]
        session = pages[0]["session"]
        texts = [p["text"] for p in pages]

        answers = parse_answer_pages(texts, year, session)
        if answers:
            all_answers[key] = answers

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


def build_jw_qid_to_row(answer_rows: dict) -> dict:
    """Build mapping from JW question_id to answer row number.

    JW answer key format: 問1 has rows 1-4, 問2 has rows 5-8, 問3 row 9, etc.
    Question IDs: Q1-1 (問1 sub 1) → row 1, Q1-2 → row 2, Q3 → row 9, etc.

    Strategy: sort rows, group consecutive rows by 問 number gaps,
    then map QN or QN-M to the correct row.
    """
    if not answer_rows:
        return {}

    # answer_rows is {row: answer}
    sorted_rows = sorted(answer_rows.keys())
    if not sorted_rows:
        return {}

    # For JW, standard structure:
    # 問1: rows 1-4 (4 sub-questions)
    # 問2: rows 5-8 (4 sub-questions)
    # 問3-問32: rows 9-38 (1 sub-question each)
    # But this varies. Build a general mapping.

    # Map: for each row, figure out which 問 it belongs to
    # Using standard EJU JW format: 問1 has 4 subs, 問2 has 4 subs, rest have 1
    # This gives: 問N for N>=3 → row = N + 6

    qid_to_row = {}

    # 問1: rows 1-4
    for i in range(1, 5):
        if i in answer_rows:
            qid_to_row[f"Q1-{i}"] = i

    # 問2: rows 5-8
    for i in range(1, 5):
        row = 4 + i
        if row in answer_rows:
            qid_to_row[f"Q2-{i}"] = row

    # 問3 onwards: row = 問number + 6
    for mon in range(3, 40):
        row = mon + 6
        if row in answer_rows:
            qid_to_row[f"Q{mon}"] = row

    return qid_to_row


def build_ja_reading_qid_to_row(answer_rows: dict) -> dict:
    """Build mapping from Japanese 読解 question_id to answer row.

    読解 format: Roman numerals I-XVI with 問1/問2 sub-questions.
    Rows are sequential 1-25.
    Question IDs from text extraction use patterns like:
    I→1, II→2, ..., XI問1→11, XI問2→12, etc.
    """
    # For 読解, question_number IS the row number in most cases
    # Just return identity mapping
    return {row: row for row in answer_rows}


def match_to_questions(all_answers: dict):
    """Match answers to questions.json using row numbers and question_id mapping."""
    with open(JSON_DIR / "questions.json") as f:
        data = json.load(f)

    # Build JW qid→row mappings per session
    jw_mappings = {}
    for key, subjects in all_answers.items():
        if "jw" in subjects:
            jw_mappings[key] = build_jw_qid_to_row(subjects["jw"])

    matched = 0
    unmatched_subjects = defaultdict(int)

    for q in data["questions"]:
        year = q.get("year")
        session = q.get("session")
        key = f"{year}_{session}"

        if key not in all_answers:
            continue

        session_answers = all_answers[key]
        subject = q.get("subject", "")
        detail = q.get("subject_detail", "")

        # Determine answer subject key
        ans_subj = None
        if subject == "science":
            subj_map = {"物理": "physics", "化学": "chemistry", "生物": "biology"}
            ans_subj = subj_map.get(detail, "")
        elif subject == "japan_and_world":
            ans_subj = "jw"
        elif subject == "japanese":
            section_map = {"読解": "ja_reading", "聴読解": "ja_listening_reading", "聴解": "ja_listening"}
            ans_subj = section_map.get(detail, "")
        elif subject == "math":
            ans_subj = "math"

        if not ans_subj or ans_subj not in session_answers:
            if ans_subj:
                unmatched_subjects[f"{key}:{ans_subj}"] += 1
            continue

        answer = None

        # Try direct question_number match (works for science, OCR questions)
        q_num = q.get("question_number")
        subj_ans = session_answers[ans_subj]
        if q_num is not None:
            if q_num in subj_ans:
                answer = subj_ans[q_num]
            elif str(q_num) in subj_ans:
                answer = subj_ans[str(q_num)]

        # For JW: use qid→row mapping or q_num + 6 offset
        if answer is None and ans_subj == "jw":
            jw_ans = session_answers.get("jw", {})
            qid = q.get("question_id", "")
            if qid and key in jw_mappings:
                row = jw_mappings[key].get(qid)
                if row is not None:
                    if row in jw_ans:
                        answer = jw_ans[row]
                    elif str(row) in jw_ans:
                        answer = jw_ans[str(row)]
            # Also try: Q{N} where N itself is the row
            if answer is None and qid:
                m = re.match(r"Q(\d+)$", qid)
                if m:
                    direct_num = int(m.group(1))
                    row_guess = direct_num + 6
                    if row_guess in jw_ans:
                        answer = jw_ans[row_guess]
                    elif str(row_guess) in jw_ans:
                        answer = jw_ans[str(row_guess)]
            # Fallback: q_num + 6 offset (問N → row N+6 for N≥3)
            if answer is None and q_num is not None:
                if q_num >= 3:
                    row_guess = q_num + 6
                    if row_guess in jw_ans:
                        answer = jw_ans[row_guess]
                    elif str(row_guess) in jw_ans:
                        answer = jw_ans[str(row_guess)]

        # For Japanese: try qid-based matching
        if answer is None and ans_subj in ("ja_reading", "ja_listening_reading", "ja_listening"):
            qid = q.get("question_id", "")
            if qid:
                # Extract number from Q{N} or Q{N}-{M}
                m = re.match(r"Q(\d+)(?:-(\d+))?", qid)
                if m:
                    main_num = int(m.group(1))
                    sub_num = int(m.group(2)) if m.group(2) else None
                    # Try main_num as row
                    sa = session_answers[ans_subj]
                    if main_num in sa:
                        answer = sa[main_num]
                    elif str(main_num) in sa:
                        answer = sa[str(main_num)]

        if answer is not None:
            q["correct_answer"] = answer
            matched += 1
        else:
            if ans_subj:
                unmatched_subjects[f"{key}:{ans_subj}"] += 1

    with open(JSON_DIR / "questions.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n  Matched {matched}/{len(data['questions'])} questions with answers")

    # Show top unmatched
    if unmatched_subjects:
        print(f"\n  Unmatched by session:subject:")
        for k, v in sorted(unmatched_subjects.items(), key=lambda x: -x[1])[:15]:
            print(f"    {k}: {v} questions")


if __name__ == "__main__":
    print("Extracting answer keys (v2)...")
    answers = extract_all_answers()
    print("\nMatching answers to questions...")
    match_to_questions(answers)
