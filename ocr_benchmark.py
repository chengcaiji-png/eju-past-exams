#!/usr/bin/env python3
"""Benchmark OCR tools on EJU exam images (Japanese + math formulas)."""

import time
from pathlib import Path

IMG_DIR = Path(__file__).parent / "images"

# Test images: math (formulas), science (Japanese text), JW (mixed)
TEST_IMAGES = [
    IMG_DIR / "2016_1/2016_1question_math/page_004.png",      # Math with formulas
    IMG_DIR / "2016_1/2016_1question_science/page_004.png",    # Science (Japanese)
    IMG_DIR / "2016_1/2016_1question_jw/page_004.png",         # Japan & World (Japanese)
    IMG_DIR / "2019_1/2019_1question_math/page_004.png",       # Math newer year
]

def test_tesseract():
    """Test Tesseract with Japanese language pack."""
    import pytesseract
    from PIL import Image
    print("=" * 60)
    print("TESSERACT (jpn)")
    print("=" * 60)
    for img_path in TEST_IMAGES:
        if not img_path.exists():
            print(f"  SKIP (not found): {img_path.name}")
            continue
        t0 = time.time()
        img = Image.open(img_path)
        text = pytesseract.image_to_string(img, lang="jpn")
        elapsed = time.time() - t0
        lines = [l for l in text.strip().split("\n") if l.strip()]
        print(f"\n--- {img_path.parent.name}/{img_path.name} ({elapsed:.1f}s) ---")
        for line in lines[:20]:
            print(f"  {line}")
        if len(lines) > 20:
            print(f"  ... ({len(lines)} lines total)")
    print()


def test_paddleocr():
    """Test PaddleOCR with Japanese model."""
    import os
    os.environ["DISABLE_MODEL_SOURCE_CHECK"] = "True"
    from paddleocr import PaddleOCR
    print("=" * 60)
    print("PADDLEOCR (japan)")
    print("=" * 60)
    ocr = PaddleOCR(use_angle_cls=True, lang="japan", show_log=False)
    for img_path in TEST_IMAGES:
        if not img_path.exists():
            print(f"  SKIP (not found): {img_path.name}")
            continue
        t0 = time.time()
        result = ocr.ocr(str(img_path), cls=True)
        elapsed = time.time() - t0
        print(f"\n--- {img_path.parent.name}/{img_path.name} ({elapsed:.1f}s) ---")
        if result and result[0]:
            lines = []
            for item in result[0]:
                box, (text, conf) = item[0], item[1]
                lines.append(f"  [{conf:.2f}] {text}")
            for line in lines[:25]:
                print(line)
            if len(lines) > 25:
                print(f"  ... ({len(lines)} detections total)")
    print()


if __name__ == "__main__":
    print("Testing available OCR tools on EJU exam images...\n")

    # Test Tesseract
    try:
        test_tesseract()
    except Exception as e:
        print(f"Tesseract failed: {e}\n")

    # Test PaddleOCR
    try:
        test_paddleocr()
    except Exception as e:
        print(f"PaddleOCR failed: {e}\n")
