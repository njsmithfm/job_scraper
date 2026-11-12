#!/usr/bin/env python3
"""
job_scraper.py — scrape job postings into tracker-ready TSV lines.

Usage:
    python job_scraper.py URL1 [URL2 ...]
    python job_scraper.py URL1 >> jobs.tsv   (to append to a file)

Parses schema.org JobPosting JSON-LD (used by most job boards, incl.
cityjobs.nyc.gov), with meta-tag fallback. Output columns match the
tracker: Company, Position, Link, Location, Type, Mode,
Min Salary, Deadline, Folder, Applied?, Date Applied, Outcome?, Notes
"""

import json
import re
import sys

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

COLUMNS = [
    "Company", "Position", "Link", "Location", "Type",
    "Mode", "Min Salary", "Deadline", "Folder", "Applied?",
    "Date Applied", "Outcome?", "Notes",
]


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def iter_jsonld(html):
    """Yield every JSON-LD object on the page, recursing into graphs/lists."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
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


def scrape(url):
    html = fetch(url)
    job = next(iter_jsonld(html), None)

    if job:
        org = job.get("hiringOrganization") or {}
        return {
            "Company": org.get("name", "") if isinstance(org, dict) else str(org),
            "Position": job.get("title", ""),
            "Link": url,
            "Location": first_city(job),
            "Type": employment_type(job),
            "Min Salary": salary(job, html),
            "Deadline": job.get("validThrough", ""),
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
    rows = []
    for url in sys.argv[1:]:
        try:
            rows.append(scrape(url))
        except Exception as e:
            print(f"# ERROR fetching {url}: {e}", file=sys.stderr)
    rows.sort(key=lambda r: r["Position"].lower())
    print("\t".join(COLUMNS))
    for r in rows:
        print("\t".join(
            r.get(c, "") for c in COLUMNS
        ))


if __name__ == "__main__":
    main()