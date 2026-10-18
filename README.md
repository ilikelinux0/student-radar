# Student Radar

Student Radar watches company career pages, job boards, program pages, and news for internships, new-grad and talent programs, scholarships, bootcamps, competitions, and events. Once a day a GitHub Action checks what is due, keeps items that match your keywords, and asks Gemini how well each one fits you. Code then checks each posting's requirements (age, graduation date, GPA, English level, citizenship, military service) against your profile and marks it eligible, unclear, or ineligible. A small web page lists the results, with what is closing soon at the top.

Your profile stays yours. Only a short `fit_summary` is sent to the model. The facts are compared by code and are never sent, stored, exported, or logged.

## Set up your own

> [!WARNING]
> **Make your copy private.** The daily job commits its results (`data/radar.db` and `web/public/data/`) to your repository, and those contain notes on how each posting fits you. In a public repository anyone can read them.

1. **Copy the repository.** Click **Use this template** → **Create a new repository**, and choose **Private**. (A fork of a public repository cannot be private, so use the template button, not Fork.)
2. **Get a Gemini API key.** It is free at [Google AI Studio](https://aistudio.google.com/apikey). Also copy a model id from AI Studio (for example a Flash-Lite model).
3. **Write your profile.** Copy `profile.example.yaml` to `profile.yaml` and fill it in. `profile.yaml` is gitignored, so it stays on your machine.
4. **Add three secrets.** In your repository: **Settings → Secrets and variables → Actions → New repository secret**.

   | Secret | Value |
   | --- | --- |
   | `GEMINI_API_KEY` | your key from step 2 |
   | `GEMINI_MODEL` | the model id from step 2 |
   | `RADAR_PROFILE` | the whole contents of your `profile.yaml` |

5. **Point it at your field.** `settings.yaml` holds the keywords and search phrases, and `sources.yaml` holds the pages and boards to watch. The checked-in lists are aimed at cybersecurity and software roles in Turkey. Change `keywords`, `keyword_groups`, and `discovery_queries` to your field, and enable, disable, or add sources ([below](#add-a-source)). Why each current source is there is in [docs/SOURCES.md](docs/SOURCES.md).
6. **Run it once.** **Actions → sweep → Run workflow**, tick *force*, and run. If the Actions tab asks you to enable workflows, enable them. After that it runs every day at 06:00 UTC.
7. **Look at the results.** Pull the repository and open the reader (below). The first run stores keyword hits, and Gemini scores at most 30 a day, so the fit and eligibility views fill in over the first few days.

To open the reader from your phone, host it on Cloudflare Pages behind Cloudflare Access so only your email can open it. The steps are in [docs/SETUP.md](docs/SETUP.md).

Never commit `profile.yaml`, `.env`, or your CV. `.gitignore` already covers `profile.yaml`, `.env`, and `*_CV.pdf`.

## Open the reader

Install [Node.js](https://nodejs.org/) 22, then from the repository:

```powershell
git pull
cd web
npm install
npm run dev
```

Open the address it prints. Marks you set (interested, applied, interview, and the rest) stay in that browser. Export them from the sidebar if you want a copy.

## Run the sweep on your computer

Optional. Install [uv](https://docs.astral.sh/uv/) and Python 3.12, then:

```powershell
uv sync
copy .env.example .env   # then fill in GEMINI_API_KEY and GEMINI_MODEL
uv run python -m radar.sweep --dry-run --force
uv run python -m radar.sweep --force
```

`--dry-run` fetches and prints what it would store. It does not write anything or call the model. A real run writes `data/radar.db` and `web/public/data/`. If the daily job is running, leave those files to it and `git pull` instead of committing a local copy.

Locally the sweep reads `profile.yaml`. In GitHub it reads the `RADAR_PROFILE` secret. `.env` is loaded for any name that is not already set, and its values are never logged.

Other flags: `--source ID` checks one source, `--force` ignores the daily or weekly wait, `--no-llm` stores keyword hits without scoring.

Checks: `uv run ruff check .` and `uv run pytest -q`, and from `web/`: `npm test`, `npm run lint`, `npm run build`.

## How a posting is judged

1. **Keyword gate.** A provider page (`rss`, `api`, or `scrape`) is kept when its title or summary contains any phrase in `keywords`. A search source needs a phrase in the title, or two phrases in the title and summary. A source with `require: [level, tech]` keeps an item only when both named groups in `keyword_groups` hit. `require_title` checks the title only. Turkish letters are folded before matching.
2. **Fit score.** The model scores fit from 0 to 10 using your `fit_summary`, and copies the posting's requirements and deadline, with the words the deadline came from. A deadline whose quote is not in the page is dropped.
3. **Eligibility.** Code compares the requirements with your profile facts. A posting shows as eligible, unclear (with what to check), or ineligible (with why).

`max_llm_items_per_run` caps how many items are scored per run (30 in `settings.yaml`). The rest wait for the next run. `llm_batch_size` items share one request. `scorer_provider` is `gemini`, `bedrock` (see [docs/SETUP.md](docs/SETUP.md)), or `none`, which stores keyword hits without calling a model.

## Add a source

Each entry in `sources.yaml` needs an `id`, `name`, `type`, and `frequency` (`daily` or `weekly`). `rss`, `api`, and `scrape` also need a `url`. Disabled entries (`enabled: false`) are never fetched. A source that errors is recorded and the run continues. The reader's Source health page lists failing sources first.

**Program page.** A single page, compared with the last check. Most programs say applications have not opened until they do, and the change is what gets picked up. `usually_opens` is shown in the sidebar as a reminder.

```yaml
- id: example-program
  name: Example talent program
  url: https://example.com/talent-program
  type: scrape
  frequency: weekly
  usually_opens: Jun–Jul
```

**Listing of cards.** Add a `selector` that matches one card, and each new or changed card becomes an item. `js: true` renders the page in Chromium when it is built by script.

```yaml
- id: example-listings
  name: Example listings
  url: https://example.com/jobs
  type: scrape
  frequency: daily
  selector: li.opening-job
  require: [tech]
```

**Job board API** (Lever, Greenhouse, and similar). `items_path` is a dot path to the list when it is not the top level, and `fields` names the keys. `closes_missing: true` marks a posting closed when it disappears from the board.

```yaml
- id: example-jobs
  name: Example jobs
  url: https://api.lever.co/v0/postings/example?mode=json
  type: api
  frequency: daily
  fields: {title: text, url: hostedUrl, summary: descriptionPlain}
  require_title: [level, tech]
  closes_missing: true
```

**RSS feed.** The title, link, and summary become the item.

```yaml
- id: example-rss
  name: Example feed
  url: https://example.com/feed.xml
  type: rss
  frequency: daily
```

**Searches.** These do not use `url`. They search the phrases in `discovery_queries` (`query_group: en` or `tr`) plus any extra `queries` on the source. `max_new_items_per_run` limits how many search results one run stores.

```yaml
- id: example-news
  name: Google News search
  type: news_search
  lang: tr-TR
  query_group: tr
  frequency: daily

- id: example-hn
  name: Hacker News search
  type: hn_search
  query_group: en
  frequency: daily
```

Other types: `reddit_search` (with `subreddits`), `github_watch` (with `repo: owner/name`, watches a README), and `youtube_channel` (with `channel_id`, keeps only the transcript minutes that mention a keyword).

Tracking parameters such as `utm_*` are stripped before an item is stored, and `max_items_per_source` (default 20) limits each source per run.
