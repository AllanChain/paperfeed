# paperfeed

Daily personalized arXiv feed delivered as a Feishu card. It is a single CLI run
(`paperfeed`: fetch → rank → describe → send) that an external scheduler invokes;
the repo ships GitHub Actions workflows as one reference setup. There is no
server, database, or cross-run state.

Current defaults, prompts, and behavior live in the code and `README.md`, not here.

## Verify

```bash
uv sync
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest -q          # offline; fixtures live in tests/fixtures
uv run paperfeed --check  # credentials, models, Zotero access
uv run paperfeed --dry-run
```

## Where to look

| Area | Code |
|---|---|
| Settings, env names, defaults | `src/paperfeed/config.py` |
| Zotero collection and profile loading | `src/paperfeed/zotero.py` |
| Profile-note HTML parsing | `src/paperfeed/profile_note.py` |
| arXiv HTML listing fetch, section filter, dedupe | `src/paperfeed/arxiv_source.py` |
| Per-paper full-text HTML fetch and body extraction | `src/paperfeed/full_text.py` |
| Ranking payload, levels, score math, selection | `src/paperfeed/rank.py` |
| Description prompt and failure handling | `src/paperfeed/describe.py` |
| Bailian decision/chat client (one key, two transports) | `src/paperfeed/bailian.py` |
| Feishu card layout and delivery | `src/paperfeed/feishu.py` |
| LaTeX → Unicode for card text | `src/paperfeed/mathfmt.py` |
| `--check` diagnostics | `src/paperfeed/doctor.py` |
| Shared client helper, Bailian retries; webhook sends once | `src/paperfeed/net.py` |
| Bounded thread-pool map for per-item calls | `src/paperfeed/concurrency.py` |
| CLI and run order | `src/paperfeed/main.py` |

User-facing setup, tuning, and troubleshooting: `README.md`.

## Invariants

- Candidates come from `/list/<category>/new`: only the new and cross sections, never
  replacements. The cap is per category, applied before cross-category dedupe.
  Listing requests are 15 s apart (arXiv's `robots.txt`); ranking runs between
  pages and usually covers the wait. A 429 means arXiv's per-IP limit is busy
  (cloud and shared IPs often are), so the GET is retried with a 30 s-first
  backoff; only a refusal that survives 3 attempts fails the run.
- A missing or invalid profile note is a hard failure; do not invent a generic feed.
- Reportable failures are logged without a traceback and exit 1; config errors exit 2.
- Ranking is one call per paper; a per-paper failure scores 0 and does not fail the run.
- Full text is for descriptions only, and only for papers that survive `select_top`.
  A missing body (no arXiv HTML, timeout, stub page) falls back to the abstract; it is
  never a run failure and never a reason to drop a paper.
- The Feishu card is sent exactly once. Never retry the webhook: a retried POST can duplicate the card.
- No cross-run state, embeddings, or keyword prefilter. There is no local copy of a
  paper body: full text is fetched, used for one chat call, and dropped. Fetching the
  e-print LaTeX source instead was measured and rejected — 1.2-4.3x more characters
  than the HTML body, and stripping it needs tar/zip detection plus regexes that
  silently delete prose.
- Credentials come from env/.env only; `.env` is gitignored.
- Tests must stay offline; use fixtures or injected clients.
