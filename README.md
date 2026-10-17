# paperfeed

> **Warning: this project is fully AI-generated.** Its source code, tests, and
> documentation were produced by an AI and have not been independently reviewed.
> Review it before relying on it, run the checks below, and use it at your own
> risk. Treat it as a starting point, not a vetted product.

A daily, personalized arXiv feed, delivered as one styled Feishu (Lark) card.
Interests live in a Zotero collection plus a single profile note; ranking is
done by a decision model, descriptions by an LLM, and the whole thing runs on a
GitHub Actions cron. No server, no database, no state.

## How it works

1. **Zotero** — fetch the configured collection's top-level items. Bibliographic
   items are the positives (and prompt anchors); the standalone note whose title is
   `PAPERFEED_ZOTERO__NOTE_TITLE` is the profile: `Focus`, `Topics`, `Methods`,
   `Avoid`, `Style`, `Language`.
2. **arXiv** — one RSS fetch per category, keeping `new` and `cross` announcements
   and dropping replacements. Cross-listed papers are deduped by arXiv id.
3. **Rank** — one `decision-model-preview` (System One) call per paper, two
   questions: relevance as a 4-level `score` (expected level index ÷ 3) and
   `avoid` as a `noul` probability. `final = max(0, relevance − 0.5 · P(avoid))`.
4. **Describe** — one chat call per kept paper, prompted with the note's `Focus`
   and `Style`, so the paragraph reads as if written for this reader.
5. **Deliver** — one Feishu `interactive` card. If nothing clears `min_score`, no
   message is sent. arXiv titles arrive with LaTeX shards and `lark_md` has no
   LaTeX, so math is rendered as Unicode on the way in (`La$_6$Ni$_5$O$_{12}$` →
   `La₆Ni₅O₁₂`, `$\nu=2/3$` → `ν=2/3`); anything exotic degrades to readable text
   instead of showing raw TeX.

## Setup

### 1. Zotero

1. Create a collection at the configured path (default `paperfeed/interests`).
2. Add papers you like — they become ranking anchors.
3. Create an API key at <https://www.zotero.org/settings/keys> with read access and
   copy the **numeric** user ID shown on that page (not your username — the API
   path is `/users/<id>/`, and a username fails with `Invalid user ID`).
4. Add a **standalone note** in that collection whose first line is
   `paperfeed profile` (an `h1`), followed by the sections in the template below.
   The note's first line is its title in Zotero's UI, which is what the tool
   matches on; if the collection holds exactly one note, the title match is
   skipped.

   Zotero's note editor is rich text: pasting HTML source leaves the tags as
   literal text. Use the rendered template below instead; copying the rendered
   view pastes the headings and bullets into Zotero as HTML, which is what
   paperfeed reads.

The template's first line must match `PAPERFEED_ZOTERO__NOTE_TITLE` (default
`paperfeed profile`). Expand it, select from `paperfeed profile` through the last
line, and paste; replace the example values with your own but keep the section
headings as written.

<details>
<summary><strong>Profile note template</strong> — expand, then copy the rendered note</summary>

# paperfeed profile

## Focus

computational physics; nonequilibrium dynamics in quantum materials

## Topics

- tensor networks
- quantum Monte Carlo
- strongly correlated electrons & magnetism

## Methods

- DMRG
- QMC
- dynamical mean-field theory

## Avoid

- experimental synthesis
- device engineering
- purely classical ML

## Style

State what quantity is measured/computed and why it matters. Omit apparatus
detail. One paragraph, concrete, no hype.

## Language

English

</details>

After pasting, the note list should show the configured title (default
`paperfeed profile`) and the editor should show heading styles, not `<h1>` tags.
If you see tags, undo and copy the rendered view instead of the source. If your
browser still flattens the headings, apply `Heading 1` to the title and
`Heading 2` to each section from the note editor's style menu.

`Focus` and `Style` are required; a note missing either fails the run rather than
producing a generic feed.

### 2. Feishu

