#!/usr/bin/env python3
"""
job_scraper.py — scrape job postings and save a PDF of each page.

Usage:
    uv run python job_scraper.py URL1 [URL2 ...]

Details print to the terminal (tab-separated, paste straight into your
spreadsheet). Each page is saved as a PDF in ./pdfs/ named
CompanyName_JobTitle.pdf. If the URL already points at a PDF (including
Google Drive /view links), the file is downloaded as-is and its text
is scraped instead of rendering with Playwright.

Requires (deps are in pyproject.toml):
    uv add requests beautifulsoup4 playwright pypdf
    uv run playwright install chromium
"""

import io
import json
import re
import sys
from pathlib import Path

import pypdf
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

COLUMNS = [
    "Company",
    "Position",
    "Link",
    "Location",
    "Type",
    "Mode",
    "Min Salary",
    "Deadline",
]

PDF_DIR = Path("pdfs")


def fetch_response(url):
    return requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)


def is_pdf_response(resp):
    ctype = resp.headers.get("Content-Type", "")
    return "pdf" in ctype.lower() or resp.content[:5] == b"%PDF-"


def drive_direct_url(url):
    """Convert a Google Drive /view link to a direct-download link."""
    m = re.search(r"drive\.google\.com/file/d/([^/]+)", url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    return url


def slug(text):
    """Make text safe for a filename: CompanyName_JobTitle convention."""
    text = re.sub(r'[\\/:*?"<>|]', "", text or "").strip()
    return re.sub(r"\s+", "_", text)


def save_pdf(url, path):
    """Render the page in headless Chromium and print it to a PDF."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.emulate_media(media="screen")
        page.pdf(path=str(path), format="Letter", print_background=True)
        browser.close()


def iter_jsonld(html):
    """Yield every JSON-LD object on the page, recursing into graphs/lists."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except json.JSONDecodeError, TypeError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if node.get("@type") == "JobPosting":
                    yield node
                for key in ("@graph", "itemListElement", "mainEntity"):
                    if key in node:
                        stack.append(node[key])


def first_city(obj):
    """Pull a location string out of whatever nesting the site uses."""

    def fmt(addr):
        if not addr:
            return ""
        if isinstance(addr, list):
            addr = addr[0]
        if isinstance(addr, str):
            return addr
        parts = [addr.get(k) for k in ("addressLocality", "addressRegion")]
        return ", ".join(p for p in parts if p)

    locs = obj.get("jobLocation")
    if isinstance(locs, list) and locs:
        locs = locs[0]
    if isinstance(locs, dict):
        return fmt(locs.get("address"))
    if isinstance(locs, str):
        return locs
    # applicantLocationRequirements is common on gov boards
    req = obj.get("applicantLocationRequirements")
    return fmt(req)


def salary(obj, html=None):
    """Return min salary as a string like '$76,385'. Falls back to $ in page text."""
    sal = obj.get("baseSalary") or obj.get("estimatedSalary")
    if sal:
        value = sal.get("value") or {}
        if isinstance(value, dict):
            amount = value.get("minValue") or value.get("value")
            unit = value.get("unitText", "")
        else:
            amount, unit = value, sal.get("unitText", "")
        if amount is not None:
            amount = float(amount)
            return f"${amount:,.0f}" + (f" ({unit})" if unit else "")
    # fallback: first dollar figure in visible text
    m = re.search(r"\$[\d,]{4,}(?:\.\d+)?(?:\s*(?:-\s*\$[\d,]+))?", html or "")
    return m.group(0) if m else ""


def employment_type(obj):
    t = obj.get("employmentType", "")
    if isinstance(t, list):
        t = t[0] if t else ""
    return t.replace("_", " ").title() if isinstance(t, str) else ""


def page_deadline(html):
    """Read 'Posted until MM/DD/YYYY' from the visible page text."""
    text = BeautifulSoup(html, "html.parser").get_text(" ")
    m = re.search(r"Posted until\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})", text)
    return m.group(1) if m else ""


def scrape_pdf(url, data):
    """Scrape a PDF's text and return a row whose _pdf_bytes is the raw file."""
    reader = pypdf.PdfReader(io.BytesIO(data))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    position = lines[0] if lines else ""

    m = re.search(
        r"(?:posted until|deadline|closing date|open until|apply by)"
        r"[:\s]*([A-Za-z]+ \d{1,2},? \d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        text,
        re.I,
    )
    deadline = m.group(1) if m else ""

    return {
        "Company": "",
        "Position": position,
        "Link": url,
        "Location": "",
        "Type": "",
        "Min Salary": salary({}, text),
        "Deadline": deadline,
        "_pdf_bytes": data,
    }


def scrape(url):
    resp = fetch_response(url)
    resp.raise_for_status()

    # Source is already a PDF: download it and scrape its text
    if is_pdf_response(resp):
        return scrape_pdf(drive_direct_url(url), resp.content)

    # Google Drive view links serve HTML first; retry as direct download
    if "drive.google.com" in url:
        resp = fetch_response(drive_direct_url(url))
        resp.raise_for_status()
        if is_pdf_response(resp):
            return scrape_pdf(url, resp.content)

    html = resp.text
    job = next(iter_jsonld(html), None)

    if job:
        org = job.get("hiringOrganization") or {}
        deadline = page_deadline(html) or job.get("validThrough", "")
        if "T" in deadline:
            deadline = deadline.split("T")[0]
        return {
            "Company": org.get("name", "") if isinstance(org, dict) else str(org),
            "Position": job.get("title", ""),
            "Link": url,
            "Location": first_city(job),
            "Type": employment_type(job),
            "Min Salary": salary(job, html),
            "Deadline": deadline,
        }

    # Fallback: meta/OpenGraph tags (weaker, varies by site)
    soup = BeautifulSoup(html, "html.parser")

    def meta(*names):
        for n in names:
            tag = soup.find("meta", attrs={"property": n}) or soup.find(
                "meta", attrs={"name": n}
            )
            if tag and tag.get("content"):
                return tag["content"]
        return ""

    title = meta("og:title", "twitter:title")
    return {
        "Company": meta("og:site_name"),
        "Position": title.split("|")[0].split(" - ")[0].strip() if title else "",
        "Link": url,
        "Location": "",
        "Type": "",
        "Min Salary": salary({}, html),
        "Deadline": "",
    }


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    PDF_DIR.mkdir(exist_ok=True)

    rows = []
    for url in sys.argv[1:]:
        try:
            row = scrape(url)
        except Exception as e:
            print(f"# ERROR fetching {url}: {e}", file=sys.stderr)
            continue
        rows.append(row)

        # PDF named CompanyName_JobTitle.pdf
        stem = f"{slug(row['Company'])}_{slug(row['Position'])}" or "job"
        path = PDF_DIR / f"{stem}.pdf"
        try:
            pdf_bytes = row.pop("_pdf_bytes", None)
            if pdf_bytes is not None:
                path.write_bytes(pdf_bytes)  # source was already a PDF
            else:
                save_pdf(url, path)
            print(f"# Saved PDF: {path}", file=sys.stderr)
        except Exception as e:
            print(f"# ERROR saving PDF for {url}: {e}", file=sys.stderr)

    def clean(v):
        return re.sub(r"[\t\r\n]+", " ", str(v))

    rows.sort(key=lambda r: r["Position"].lower())
    print("\t".join(COLUMNS))
    for r in rows:
        print("\t".join(clean(r.get(c, "")) for c in COLUMNS))


if __name__ == "__main__":
    main()
