# Sources

Checked in `sources.yaml` on 2026-10-12. Each row was fetched with the project fetcher in `python -m radar.sweep --dry-run --force`. "Kept" is how many items passed the keyword gate on a fresh database. A source that returned text and kept nothing is still included: the next change can match.

The full run checked 31 sources, failed 0, and would insert 60. The polite client made 44 HTTP requests. Dry-run scores nothing. A real run scores at most 30 per day (`max_llm_items_per_run`) in batches of 5, so the first 60 take two days at about 6 Gemini requests each.

Not yet verified from GitHub Actions. Cloud runners are blocked by some sites (YouTube transcripts failed there on 2026-10-12, TÜBİTAK did not). The first CI run's health page is that check.

## How the sources work

- **Job boards** (`api`) read the company's public board API. The title must contain a level word (`require_title: [level]`), and for general tech companies a field word too (`[level, tech]`), because job descriptions say "graduate" or "öğrenci" for senior roles. A posting that disappears from its board is closed (`closes_missing`).
- **Career pages** (`scrape` with a `selector`) diff card by card. The first fetch yields one item per card; later fetches yield the new or changed cards.
- **Watched programs** (`scrape`, no selector) are single program pages. Most say "Başvurular henüz başlamadı" until applications open. The page diff fires when that text changes. `usually_opens` is what the program did in earlier years.

## Included

| id | group | type | why it is included | verification |
| --- | --- | --- | --- | --- |
| picus-jobs | jobs | api (Lever) | Turkish security company (BAS), runs the Cyber Talent Academy | fetched 14 postings, kept 0 (no early-career title today) |
| lostar-jobs | jobs | scrape `li.opening-job` | Says it has Turkey's largest pentest team; hires from camps and CTFs | 2 cards, kept 0 |
| brandefense-jobs | jobs | scrape `li.opening-job` | Turkish threat-intelligence company | 2 cards, kept 0 |
| trendyol-jobs | jobs | api (Lever) | Large Istanbul tech employer with internships | fetched 35, kept 0 under the title gate |
| dreamgames-jobs | jobs | api (Lever) | Istanbul, posts "(New Grad)" engineering roles | fetched 20, kept 2 |
| midas-jobs | jobs | api (Lever) | Istanbul fintech | fetched 14, kept 0 |
| iyzico-jobs | jobs | api (Lever) | Istanbul payments company | fetched 9, kept 0 |
| youthall-jobs | listings | scrape `.jobs` | Internships, part-time, and new-grad listings from 1,200+ companies | 27 cards, kept 7 (`require: [tech]`) |
| youthall-programs | listings | scrape `.jobs-content-header` | Talent programs (321 listed). Titles only: the card rating changes daily | 18 cards, kept 18 |
| coderspace-events | listings | scrape `div.group.shadow-elevation-200` | Hackathons, hiring challenges, talent programs | 6 cards, kept 3 |
| techcareer-events | listings | scrape `div.MuiGrid-grid-md-4 > a` | Bootcamps with "Son Başvuru" dates | 10 cards, kept 10 |
| patika-bootcamps | listings | scrape | Free bootcamps with placement at partner companies | page fetched, kept 1 baseline |
| stm-starteam | programs | scrape | StarTeaM has a Siber Güvenlik track for 3rd and 4th years | kept 1 baseline |
| turkcell-gncytnk | programs | scrape | Full-time track for final-year students; last round closed 12 March | kept 1 baseline |
| turk-telekom-start | programs | scrape | START Stajım and START Yeni İşim | kept 1 baseline |
| intertech-starttech | programs | scrape | Online part-time for 4th years; windows Jun–Jul and Nov–Dec | kept 1 baseline |
| garanti-z-talent | programs | scrape | Garanti BBVA Teknoloji, final-year students | kept 1 baseline |
| softtech-yetenek | programs | scrape | İş Bankası's software company, final year or new grad | kept 1 baseline |
| vodafone-graduate | programs | scrape | Discover Graduate; the 2026 round closed 20 July | kept 1 baseline |
| vodafone-internship | programs | scrape | Long-term internship, 4th year | kept 1 baseline |
| tei-aday-muhendis | programs | scrape | 4th years in 2026–27, Sep–Oct window, onsite in Eskişehir | kept 1 baseline |
| teb-tech-leaders | programs | scrape | TEB Geleceğin Teknoloji Liderleri | kept 1 baseline |
| turkiye-finans-we-plus-it | programs | scrape | Part-time IT hiring program | kept 1 baseline |
| siemens-working-student | programs | scrape | Part-time working student | kept 1 baseline |
| hubx-new-grad | programs | scrape | New grad program | kept 1 baseline |
| thy-careers | programs | scrape | THY open positions, including "Yeni Mezun/Öğrenci". Not on Youthall | kept 1 baseline |
| tubitak-2209-a | grants | scrape | 2209-A, open 7 Oct – 11 Nov 2026 | kept 1 baseline |
| tubitak-2209-b | grants | scrape | 2209-B (industry projects), same window | kept 1 baseline |
| sibervatan | training | scrape | Siber Vatan; last round's applications closed in November | kept 1 baseline |
| picus-academy | training | scrape | Picus Cyber Talent Academy | kept 1 baseline |
| teknofest | competitions | scrape | TEKNOFEST competitions | kept 1 baseline |

## Rejected

| candidate | reason |
| --- | --- |
| SmartRecruiters API (`api.smartrecruiters.com`) for Lostar and Brandefense | `robots.txt`: `Disallow: /` for every agent except LinkedInBot. The public career pages are used instead |
| `codeway-jobs` (Ashby) | `api.ashbyhq.com/robots.txt` answers 401, which the client treats as a refusal. The public board keeps its jobs inside a script, so a scrape gets no text |
| `btk-akademi` | JavaScript-only page: extracts no text without `js: true` |
| OKX (Greenhouse) | 349 global postings; the Istanbul ones are senior compliance roles |
| International security companies (Cloudflare, GitLab, Wiz, SentinelOne, Elastic, Datadog, HackerOne, Bugcrowd, Trail of Bits, and others) | About 3,500 postings checked on 2026-10-12; 2 were early-career security roles, both in Israel. Most roles there need relocation or visa sponsorship. Add their boards if you can work abroad |
| Barikat, BGA, PRODAFT, SOCRadar, Invicti, Logsign, Labris, Crypttech | Openings load by script or are posted only on LinkedIn, which is not fetched. Set LinkedIn's own job alerts for these |
| Hepsiburada, Getir, Papara, Turkcell, Vodafone, Garanti, Akbank, Sahibinden, BtcTurk boards | No public board API. Their programs reach Youthall, which is watched |
| Kariyer.net search | The search page returned mostly unrelated listings |
| LinkedIn, Indeed, Glassdoor | Terms of service and `robots.txt` |
| `cybertake-off.com` (THY Cyber Take-Off) | Does not respond. The last rounds found were 2022–2023. `thy-careers` watches THY instead |
| ASELSAN a Yetenek 4 | Applications open in July for students who will be 4th years the next year. Add it as a `scrape` source if that fits you |
| Kommunity, Meetup security events | No verified Istanbul security event page yet |
