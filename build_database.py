#!/usr/bin/env python3
"""Build comprehensive EJU question database from all sources.

Strategy:
- Science: Each content page = 1 question. Use page position + end markers for section detection.
- JW: Each 問N = 1 question. Row number for answer matching.
- Japanese: Section-based reading questions.
- Answers: From answer_keys.json, matched by row number.
"""

import json
import re
from pathlib import Path
from collections import defaultdict, Counter

BASE_DIR = Path(__file__).parent
JSON_DIR = BASE_DIR / "json"


def load_merged_texts():
    """Load all page texts, merging clean PDF text with OCR for garbled pages."""
    with open(JSON_DIR / "pdf_texts.json") as f:
        pdf_texts = json.load(f)
    with open(JSON_DIR / "ocr_results.json") as f:
        ocr_data = json.load(f)

    ocr_by_dir = defaultdict(dict)
    for r in ocr_data["results"]:
        for p in r["pages"]:
            ocr_by_dir[r["dir_name"]][p["page"]] = p["text"]

    def is_garbled(text):
        if not text or len(text.strip()) < 50:
            return True
        # Only copyright line
        if len(text.strip()) < 80 and ("ⓒ" in text or "©" in text):
            return True
        # Check for exotic Unicode (CID-mapped fonts producing Myanmar, Lao, NKo, etc.)
        exotic = sum(1 for c in text[:300] if '\u0780' <= c <= '\u0fff' or '\u1000' <= c <= '\u1fff')
        if exotic > 5:
            return True
        # Check ratio of Japanese chars vs total non-ASCII
        # Real Japanese text has hiragana/katakana/kanji; garbled text has random Unicode
        sample = text[:400]
        non_ascii = sum(1 for c in sample if ord(c) > 127)
        if non_ascii < 10:
            return False  # Mostly ASCII/English, not garbled
        jp_chars = sum(1 for c in sample if
            '\u3040' <= c <= '\u309f' or  # hiragana
            '\u30a0' <= c <= '\u30ff' or  # katakana
            '\u4e00' <= c <= '\u9fff' or  # CJK unified
            '\uff00' <= c <= '\uffef')    # fullwidth
        ratio = jp_chars / non_ascii if non_ascii > 0 else 0
        if ratio < 0.15 and non_ascii > 20:
            return True
        return False

    all_texts = {}
    for key, info in pdf_texts.items():
        year, session, subj = info["year"], info["session"], info["subject"]
        texts = {}
        for pnum_str, text in info["texts"].items():
            pnum = int(pnum_str)
            if not is_garbled(text):
                texts[pnum] = text
            else:
                ocr_text = ocr_by_dir.get(info["dir"], {}).get(pnum, "")
                texts[pnum] = ocr_text if ocr_text else ""
        all_texts[(year, session, subj)] = texts

    # Also add OCR-only results (for sessions not in pdf_texts)
    subj_map = {
        "science": "science", "math": "math",
        "japan_and_world": "jw", "japanese": "japanese",
    }
    for r in ocr_data["results"]:
        subj = r["subject"]
        if subj in ("answer", "script", "unknown"):
            continue
        mapped = subj_map.get(subj, subj)
        key = (r["year"], r["session"], mapped)
        if key not in all_texts:
            texts = {}
            for p in r["pages"]:
                if p["text"] and len(p["text"].strip()) > 30:
                    texts[p["page"]] = p["text"]
            if texts:
                all_texts[key] = texts
        else:
            # Merge in OCR pages that are missing
            for p in r["pages"]:
                if p["page"] not in all_texts[key] or not all_texts[key][p["page"]]:
                    if p["text"] and len(p["text"].strip()) > 30:
                        all_texts[key][p["page"]] = p["text"]

    return all_texts


# ============================================================
# Science
# ============================================================

