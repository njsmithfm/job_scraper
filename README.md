Details print to the terminal (tab-separated, paste straight into your
spreadsheet). Each page is saved as a PDF in ./pdfs/ named
CompanyName_JobTitle.pdf.

Requires:
  uv add requests beautifulsoup4 playwright
  uv run playwright install chromium

## Usage

`uv run python job_scraper.py URL1 [URL2 ...]`
