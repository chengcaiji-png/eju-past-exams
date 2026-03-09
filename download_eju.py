#!/usr/bin/env python3
"""Download EJU past exam papers from JASSO official site and third-party sources."""

import os
import re
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, unquote
from pathlib import Path

BASE_DIR = Path(__file__).parent
JASSO_DIR = BASE_DIR / "jasso"
CAROBOOK_DIR = BASE_DIR / "carobook"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.9",
}

# JASSO sessions (year, session_number)
JASSO_SESSIONS = [
    (2010, 1),
    (2011, 1), (2011, 2),
    (2012, 1), (2012, 2),
    (2013, 1), (2013, 2),
    (2014, 1), (2014, 2),
    (2015, 1), (2015, 2),
    (2016, 1), (2016, 2),
    (2017, 1), (2017, 2),
    (2018, 1), (2018, 2),
    (2019, 1),
    (2020, 2),
    (2021, 1),
]

# URL patterns for JASSO - both JP and EN pages
JASSO_URL_TEMPLATES = [
    "https://www.jasso.go.jp/ryugaku/eju/examinee/pastpaper_sample/pastpaper_{year}_{session}.html",
    "https://www.jasso.go.jp/en/ryugaku/eju/examinee/pastpaper_sample/pastpaper_{year}_{session}.html",
]


def download_file(url: str, dest: Path, session: requests.Session) -> bool:
    """Download a file if it doesn't already exist. Returns True if downloaded."""
    if dest.exists() and dest.stat().st_size > 1024:
        return False
    try:
        resp = session.get(url, headers=HEADERS, timeout=30, stream=True)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if "pdf" not in content_type and "octet" not in content_type:
            # Check if it's actually a PDF by magic bytes
            first_bytes = next(resp.iter_content(chunk_size=8), b"")
            if not first_bytes.startswith(b"%PDF"):
                print(f"  SKIP (not PDF): {url}")
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                f.write(first_bytes)
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
        print(f"  OK: {dest.name} ({dest.stat().st_size // 1024} KB)")
        return True
    except Exception as e:
        print(f"  FAIL: {url} -> {e}")
        return False


def scrape_jasso_session(year: int, session: int, s: requests.Session) -> int:
    """Scrape a single JASSO session page for PDF links. Returns count of new downloads."""
    session_label = f"{year}_第{session}回"
    out_dir = JASSO_DIR / session_label
    out_dir.mkdir(parents=True, exist_ok=True)

    pdf_urls = {}  # url -> filename, deduplicated

    for template in JASSO_URL_TEMPLATES:
        url = template.format(year=year, session=session)
        try:
            resp = s.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.lower().endswith(".pdf"):
                    full_url = urljoin(url, href)
                    filename = unquote(full_url.split("/")[-1])
                    pdf_urls[full_url] = filename
            time.sleep(1)
        except Exception as e:
            print(f"  Error fetching {url}: {e}")

    if not pdf_urls:
        print(f"[{session_label}] No PDFs found on page")
        return 0

    print(f"[{session_label}] Found {len(pdf_urls)} PDFs")
    downloaded = 0
    for pdf_url, filename in sorted(pdf_urls.items()):
        dest = out_dir / filename
        if download_file(pdf_url, dest, s):
            downloaded += 1
            time.sleep(0.5)
    return downloaded


def download_jasso():
    """Download all JASSO official past papers (2010-2021)."""
    print("=" * 60)
    print("Part A: JASSO Official Past Papers (2010-2021)")
    print("=" * 60)

    s = requests.Session()
    total = 0
    for year, session in JASSO_SESSIONS:
        count = scrape_jasso_session(year, session, s)
        total += count

    print(f"\nJASSO total new downloads: {total}")


