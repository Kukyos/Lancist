# Lancist — Oryn Outreach Bot

Desktop app that finds local SMB websites, audits them, drafts personalised cold emails, and sends them via Gmail. Built for the Oryn web agency.

## What it does

- **Find Leads** — scrape Bing/DDG for businesses in a given category + city (currently biased to India)
- **Audit a site** — scrape its homepage, detect tech stack, missing features, score it
- **Draft an email** — LLM writes a short, specific cold email pitching only services that fit the detected category
- **Make a demo** — clone the site's structure into a quick mockup we can link in the email
- **Send via Gmail** — SMTP with an app password, logs to `emails.csv`

## Stack

- Python 3.11+
- CustomTkinter for the UI
- BeautifulSoup + requests for scraping
- Pluggable LLM backend (Anthropic API *or* any OpenAI-compatible endpoint — Ollama, LM Studio, vLLM, etc.)

## Running from source

```powershell
pip install -r requirements.txt
python main.py
```

First run: open **Settings**, configure either an Anthropic API key *or* an OpenAI-compatible base URL + model. Add Gmail address + app password for sending.

## Building the .exe

```powershell
.\build.ps1
```

Output lands in `dist/OrynOutreach/`. Ship the whole folder (not just the .exe — it needs `_internal/`).

## Project layout

| file | role |
| --- | --- |
| `main.py` | CustomTkinter UI, dialogs, app loop |
| `lead_finder.py` | Bing/DDG scraping with country + domain filters |
| `scraper.py` | Per-site homepage scrape + feature detection |
| `analyzer.py` | Category classification + scorecard + recommendations |
| `email_writer.py` | LLM prompt builder, returns subject/body JSON |
| `demo_writer.py` + `cloner.py` | Demo site generator |
| `smtp_sender.py` | Gmail SMTP send |
| `llm.py` | Provider abstraction (Anthropic / OpenAI-compatible) |
| `config.py` + `paths.py` | Settings + on-disk locations |
| `store.py` + `csv_log.py` | Lead persistence + send log |
| `theme.py` | Colors / fonts |

## Runtime data

`leads.json`, `emails.csv`, `config.json`, and `demos/` are created next to the .exe (or the source folder in dev). All gitignored.
