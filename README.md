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
- public-safe totals grouped into projects and categories.

Commit hashes are deduplicated across all scanned repositories. A repeated hash
contributes its commit, dates, and numstat only once. Repeated hashes must stay
within the same public group; a hash shared by differently classified projects
is treated as a configuration error instead of being attributed arbitrarily.

Raw repository names are **never** written to the public JSON. Known repositories
can be mapped to one of the approved public projects; explicitly marked work
repositories are aggregated as `Work`; everything else is aggregated as `Other`.

The only accepted public project names are:

- `Flatform`
- `Marmelad Platform`
- `LaL`
- `The Drowned Frontier`

This allowlist is enforced by the collector, so a typo or an unapproved project
name stops collection instead of leaking a repository name.

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

Edit the roots, author identities, and repository grouping, then run:

```powershell
dev-stats run
```

The default output is:

```text
.codex/stats.json
```

The snapshot is ignored by Git. Real statistics are uploaded directly to
Cloudflare KV and are never included in the repository or static web bundle.

Useful overrides:

```powershell
dev-stats run --root G:\repos --author-name "Nickita Che"
dev-stats run --include-repositories
dev-stats run --output .codex/stats.json
```

## Public project grouping

Each repository selector is either an exact repository directory name or an
exact path. Multiple selectors can map to the same logical project:

```json
{
  "projectMappings": [
    {
      "project": "Marmelad Platform",
      "repositories": [
        "marmelad-platform-api",
        "marmelad-platform-web",
        "G:\\projects\\marmelad-platform-infrastructure"
      ]
    }
  ],
  "workRepositories": [
    "G:\\work\\client-repository",
    "internal-tools"
  ]
}
```

Matching is case-insensitive. A selector containing `/` or `\\` is resolved as
a path relative to the configuration file; any other selector matches the
repository directory name. A repository may match only one project/category.

The generated JSON contains additive `projects` and `categories` arrays. Each
entry is an aggregate and includes its physical repository count. Existing
totals, monthly/yearly data, and metric API routes retain their shape. The
legacy `repositories` array remains empty by default; `includeRepositories` or
`--include-repositories` fills it with the same public-safe groups, never raw
repository names. Legacy `repositoryAliases` config is accepted only when the
target is one of the four projects, `Work`, or `Other`.
`meta.duplicateCommitsExcluded` reports how many repeated commit hashes were
removed from the aggregate.

## Publish statistics

`publish` validates the snapshot before upload. It refuses payloads containing
repository details, unknown public groups, scan errors, or an unsupported
schema:

```powershell
dev-stats run
dev-stats publish --dry-run
dev-stats publish
```

The upload writes `.codex/stats.json` to the `stats:current` key of the `STATS`
Workers KV binding through the locally authenticated Wrangler CLI. No upload
credential or real statistics file is committed to Git.

## Web

The page intentionally looks like an Ubuntu terminal. It loads statistics from the API and reveals CLI-like output line by line.

Local development:

```powershell
npm install
dev-stats run
dev-stats publish --local
npm run worker:dev
```

`npm run dev` still starts a UI-only Vite server, but API-backed statistics
require `npm run worker:dev`.

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

This repository is prepared for **Cloudflare Workers + Static Assets + Workers
KV**. The application bundle and statistics are deployed independently.

Authenticate Wrangler once:

```powershell
npx wrangler login
```

Deploy the application:

```powershell
npm run deploy
```

The first deployment automatically provisions the `STATS` KV namespace and
writes its generated namespace ID into `wrangler.jsonc`. Commit that binding ID;
it is a public resource identifier, not a credential.

Generate and upload statistics separately whenever a new snapshot is needed:

```powershell
dev-stats run
dev-stats publish
```

The Worker serves API routes from the `stats:current` KV value and Vite's
`dist/` as static assets. A newly written KV value may take roughly a minute to
become visible at every Cloudflare location.

After creating the Worker, attach:

```text
stats.nickitache.com
```

as its Custom Domain.

## License

MIT.