Add a custom bot to the target group chat and copy its webhook URL. No signature
verification is used: anyone with the URL can post to the chat.

### 3. Bailian

Create an API key in Alibaba Cloud Model Studio (Bailian). One key serves both
roles: `POST {base_url}/systemone` for ranking and `{base_url}/chat/completions`
for descriptions. The default base URL is the public DashScope-compatible endpoint;
if your models are only exposed on a private workspace host, set
`PAPERFEED_BAILIAN__BASE_URL` to that host.

### 4. Local run

```bash
cp .env.example .env     # fill in the four credentials
uv sync
uv run paperfeed --check     # proves host, key, model ids, Zotero access
uv run paperfeed --dry-run   # prints the card JSON, sends nothing
uv run paperfeed             # sends
```

### 5. GitHub Actions

Push the repo, then add:

| Kind | Name | Value |
|---|---|---|
| Secret | `ZOTERO_ID` | numeric Zotero user ID |
| Secret | `ZOTERO_KEY` | Zotero API key |
| Secret | `BAILIAN_KEY` | Model Studio API key |
| Secret | `FEISHU_WEBHOOK` | custom-bot webhook URL |
| Variable | `PAPERFEED_BAILIAN__BASE_URL` | optional; defaults to the public DashScope-compatible endpoint. Set your workspace host if your models are not public. |
| Variable | `PAPERFEED_ZOTERO__COLLECTION` | e.g. `paperfeed/interests` |
| Variable | `PAPERFEED_ZOTERO__NOTE_TITLE` | e.g. `paperfeed profile` |
| Variable | `PAPERFEED_ARXIV__CATEGORIES` | JSON list, e.g. `["cond-mat.str-el","quant-ph"]` |

`ci.yml` runs lint, formatting, type checks, and tests on every push and pull
request. `daily.yml` runs the real feed at 05:00 UTC Monday–Friday and on manual
`workflow_dispatch`; it never runs on pushes, so a push cannot send a card. Run
the daily workflow once by hand and confirm the card renders before trusting the
cron.

## Tuning

| Knob | Effect |
|---|---|
| `PAPERFEED_MAX_PAPERS` | how many papers reach the card (default 10) |
| `PAPERFEED_MIN_SCORE` | relevance threshold after the avoid penalty (default 0.60 ≈ level 1.8 of 3) |
| `PAPERFEED_AVOID_PENALTY` | weight of `P(avoid)` (default 0.5) |
| `PAPERFEED_ARXIV__MAX_CANDIDATES` | candidate cap before scoring (default 300) |
| note `Topics` / `Methods` / `Avoid` | the ranking rubric itself |
| note `Style` | the description rubric itself |

**Calibration.** `uv run paperfeed --check` validates plumbing, not judgement. To
check judgement, temporarily score the papers already in the collection (they are
the reader's own positives) and confirm they clear `min_score`. If they do not, the
level criteria need fixing — not the reader's taste. The threshold is a precision
knob; the criteria wording is the lever, so tighten the levels before raising
`min_score`.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `--check` says `Invalid user ID` | `PAPERFEED_ZOTERO__USER_ID` holds a username; use the numeric ID |
| `--check` says `no note titled …` | the note's first line is not exactly `NOTE_TITLE` (did you copy the template's instruction paragraph too?), and the collection has more than one note |
| `--check` says `profile note has no non-empty 'Focus' section` | the note was pasted as plain text or raw HTML, so Zotero lost the headings; copy the rendered template from the dropdown instead |
| `--check` chat fails with 400 | your model id rejects `chat_extra_body`; set `PAPERFEED_BAILIAN__CHAT_EXTRA_BODY={}` |
| A run keeps nothing | `min_score` too high for the profile, or the note's `Topics` are too narrow |

## Development

```bash
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest -q
```

Tests never touch the network: RSS, the profile note and a recorded System One
response live in `tests/fixtures/`. `PAPERFEED_DEBUG=true` trims ranking to 10
candidates and turns on verbose logging.

## License

Apache-2.0. See [LICENSE](LICENSE).