def parse_science(texts: dict, year: int, session: int) -> list[dict]:
    """Parse science questions. Each substantial page = one question."""
    sorted_pages = sorted(texts.keys())
    if not sorted_pages:
        return []

    # Find section boundaries using header pages (解答科目 + subject name)
    # These are more reliable than end markers
    chem_start = bio_start = None
    phys_end = chem_end = bio_end = None
    for pnum in sorted_pages:
        t = texts[pnum]
        if "物理の問題はこれで終わり" in t:
            phys_end = pnum
        if "化学の問題はこれで終わり" in t:
            chem_end = pnum
        if "生物の問題はこれで終わり" in t:
            bio_end = pnum
        # Detect subject header pages (「化学」or 「生物」 in first few lines)
        for line in t.strip().split("\n")[:5]:
            ls = line.strip()
            if chem_start is None and pnum > 20:
                if re.match(r"^化学\s*$", ls) or ("化学" in ls and "解答科目" in t):
                    chem_start = pnum
            if bio_start is None and pnum > 30:
                if re.match(r"^生物\s*$", ls) or ("生物" in ls and "解答科目" in t):
                    bio_start = pnum

    # Find first content page (skip instruction pages)
    first_content = None
    for pnum in sorted_pages:
        t = texts[pnum]
        if len(t.strip()) > 100 and ("次の" in t or re.search(r"問\s*\d+", t)):
            first_content = pnum
            break

    if first_content is None:
        first_content = sorted_pages[2] if len(sorted_pages) > 2 else sorted_pages[0]

    # Use header pages as primary boundaries, end markers as secondary
    # Physics: first_content to chem_start (or phys_end)
    phys_boundary = chem_start or phys_end
    if phys_boundary is None:
        content_pages = [p for p in sorted_pages if p >= first_content and len(texts[p].strip()) > 80]
        phys_boundary = content_pages[19] + 1 if len(content_pages) >= 40 else content_pages[len(content_pages)//3] + 1

    # Chemistry: chem_start to bio_start (or chem_end)
    chem_boundary = bio_start or chem_end
    if chem_boundary is None and chem_start:
        remaining = [p for p in sorted_pages if p > chem_start and len(texts[p].strip()) > 80]
        chem_boundary = remaining[len(remaining)//2] + 1 if remaining else sorted_pages[-1]

    questions = []
    phys_q = 0
    # For chem/bio: track the next expected question number
    chem_next = 1
    bio_next = 1

    for pnum in sorted_pages:
        t = texts[pnum]
        if len(t.strip()) < 80:
            continue

        # Skip non-question pages (but allow header pages that also contain questions)
        if "問題はこれで終わり" in t:
            continue
        if "試験問題" in t and "注意" in t and len(t) < 600:
            continue
        if pnum < first_content:
            continue

        # Determine section using boundaries
        if pnum < phys_boundary:
            section = "物理"
        elif chem_boundary and pnum < chem_boundary:
            section = "化学"
        elif pnum >= (chem_boundary or phys_boundary):
            section = "生物"
        else:
            continue

        # For header pages (解答科目): only process if they contain question markers
        if "解答科目" in t:
            if section == "物理":
                continue  # Physics header pages never have questions
            # Chem/bio header pages sometimes contain 問1
            if not re.search(r"問\s*\d+", t):
                continue

        # Extract explicit 問N markers from text
        q_nums_found = sorted(set(
            int(m.group(1)) for m in re.finditer(r"問\s*(\d+)", t)
            if 1 <= int(m.group(1)) <= 25
        ))

        if section == "物理":
            # Physics: sequential counting (1 question per page, no multi-Q)
            phys_q += 1
            q_num = phys_q
            q_text = _extract_q_text(t)
            questions.append({
                "year": year, "session": session,
                "subject": "science", "subject_detail": section,
                "question_number": q_num, "question_text": q_text,
                "source": "combined", "page": pnum,
            })
        elif section in ("化学", "生物"):
            next_q = chem_next if section == "化学" else bio_next

            if len(q_nums_found) >= 2 and q_nums_found[-1] - q_nums_found[0] == len(q_nums_found) - 1:
                # Multiple consecutive questions on this page
                for qn in q_nums_found:
                    pattern = rf"問\s*{qn}\s*(.{{0,200}})"
                    m = re.search(pattern, t, re.DOTALL)
                    q_text = m.group(1).split("\n")[0].strip()[:200] if m else ""
                    questions.append({
                        "year": year, "session": session,
                        "subject": "science", "subject_detail": section,
                        "question_number": qn, "question_text": q_text,
                        "source": "combined", "page": pnum,
                    })
                max_q = max(q_nums_found)
                if section == "化学":
                    chem_next = max_q + 1
                else:
                    bio_next = max_q + 1
            elif len(q_nums_found) == 1:
                # Single explicit question number
                qn = q_nums_found[0]
                q_text = _extract_q_text(t)
                questions.append({
                    "year": year, "session": session,
                    "subject": "science", "subject_detail": section,
                    "question_number": qn, "question_text": q_text,
                    "source": "combined", "page": pnum,
                })
                if section == "化学":
                    chem_next = qn + 1
                else:
                    bio_next = qn + 1
            else:
                # No 問N found (garbled OCR) — use sequential counter
                qn = next_q
                q_text = _extract_q_text(t)
                questions.append({
                    "year": year, "session": session,
                    "subject": "science", "subject_detail": section,
                    "question_number": qn, "question_text": q_text,
                    "source": "combined", "page": pnum,
                })
                if section == "化学":
                    chem_next = qn + 1
                else:
                    bio_next = qn + 1

    return questions


def _extract_q_text(t: str) -> str:
    """Extract question text from a page."""
    for line in t.split("\n"):
        ls = line.strip()
        if re.search(r"問\s*\d+|次の|選び|正し|どれ|どの|誤", ls) and len(ls) > 10:
            return ls[:200]
    for line in t.split("\n"):
        ls = line.strip()
        if len(ls) > 20 and not re.match(r"^理科|^©|^ⓒ|^\d+$", ls):
            return ls[:200]
    return ""


# ============================================================
# JW (総合科目)
# ============================================================

def parse_jw(texts: dict, year: int, session: int) -> list[dict]:
    """Parse JW questions. Find all 問N markers."""
    questions = []
    found_qs = set()

    for pnum in sorted(texts.keys()):
        t = texts[pnum]
        if len(t.strip()) < 50:
            continue
        if "試験問題" in t and "注意" in t and len(t) < 600:
            continue

        # Find all 問N on this page
        for m in re.finditer(r"問\s*(\d+)", t):
            q_num = int(m.group(1))
            if q_num in found_qs:
                continue
            if q_num > 40:
                continue  # Probably OCR noise

            found_qs.add(q_num)

            # Extract context
            pos = m.start()
            context = t[max(0, pos):min(len(t), pos+300)]
            q_text = context.split("\n")[0].strip()[:200]

            questions.append({
                "year": year,
                "session": session,
                "subject": "japan_and_world",
                "subject_detail": "",
                "question_number": q_num,
                "question_text": q_text,
                "source": "combined",
                "page": pnum,
            })

    questions.sort(key=lambda q: q["question_number"])
    return questions


# ============================================================
# Japanese (読解)
# ============================================================

ROMAN_MAP = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5,
    "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
    "XI": 11, "XII": 12, "XIII": 13, "XIV": 14, "XV": 15,
    "XVI": 16, "XVII": 17, "XVIII": 18,
    "Ⅰ": 1, "Ⅱ": 2, "Ⅲ": 3, "Ⅳ": 4, "Ⅴ": 5,
}


def _detect_section(text: str) -> int | None:
    """Detect Roman numeral section from page text (inline or standalone)."""
    for line in text.strip().split("\n")[:8]:
        ls = line.strip()
        # Standalone: "XIII" or "XVII"
        m = re.match(r"^([IⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩXVvi]{1,6})\s*$", ls)
        if m and m.group(1) in ROMAN_MAP:
            return ROMAN_MAP[m.group(1)]
        # Inline with space: "III 次の文章で..." or "Ⅳ、Ⅴ..."
        m = re.match(r"^([IⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩXVvi]{1,6})[\s　、,]+", ls)
        if m and m.group(1) in ROMAN_MAP:
            return ROMAN_MAP[m.group(1)]
        # Inline without space (OCR): "Ⅱ次の文章..." or "VI次の..."
        m = re.match(r"^([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]{1,4})[次この]", ls)
        if m and m.group(1) in ROMAN_MAP:
            return ROMAN_MAP[m.group(1)]
        m = re.match(r"^(X{0,3}(?:IV|IX|V?I{0,3}))\s*[次この]", ls)
        if m and m.group(1) in ROMAN_MAP:
            return ROMAN_MAP[m.group(1)]
    return None


def _extract_choices(text: str) -> list[str]:
    """Extract MC choices from Japanese question text."""
    choices = []
    for cm in re.finditer(r"^(\d)\s*[．.]\s*(.+?)$", text, re.MULTILINE):
        if 1 <= int(cm.group(1)) <= 6:
            choices.append(cm.group(2).strip()[:100])
    if len(choices) >= 3:
        return choices[:4]
    choices = []
    for cm in re.finditer(r"^(\d)\s+(.{5,})$", text, re.MULTILINE):
        if 1 <= int(cm.group(1)) <= 6:
            choices.append(cm.group(2).strip()[:100])
    return choices[:4]


def parse_japanese(texts: dict, year: int, session: int) -> list[dict]:
    """Parse Japanese reading questions.

    Strategy: Find pages between 読解問題 header and 聴読解 marker.
    Each page with choices = one question. Track section via Roman numerals.
    For sections XI+, sub-questions (問1, 問2) each get their own row.
    """
    questions = []
    sorted_pages = sorted(texts.keys())

    # Find reading section boundaries
    reading_start = reading_end = None

    # Strategy: find first real question page (has choices + question text)
    # and the 聴読解問題 header page (standalone, not in TOC)
    for pnum in sorted_pages:
        t = texts[pnum]
        if len(t.strip()) < 50:
            continue

        # Detect reading start: first page with question + choices after instruction pages
        if reading_start is None:
            # Skip TOC and instruction pages (usually first 3-4 pages)
            if pnum <= sorted_pages[0] + 2:
                # Only accept if clearly a question page
                if _extract_choices(t) and re.search(r"次の|どれ|どのよう|筆者", t):
                    # Check it's not an instruction page with numbered rules
                    if not re.search(r"係員|試験開始|合図|問題冊子", t):
                        reading_start = pnum
                continue
            if _extract_choices(t) and re.search(r"次の|どれ|どのよう|筆者", t):
                reading_start = pnum
            elif "読解問題" in t and len(t.strip()) < 200:
                reading_start = pnum

        # Detect reading end: standalone 聴読解 header page (not TOC)
        if reading_start is not None and pnum > reading_start:
            if "聴読解" in t:
                # Must be a header page, not a question page with 聴読解 in passing
                if "聴読解問題" in t or (len(t.strip()) < 400 and "聴読解" in t):
                    reading_end = pnum
                    break

    if reading_start is None:
        return []
    if reading_end is None:
        reading_end = max(sorted_pages) + 1

    current_section = 0
    found_rows = set()
    row_counter = 0  # Sequential row counter as fallback

    for pnum in sorted_pages:
        if pnum < reading_start or pnum >= reading_end:
            continue
        t = texts[pnum]
        if len(t.strip()) < 50:
            continue

        # Skip header/instruction pages
        if re.match(r".*読解問題\s*$", t.strip().split("\n")[0].strip()):
            continue
        if "問題冊子" in t and "記入して" in t:
            continue

        # Detect section
        sec = _detect_section(t)
        if sec is not None:
            current_section = sec

        # Need question content or sub-question marker
        is_question = bool(re.search(r"次の|どれ|どのよう|筆者|合って|下線|問\d", t))
        if not is_question:
            continue

        # Need choices (except for passage-only pages in multi-page sections)
        choices = _extract_choices(t)
        if len(choices) < 2:
            continue

        # Determine row number
        sub_qs = [int(sq.group(1)) for sq in re.finditer(r"問\s*(\d+)", t)]
        if current_section >= 11 and sub_qs:
            sub_num = max(sub_qs)
            row = 10 + (current_section - 11) * 2 + sub_num
        elif current_section > 0:
            row = current_section
        else:
            # No section detected - use sequential counter
            row_counter += 1
            row = row_counter

        if row in found_rows:
            continue
        found_rows.add(row)

        # Extract question prompt
        prompt = ""
        for line in t.split("\n"):
            ls = line.strip()
            if re.search(r"次の|どれ|どのよう|筆者|合って|下線|問\s*\d", ls) and len(ls) > 10:
                prompt = ls[:200]
                break

        questions.append({
            "year": year,
            "session": session,
            "subject": "japanese",
            "subject_detail": "読解",
            "question_number": row,
            "question_text": prompt,
            "choices": choices[:4],
            "source": "combined",
            "page": pnum,
        })

    return questions


# ============================================================
# Answer matching
# ============================================================

def load_and_match_answers(questions: list[dict]) -> int:
    """Match answers to questions. Returns count matched."""
    with open(JSON_DIR / "answer_keys.json") as f:
        answers = json.load(f)

    matched = 0
    for q in questions:
        key = f"{q['year']}_{q['session']}"
        if key not in answers:
            continue

        sa = answers[key]
        subj = q["subject"]
        detail = q.get("subject_detail", "")
        q_num = q.get("question_number")
        if q_num is None:
            continue

        # Map to answer key subject
        if subj == "science":
            ans_key = {"物理": "physics", "化学": "chemistry", "生物": "biology"}.get(detail)
        elif subj == "japan_and_world":
            ans_key = "jw"
            # JW row mapping: 問1 rows 1-4, 問2 rows 5-8, 問N>2 row N+6
            if q_num >= 3:
                q_num = q_num + 6
        elif subj == "japanese" and detail == "読解":
            ans_key = "ja_reading"
        else:
            continue

        if not ans_key or ans_key not in sa:
            continue

        ans_dict = sa[ans_key]
        for k in [q_num, str(q_num)]:
            if k in ans_dict:
                val = ans_dict[k]
                if isinstance(val, int) and 1 <= val <= 9:
                    q["correct_answer"] = val
                    matched += 1
                    break

    return matched


# ============================================================
# Main
# ============================================================

def main():
    print("Loading page texts...")
    all_texts = load_merged_texts()

    total_with_text = sum(sum(1 for t in pt.values() if t and len(t.strip()) > 50) for pt in all_texts.values())
    print(f"  {len(all_texts)} subject-PDFs, {total_with_text} pages with text")

    all_questions = []

    # Science
    print("\nScience:")
    for (year, session, subj), texts in sorted(all_texts.items()):
        if subj != "science":
            continue
        qs = parse_science(texts, year, session)
        all_questions.extend(qs)
        phys = sum(1 for q in qs if q["subject_detail"] == "物理")
        chem = sum(1 for q in qs if q["subject_detail"] == "化学")
        bio = sum(1 for q in qs if q["subject_detail"] == "生物")
        print(f"  {year}_{session}: {phys}P + {chem}C + {bio}B = {len(qs)}")

    # JW
    print("\nJW:")
    for (year, session, subj), texts in sorted(all_texts.items()):
        if subj != "jw":
            continue
        qs = parse_jw(texts, year, session)
        all_questions.extend(qs)
        print(f"  {year}_{session}: {len(qs)}")

    # Japanese
    print("\nJapanese:")
    for (year, session, subj), texts in sorted(all_texts.items()):
        if subj != "japanese":
            continue
        qs = parse_japanese(texts, year, session)
        all_questions.extend(qs)
        if qs:
            print(f"  {year}_{session}: {len(qs)}")

    # Match answers
    print("\nMatching answers...")
    matched = load_and_match_answers(all_questions)

    # Save
    output = {"questions": all_questions}
    with open(JSON_DIR / "questions.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Summary
    print(f"\n{'='*60}")
    print(f"TOTAL: {len(all_questions)} questions, {matched} with answers ({100*matched/max(len(all_questions),1):.0f}%)")

    by_label = Counter()
    matched_by = Counter()
    for q in all_questions:
        label = f"{q['subject']}:{q.get('subject_detail','')}" if q.get("subject_detail") else q["subject"]
        by_label[label] += 1
        if "correct_answer" in q:
            matched_by[label] += 1

    print("\nBy subject:")
    for label in sorted(by_label.keys()):
        t = by_label[label]
        m = matched_by.get(label, 0)
        print(f"  {label}: {m}/{t} ({100*m/t:.0f}%)")

    print(f"\nPer session:")
    sc = Counter()
    for q in all_questions:
        sc[f"{q['year']}_{q['session']}"] += 1
    for k, v in sorted(sc.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
