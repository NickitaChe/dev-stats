# dev-stats

Local Git statistics collector + terminal-style web page for `stats.nickitache.com`.

The collector scans Git history on your own machine and publishes **aggregated statistics only**. Source files are not uploaded.

## What it counts

- repositories with matching commits;
- commits;
- added / deleted / net lines from `git log --numstat`;
- active days;
- first / last matching commit;
- totals grouped by month and year;
- optional per-repository totals.

By default repository names are **not** written to the public JSON. This avoids leaking names of private/work repositories.

## Local collector

Requirements:

- Python 3.11+
- Git available in `PATH`

Install the CLI in editable mode:

```powershell
py -m pip install -e .
```

Create your local configuration:

```powershell
Copy-Item dev-stats.config.example.json dev-stats.config.json
```

Edit the roots and author identities, then run:

```powershell
dev-stats run
```

The default output is:

```text
public/data/stats.json
```

Useful overrides:

```powershell
dev-stats run --root G:\repos --author-name "Nickita Che"
dev-stats run --include-repositories
dev-stats run --output public/data/stats.json
```

After collection, commit only the generated aggregate:

```powershell
git add public/data/stats.json
git commit -m "data: update dev stats"
git push
```

## Web

The page intentionally looks like an Ubuntu terminal. It loads statistics from the API and reveals CLI-like output line by line.

Local development:

```powershell
npm install
npm run dev
```

Checks:

```powershell
npm run check
npm run build
```

## API

The Cloudflare Worker exposes public, CORS-enabled read-only endpoints:

```text
GET /api
GET /api/health
GET /api/stats
GET /api/stats/summary
GET /api/stats/commits
GET /api/stats/repositories
GET /api/stats/lines-added
GET /api/stats/lines-deleted
GET /api/stats/net-lines
GET /api/stats/active-days
```

Metric endpoints return JSON by default. For easy embedding into another site:

```text
GET /api/stats/commits?format=text
```

returns only the number as `text/plain`.

Example from another site:

```ts
const commits = await fetch(
  "https://stats.nickitache.com/api/stats/commits?format=text"
).then(x => x.text());
```

## Cloudflare deployment

This repository is prepared for **Cloudflare Workers + Static Assets**.

Build:

```text
npm run build
```

Deploy:

```text
npm run deploy
```

The Worker serves API routes and Vite's `dist/` as static assets.

After creating the Worker, attach:

```text
stats.nickitache.com
```

as its Custom Domain.

## License

MIT.