def try_carobook(s: requests.Session) -> int:
    """Try downloading from exam.carobook.com."""
    print("\nTrying exam.carobook.com...")
    base = "https://exam.carobook.com"
    downloaded = 0

    # Try main page and subject pages
    try:
        resp = s.get(base, headers=HEADERS, timeout=15)
        if resp.status_code == 403:
            print("  403 Forbidden - site blocks automated access")
            return 0
        if resp.status_code != 200:
            print(f"  HTTP {resp.status_code}")
            return 0

        soup = BeautifulSoup(resp.text, "html.parser")
        # Look for EJU-related links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if "eju" in href.lower() or "留学" in text:
                print(f"  Found link: {text} -> {href}")
                full_url = urljoin(base, href)
                # Follow the link to find PDFs
                try:
                    sub_resp = s.get(full_url, headers=HEADERS, timeout=15)
                    if sub_resp.status_code == 200:
                        sub_soup = BeautifulSoup(sub_resp.text, "html.parser")
                        for pdf_a in sub_soup.find_all("a", href=True):
                            if pdf_a["href"].lower().endswith(".pdf"):
                                pdf_url = urljoin(full_url, pdf_a["href"])
                                filename = unquote(pdf_url.split("/")[-1])
                                # Try to determine year/session from filename or path
                                dest = CAROBOOK_DIR / filename
                                dest.parent.mkdir(parents=True, exist_ok=True)
                                if download_file(pdf_url, dest, s):
                                    downloaded += 1
                                time.sleep(0.5)
                except Exception as e:
                    print(f"  Error: {e}")
                time.sleep(1)
    except Exception as e:
        print(f"  Error: {e}")

    return downloaded


def try_geetbook(s: requests.Session) -> int:
    """Try downloading from geetbook.com."""
    print("\nTrying geetbook.com...")
    base = "https://geetbook.com"
    downloaded = 0

    try:
        resp = s.get(base, headers=HEADERS, timeout=15)
        if resp.status_code == 403:
            print("  403 Forbidden - site blocks automated access")
            return 0
        if resp.status_code != 200:
            print(f"  HTTP {resp.status_code}")
            return 0

        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if "eju" in href.lower() or "留学" in text:
                print(f"  Found link: {text} -> {href}")
    except Exception as e:
        print(f"  Error: {e}")

    return downloaded


def download_thirdparty():
    """Try third-party sites for 2022+ papers."""
    print("\n" + "=" * 60)
    print("Part B: Third-party Sites (2022-2025)")
    print("=" * 60)

    CAROBOOK_DIR.mkdir(parents=True, exist_ok=True)
    s = requests.Session()

    total = try_carobook(s)
    if total == 0:
        total = try_geetbook(s)

    if total == 0:
        print("\nNo third-party downloads succeeded.")
        print("For 2022+ papers, try manually:")
        print("  - https://exam.carobook.com")
        print("  - https://geetbook.com")
        print("  - JASSO sells printed booklets: https://www.jasso.go.jp/ryugaku/eju/examinee/pastpaper_sample/")

    print(f"\nThird-party total new downloads: {total}")


def verify_downloads():
    """Verify downloaded PDFs and print summary."""
    print("\n" + "=" * 60)
    print("Download Summary")
    print("=" * 60)

    total_files = 0
    total_size = 0
    small_files = []

    for source_dir in [JASSO_DIR, CAROBOOK_DIR]:
        if not source_dir.exists():
            continue
        print(f"\n{source_dir.name}/")
        for session_dir in sorted(source_dir.iterdir()):
            if not session_dir.is_dir():
                continue
            pdfs = list(session_dir.glob("*.pdf"))
            size = sum(f.stat().st_size for f in pdfs)
            print(f"  {session_dir.name}: {len(pdfs)} files, {size // 1024} KB total")
            total_files += len(pdfs)
            total_size += size
            for f in pdfs:
                if f.stat().st_size < 10240:
                    small_files.append(f)

    print(f"\nTotal: {total_files} PDF files, {total_size // (1024*1024)} MB")
    if small_files:
        print(f"\nWARNING: {len(small_files)} files < 10KB (may be error pages):")
        for f in small_files:
            print(f"  {f} ({f.stat().st_size} bytes)")


if __name__ == "__main__":
    download_jasso()
    download_thirdparty()
    verify_downloads()
