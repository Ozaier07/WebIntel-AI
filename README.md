# WebIntel AI - Python (FastAPI) backend
Windows: double-click run_windows.bat   |   Mac/Linux: bash run.sh
Manual: pip install -r requirements.txt  then  python -m uvicorn main:app --reload
Open http://127.0.0.1:8000  (API docs: /docs). Always open it through this address, not the .html file.

Test first with the "Use built-in sample page" button, then try https://quotes.toscrape.com
Troubleshooting: "refused (403)" = site blocks bots; "robots.txt" = site forbids scraping; use another page.
