# Scout

A free, static internship feed backed by a durable public-job scanner. The frontend is unchanged. This repository contains only allowlisted application code, tests, public company/job data and a scanner-only seed. It contains no personalized tracker records or credentials.

## Deployment

Use GitHub Pages with this public repository and standard GitHub-hosted runners. In Settings → Pages, choose **GitHub Actions**. Run **Actions → Refresh Scout → Run workflow**. A production verification report must distinguish manual dispatch from naturally scheduled runs.

The workflow has four UTC opportunities, at 01:17, 07:17, 13:17 and 19:17. This is a best-effort schedule; there is no same-day discovery guarantee. Do not increase frequency based on planning estimates alone. Standard public-repository runners are [free under GitHub's documented policy](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job); [Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits) and artifact/storage policies still apply.

## Durable state

The first deployment uses `deploy/scanner-seed.sqlite3.gz`. Every subsequent execution restores the `scout-state` branch and verifies checkpoint/hash/history before scanning. Network, permission, corruption and fetch errors **fail closed**; none triggers a seed fallback. Never delete the production state branch or replace its database with the seed.

Configuration upgrades are additive, use natural employer/provider/publication identities, preserve production IDs and job history, and require live complete inventories before newly configured sources can certify scope. Conflicting, unknown or retired publications remain explicit review cases.

SQLite commits individually accepted complete source inventories. Checkpointing uses SQLite's WAL-aware backup and validates integrity/foreign keys. Both public feed files are retained together if publication fails. State writes use workflow concurrency and a compare-and-swap push lease; a stale writer cannot replace newer state. The branch stores a rolling checkpoint; longitudinal job, source, queue and operational history lives inside the database. A hard runner loss before the final remote checkpoint can lose that run's not-yet-checkpointed changes; successful earlier remote checkpoints remain intact. The 45-minute workflow leaves headroom around the 25-minute scan launch budget and per-source deadline.

## Operational evidence

Each run records actual workflow ID/attempt/event, source costs, retries, queue delays, requested/effective cadence misses, source-health changes, export timing, integrity and original-discovery checks. Operational JSON, the checkpoint manifest and discovery events are persisted in `scout-state`. Per-run evidence is also uploaded as an Actions artifact with 14-day retention.

New membership after a previous successful complete census is distinct from initial onboarding, updates/reposts, reappearance and historical revalidation. Exact latency requires employer-provided timestamp precision **and** timezone evidence. Unknown or date-only publication fields never establish exact or same-day latency. Previous native discovery timestamps are never reset for a repair.

The seven-day evaluation initializes automatically. It begins at the next New York midnight after the first hosted run so partial days are not claimed as full observation days. Only elapsed calendar days are evaluated, and missing scheduled evidence is explicit. Two manual runs do not complete seven days or verify unattended cron operation. Ordinary scheduled runs accumulate the metrics without an ongoing assistant session.

## Local commands

```sh
python3 -m unittest discover -s tests -q
node --test tests/site.test.cjs
python3 -m scripts.validate_public_site
python3 -m http.server 8001 --bind 127.0.0.1 --directory site
```

To operate on a restored production state, always pass `--db shared-state/scout.sqlite3` to scanner/recovery/audit commands. Never initialize a new database over existing state. Back up the state branch before configuration migration. User saves/preferences remain browser-local and never enter scanner state.
