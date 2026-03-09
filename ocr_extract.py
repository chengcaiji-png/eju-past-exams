#!/usr/bin/env python3
"""OCR extraction for EJU exam images using PaddleOCR.

Optimized: Only process Japanese versions, skip English and duplicate copies.
Targets: math (all years), science (all years), 2010 all subjects, 2018 all subjects.
"""

import os
os.environ["DISABLE_MODEL_SOURCE_CHECK"] = "True"

import json
import re
import time
from pathlib import Path

from paddleocr import PaddleOCR

BASE_DIR = Path(__file__).parent
IMG_DIR = BASE_DIR / "images"
JSON_DIR = BASE_DIR / "json"

# Initialize PaddleOCR with Japanese model
print("Loading PaddleOCR (Japanese model)...")
ocr = PaddleOCR(use_textline_orientation=True, lang="japan")


def ocr_image(img_path: str) -> list[dict]:
    """OCR a single image, return list of {text, confidence, y, x}."""
    results = list(ocr.predict(str(img_path)))
    lines = []
    for r in results:
        for text, score, poly in zip(r["rec_texts"], r["rec_scores"], r["rec_polys"]):
            y_center = sum(p[1] for p in poly) / 4
            x_center = sum(p[0] for p in poly) / 4
            lines.append({
                "text": text,
                "confidence": float(score),
                "y": float(y_center),
                "x": float(x_center),
            })
    lines.sort(key=lambda l: (l["y"], l["x"]))
    return lines


def lines_to_text(lines: list[dict], min_conf: float = 0.3) -> str:
    """Convert OCR lines to readable text, grouping by approximate Y position."""
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


def select_primary_dirs(dirs: list[dict]) -> list[dict]:
    """Select one primary Japanese version per (year, session, subject).

    Priority: unnumbered JP > lowest-numbered JP > skip English.
    """
    from collections import defaultdict

    # Group by (year, session, subject)
    groups = defaultdict(list)
    for d in dirs:
        key = (d["year"], d["session"], d["subject"])
        groups[key].append(d)

    selected = []
    for key, candidates in sorted(groups.items()):
        # Filter out English versions
        jp_candidates = []
        for c in candidates:
            dn = c["dir_name"].lower()
            # Skip English: contains '_e' suffix or '_en'
            if re.search(r"_e(_\d+)?$", dn) or "_en" in dn:
                continue
            jp_candidates.append(c)

        if not jp_candidates:
            # All English? Take first candidate anyway
            jp_candidates = candidates[:1]

        # Among JP candidates, prefer unnumbered, then lowest number
        def sort_key(c):
            dn = c["dir_name"]
            m = re.search(r"_(\d+)$", dn)
            if m:
                return int(m.group(1))
            return 0  # Unnumbered = highest priority

        jp_candidates.sort(key=sort_key)
        selected.append(jp_candidates[0])

    return selected


def identify_subject(filename: str) -> tuple[str, str]:
    """Identify subject from filename."""
    fn = filename.lower()
    if "math" in fn:
        return "math", "数学"
    elif "science" in fn:
        return "science", "理科"
    elif "jw" in fn:
        return "japan_and_world", "総合科目"
    elif "jafl" in fn or "japanese" in fn:
        return "japanese", "日本語"
    elif "answer" in fn:
        return "answer", "正解表"
    elif "script" in fn:
        return "script", "聴解スクリプト"
    return "unknown", "不明"


def get_dirs_to_process() -> list[dict]:
    """Get directories needing OCR, filtered to one Japanese version per subject."""
    all_candidates = []
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
            pngs = sorted(sub_dir.glob("*.png"))
            if not pngs:
                continue

            subject, subject_ja = identify_subject(sub_dir.name)

            # Skip answer keys and scripts
            if subject in ("answer", "script", "unknown"):
                continue

            # Check if this subject needs OCR
            needs = (
                subject == "math"
                or subject == "science"
                or year == 2010
                or year == 2018
            )
            if not needs:
                continue

            all_candidates.append({
                "path": sub_dir,
                "year": year,
                "session": session,
                "subject": subject,
                "subject_ja": subject_ja,
                "dir_name": sub_dir.name,
                "pages": pngs,
                "page_count": len(pngs),
            })

    return select_primary_dirs(all_candidates)


def process_directory(dir_info: dict) -> dict:
    """OCR all pages in a directory."""
    pages = []
    for png in dir_info["pages"]:
        page_num = int(png.stem.split("_")[-1])
        lines = ocr_image(png)
        text = lines_to_text(lines)
        pages.append({
            "page": page_num,
            "image": str(png.relative_to(BASE_DIR)),
            "text": text,
            "line_count": len(lines),
            "avg_confidence": round(
                sum(l["confidence"] for l in lines) / len(lines), 3
            ) if lines else 0,
        })
    return {
        "year": dir_info["year"],
        "session": dir_info["session"],
        "subject": dir_info["subject"],
        "subject_ja": dir_info["subject_ja"],
        "dir_name": dir_info["dir_name"],
        "page_count": len(pages),
        "pages": pages,
    }


def _save_results(all_results, processed_pages, total_time):
    """Save OCR results to JSON."""
    output = {
        "description": "OCR-extracted text from EJU exam images (PaddleOCR v3.3.3 Japanese model)",
        "total_directories": len(all_results),
        "total_pages": processed_pages,
        "processing_time_seconds": round(total_time, 1),
        "results": all_results,
    }
    out_path = JSON_DIR / "ocr_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"  -> Saved {out_path.name} ({out_path.stat().st_size // 1024} KB)")


def main():
    to_process = get_dirs_to_process()
    total_pages = sum(d["page_count"] for d in to_process)
    print(f"\nWill OCR {len(to_process)} directories ({total_pages} pages)")
    print(f"Estimated time: ~{total_pages * 5 / 60:.0f} min\n")

    for d in to_process:
        print(f"  {d['dir_name']}: {d['page_count']} pages ({d['subject']})")

    all_results = []
    processed_pages = 0
    t_start = time.time()

    for i, dir_info in enumerate(to_process):
        label = f"[{i+1}/{len(to_process)}] {dir_info['dir_name']}"
        print(f"\n{label} ({dir_info['page_count']} pages)...", flush=True)
        t0 = time.time()

        result = process_directory(dir_info)
        all_results.append(result)

        elapsed = time.time() - t0
        processed_pages += dir_info["page_count"]
        avg_conf = sum(p["avg_confidence"] for p in result["pages"]) / len(result["pages"]) if result["pages"] else 0
        eta = (time.time() - t_start) / processed_pages * (total_pages - processed_pages)
        print(f"  Done {elapsed:.0f}s, conf={avg_conf:.2f}, progress={processed_pages}/{total_pages}, ETA={eta/60:.0f}min", flush=True)

        # Save every 5 directories
        if (i + 1) % 5 == 0:
            _save_results(all_results, processed_pages, time.time() - t_start)

    total_time = time.time() - t_start
    print(f"\n{'='*60}")
    print(f"OCR complete: {processed_pages} pages in {total_time:.0f}s ({total_time/60:.1f} min)")
    print(f"Average: {total_time/processed_pages:.1f}s per page")

    _save_results(all_results, processed_pages, total_time)


if __name__ == "__main__":
    main()
