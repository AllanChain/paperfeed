# paperfeed

Daily personalized arXiv feed delivered as a Feishu card. It runs from a GitHub
Actions cron; there is no server, database, or cross-run state.

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
| arXiv RSS fetch, announce filter, dedupe | `src/paperfeed/arxiv_source.py` |
| Ranking payload, levels, score math, selection | `src/paperfeed/rank.py` |
| Description prompt and failure handling | `src/paperfeed/describe.py` |
| Feishu card layout and delivery | `src/paperfeed/feishu.py` |
| LaTeX → Unicode for card text | `src/paperfeed/mathfmt.py` |
| `--check` diagnostics | `src/paperfeed/doctor.py` |
| Shared HTTP mechanics (Bailian retries; webhook sends once) | `src/paperfeed/net.py` |
| Bounded thread-pool map for per-item calls | `src/paperfeed/concurrency.py` |
| CLI and run order | `src/paperfeed/main.py` |

User-facing setup, tuning, and troubleshooting: `README.md`.

## Invariants

- A missing or invalid profile note is a hard failure; do not invent a generic feed.
- Reportable failures are logged without a traceback and exit 1; config errors exit 2.
- Ranking is one call per paper; a per-paper failure scores 0 and does not fail the run.
- The Feishu card is sent exactly once. Never retry the webhook: a retried POST can duplicate the card.
- No cross-run state, embeddings, or keyword prefilter.
- Credentials come from env/.env only; `.env` is gitignored.
- Tests must stay offline; use fixtures or injected clients.
