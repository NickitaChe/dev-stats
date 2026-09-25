from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

COMMIT_PREFIX = "__DEV_STATS_COMMIT__"
DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    ".vs",
    "bin",
    "obj",
    "node_modules",
    "dist",
    "build",
    "vendor",
    ".venv",
    "venv",
}
PUBLIC_PROJECT_NAMES = (
    "Flatform",
    "Marmelad Platform",
    "LaL",
    "The Drowned Frontier",
)
PUBLIC_CATEGORY_NAMES = ("Work", "Other")
STATS_KV_KEY = "stats:current"


@dataclass(frozen=True)
class AuthorIdentity:
    name: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class RepositorySelector:
    names: frozenset[str] = frozenset()
    paths: frozenset[str] = frozenset()

    def matches(self, repo: Path) -> bool:
        return (
            repo.name.casefold() in self.names
            or str(repo.resolve()).casefold() in self.paths
        )


class Colors:
    def __init__(self) -> None:
        enabled = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
        self.reset = "\033[0m" if enabled else ""
        self.green = "\033[32m" if enabled else ""
        self.cyan = "\033[36m" if enabled else ""
        self.yellow = "\033[33m" if enabled else ""
        self.red = "\033[31m" if enabled else ""
        self.dim = "\033[2m" if enabled else ""


C = Colors()


def run_git(repo: Path, *args: str, check: bool = True) -> str:
    process = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and process.returncode != 0:
        message = process.stderr.strip() or process.stdout.strip() or "git command failed"
        raise RuntimeError(message)
    return process.stdout


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("Configuration root must be a JSON object.")
    return data


def resolve_path(value: str, base: Path) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def discover_repositories(roots: Iterable[Path], excluded: set[str]) -> list[Path]:
    found: set[Path] = set()

    for root in roots:
        if not root.exists():
            print(f"{C.yellow}[warn]{C.reset} root does not exist: {root}")
            continue

        if (root / ".git").exists():
            found.add(root.resolve())
            continue

        for current, dirs, _files in os.walk(root):
            current_path = Path(current)

            dirs[:] = [
                directory
                for directory in dirs
                if directory not in excluded
            ]

            git_marker = current_path / ".git"
            if git_marker.exists():
                found.add(current_path.resolve())
                dirs.clear()

    return sorted(found, key=lambda value: str(value).lower())


def read_git_identity() -> list[AuthorIdentity]:
    identities: list[AuthorIdentity] = []
    name = subprocess.run(
        ["git", "config", "--global", "user.name"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    ).stdout.strip()
    email = subprocess.run(
        ["git", "config", "--global", "user.email"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    ).stdout.strip()

    if name or email:
        identities.append(AuthorIdentity(name=name or None, email=email or None))

    return identities


def parse_identities(config: dict[str, Any], args: argparse.Namespace) -> list[AuthorIdentity]:
    identities: list[AuthorIdentity] = []

    for item in config.get("authors", []):
        if isinstance(item, str):
            identities.append(AuthorIdentity(name=item))
        elif isinstance(item, dict):
            identities.append(
                AuthorIdentity(
                    name=str(item["name"]).strip() if item.get("name") else None,
                    email=str(item["email"]).strip() if item.get("email") else None,
                )
            )

    for name in args.author_name or []:
        identities.append(AuthorIdentity(name=name.strip()))

    for email in args.author_email or []:
        identities.append(AuthorIdentity(email=email.strip()))

    identities = [
        identity
        for identity in identities
        if identity.name or identity.email
    ]

    if not identities:
        identities = read_git_identity()

    if not identities:
        raise RuntimeError(
            "No author identities configured. Add authors to dev-stats.config.json "
            "or use --author-name / --author-email."
        )

    return identities


def parse_repository_selector(
    values: Any,
    base_dir: Path,
    field_name: str,
) -> RepositorySelector:
    if values is None:
        return RepositorySelector()
    if not isinstance(values, list):
        raise ValueError(f"{field_name} must be an array of repository names or paths.")

    names: set[str] = set()
    paths: set[str] = set()

    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name}[{index}] must be a non-empty string.")

        selector = value.strip()
        if Path(selector).is_absolute() or any(mark in selector for mark in ("/", "\\")):
            paths.add(str(resolve_path(selector, base_dir)).casefold())
        else:
            names.add(selector.casefold())

    return RepositorySelector(frozenset(names), frozenset(paths))


def merge_selectors(
    left: RepositorySelector,
    right: RepositorySelector,
) -> RepositorySelector:
    return RepositorySelector(left.names | right.names, left.paths | right.paths)


def parse_grouping(
    config: dict[str, Any],
    base_dir: Path,
) -> tuple[dict[str, RepositorySelector], RepositorySelector]:
    mappings = config.get("projectMappings", [])
    if not isinstance(mappings, list):
        raise ValueError("projectMappings must be an array.")

    projects = {name: RepositorySelector() for name in PUBLIC_PROJECT_NAMES}
    work = parse_repository_selector(
        config.get("workRepositories", []),
        base_dir,
        "workRepositories",
    )

    for index, item in enumerate(mappings):
        if not isinstance(item, dict):
            raise ValueError(f"projectMappings[{index}] must be an object.")

        project = item.get("project")
        if project not in PUBLIC_PROJECT_NAMES:
            allowed = ", ".join(PUBLIC_PROJECT_NAMES)
            raise ValueError(
                f"projectMappings[{index}].project must be one of: {allowed}."
            )

        selector = parse_repository_selector(
            item.get("repositories", []),
            base_dir,
            f"projectMappings[{index}].repositories",
        )
        projects[project] = merge_selectors(projects[project], selector)

    # Keep old configurations useful, but only allow aliases to public-safe groups.
    aliases = config.get("repositoryAliases", {})
    if not isinstance(aliases, dict):
        raise ValueError("repositoryAliases must be an object.")

    for repository, group in aliases.items():
        if not isinstance(repository, str) or not isinstance(group, str):
            raise ValueError("repositoryAliases keys and values must be strings.")

        selector = parse_repository_selector(
            [repository],
            base_dir,
            f"repositoryAliases[{repository!r}]",
        )
        if group in PUBLIC_PROJECT_NAMES:
            projects[group] = merge_selectors(projects[group], selector)
        elif group == "Work":
            work = merge_selectors(work, selector)
        elif group != "Other":
            allowed = ", ".join((*PUBLIC_PROJECT_NAMES, *PUBLIC_CATEGORY_NAMES))
            raise ValueError(
                f"repositoryAliases[{repository!r}] must map to one of: {allowed}."
            )

    return projects, work


def classify_repository(
    repo: Path,
    project_selectors: dict[str, RepositorySelector],
    work_selector: RepositorySelector,
) -> str:
    project_matches = [
        project
        for project, selector in project_selectors.items()
        if selector.matches(repo)
    ]
    is_work = work_selector.matches(repo)

    if len(project_matches) > 1 or (project_matches and is_work):
        matches = [*project_matches, *(["Work"] if is_work else [])]
        raise ValueError(
            f"Repository {repo} matches multiple public groups: {', '.join(matches)}."
        )

    if project_matches:
        return project_matches[0]
    if is_work:
        return "Work"
    return "Other"


def matches_author(name: str, email: str, identities: list[AuthorIdentity]) -> bool:
    normalized_name = name.strip().casefold()
    normalized_email = email.strip().casefold()

    for identity in identities:
        name_matches = (
            identity.name is not None
            and normalized_name == identity.name.strip().casefold()
        )
        email_matches = (
            identity.email is not None
            and normalized_email == identity.email.strip().casefold()
        )

        # Each configured item is an alternative identity.
        if name_matches or email_matches:
            return True

    return False


def empty_period() -> dict[str, int]:
    return {"commits": 0, "linesAdded": 0, "linesDeleted": 0}


def scan_repository(
    repo: Path,
    identities: list[AuthorIdentity],
    include_merges: bool,
    seen_commit_groups: dict[str, str],
    group_name: str,
) -> tuple[dict[str, Any] | None, int]:
    author_patterns = {
        pattern
        for identity in identities
        for pattern in (
            f"^{re.escape(identity.name)} <" if identity.name else None,
            f"<{re.escape(identity.email)}>$" if identity.email else None,
        )
        if pattern is not None
    }
    command = [
        "log",
        "--all",
        "--extended-regexp",
        *(f"--author={pattern}" for pattern in sorted(author_patterns)),
        "--date=iso-strict",
        f"--pretty=format:{COMMIT_PREFIX}%H%x1f%an%x1f%ae%x1f%aI",
        "--numstat",
    ]
    if not include_merges:
        command.insert(2, "--no-merges")

    output = run_git(repo, *command)

    commits = 0
    additions = 0
    deletions = 0
    active_days: set[str] = set()
    first_commit: datetime | None = None
    last_commit: datetime | None = None
    by_month: dict[str, dict[str, int]] = defaultdict(empty_period)
    by_year: dict[str, dict[str, int]] = defaultdict(empty_period)
    repository_hashes: set[str] = set()
    duplicate_commits = 0

    current_matches = False
    current_month: str | None = None
    current_year: str | None = None

    for raw_line in output.splitlines():
        line = raw_line.rstrip("\r")

        if line.startswith(COMMIT_PREFIX):
            payload = line[len(COMMIT_PREFIX):]
            parts = payload.split("\x1f")
            if len(parts) < 4:
                current_matches = False
                continue

            commit_sha, author_name, author_email, authored_at = parts[:4]
            current_matches = matches_author(author_name, author_email, identities)

            if not current_matches:
                current_month = None
                current_year = None
                continue

            existing_group = seen_commit_groups.get(commit_sha)
            if existing_group is None and commit_sha in repository_hashes:
                existing_group = group_name
            if existing_group is not None:
                if existing_group != group_name:
                    raise ValueError(
                        f"Commit {commit_sha} occurs in both {existing_group} "
                        f"and {group_name}; adjust repository grouping."
                    )
                duplicate_commits += 1
                current_matches = False
                current_month = None
                current_year = None
                continue

            try:
                moment = datetime.fromisoformat(authored_at)
            except ValueError:
                current_matches = False
                continue

            repository_hashes.add(commit_sha)
            commits += 1
            day = moment.date().isoformat()
            current_month = moment.strftime("%Y-%m")
            current_year = moment.strftime("%Y")
            active_days.add(day)

            by_month[current_month]["commits"] += 1
            by_year[current_year]["commits"] += 1

            if first_commit is None or moment < first_commit:
                first_commit = moment
            if last_commit is None or moment > last_commit:
                last_commit = moment
            continue

        if not current_matches or not line.strip():
            continue

        parts = line.split("\t", 2)
        if len(parts) < 2:
            continue

        added_raw, deleted_raw = parts[:2]
        if not added_raw.isdigit() or not deleted_raw.isdigit():
            # Binary diff entries are represented as "-".
            continue

        added = int(added_raw)
        deleted = int(deleted_raw)
        additions += added
        deletions += deleted

        if current_month is not None:
            by_month[current_month]["linesAdded"] += added
            by_month[current_month]["linesDeleted"] += deleted
        if current_year is not None:
            by_year[current_year]["linesAdded"] += added
            by_year[current_year]["linesDeleted"] += deleted

    if commits == 0 and duplicate_commits == 0:
        return None, 0

    seen_commit_groups.update(
        (commit_sha, group_name)
        for commit_sha in repository_hashes
    )

    return (
        {
            "path": repo,
            "name": repo.name,
            "commits": commits,
            "linesAdded": additions,
            "linesDeleted": deletions,
            "netLines": additions - deletions,
            "activeDays": active_days,
            "firstCommit": first_commit,
            "lastCommit": last_commit,
            "byMonth": by_month,
            "byYear": by_year,
        },
        duplicate_commits,
    )


def merge_periods(
    target: dict[str, dict[str, int]],
    source: dict[str, dict[str, int]],
) -> None:
    for period, values in source.items():
        bucket = target.setdefault(period, empty_period())
        bucket["commits"] += values["commits"]
        bucket["linesAdded"] += values["linesAdded"]
        bucket["linesDeleted"] += values["linesDeleted"]


def iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def summarize_results(name: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    active_days: set[str] = set()
    first_commit: datetime | None = None
    last_commit: datetime | None = None

    for item in results:
        active_days.update(item["activeDays"])
        first = item["firstCommit"]
        last = item["lastCommit"]
        if first is not None and (first_commit is None or first < first_commit):
            first_commit = first
        if last is not None and (last_commit is None or last > last_commit):
            last_commit = last

    additions = sum(item["linesAdded"] for item in results)
    deletions = sum(item["linesDeleted"] for item in results)

    return {
        "name": name,
        "repositories": len(results),
        "commits": sum(item["commits"] for item in results),
        "linesAdded": additions,
        "linesDeleted": deletions,
        "netLines": additions - deletions,
        "activeDays": len(active_days),
        "firstCommitAt": iso_or_none(first_commit),
        "lastCommitAt": iso_or_none(last_commit),
    }


def group_results(
    results: list[dict[str, Any]],
    project_selectors: dict[str, RepositorySelector],
    work_selector: RepositorySelector,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    projects: dict[str, list[dict[str, Any]]] = defaultdict(list)
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for item in results:
        group_name = classify_repository(
            item["path"],
            project_selectors,
            work_selector,
        )
        if group_name in PUBLIC_PROJECT_NAMES:
            projects[group_name].append(item)
        else:
            categories[group_name].append(item)

    project_stats = [
        summarize_results(name, projects[name])
        for name in PUBLIC_PROJECT_NAMES
        if projects[name]
    ]
    category_stats = [
        summarize_results(name, categories[name])
        for name in PUBLIC_CATEGORY_NAMES
        if categories[name]
    ]
    return project_stats, category_stats


def build_payload(
    results: list[dict[str, Any]],
    include_repositories: bool,
    project_selectors: dict[str, RepositorySelector],
    work_selector: RepositorySelector,
) -> dict[str, Any]:
    commits = sum(item["commits"] for item in results)
    additions = sum(item["linesAdded"] for item in results)
    deletions = sum(item["linesDeleted"] for item in results)

    active_days: set[str] = set()
    first_commit: datetime | None = None
    last_commit: datetime | None = None
    by_month: dict[str, dict[str, int]] = {}
    by_year: dict[str, dict[str, int]] = {}

    for item in results:
        active_days.update(item["activeDays"])
        merge_periods(by_month, item["byMonth"])
        merge_periods(by_year, item["byYear"])

        first = item["firstCommit"]
        last = item["lastCommit"]
        if first is not None and (first_commit is None or first < first_commit):
            first_commit = first
        if last is not None and (last_commit is None or last > last_commit):
            last_commit = last

    projects, categories = group_results(results, project_selectors, work_selector)
    public_groups = sorted(
        [*projects, *categories],
        key=lambda value: value["commits"],
        reverse=True,
    )

    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": "local-git",
        "totals": {
            "repositories": len(results),
            "commits": commits,
            "linesAdded": additions,
            "linesDeleted": deletions,
            "netLines": additions - deletions,
            "activeDays": len(active_days),
            "firstCommitAt": iso_or_none(first_commit),
            "lastCommitAt": iso_or_none(last_commit),
        },
        "byYear": [
            {
                "year": period,
                **values,
                "netLines": values["linesAdded"] - values["linesDeleted"],
            }
            for period, values in sorted(by_year.items())
        ],
        "byMonth": [
            {
                "month": period,
                **values,
                "netLines": values["linesAdded"] - values["linesDeleted"],
            }
            for period, values in sorted(by_month.items())
        ],
        "projects": projects,
        "categories": categories,
        # Compatibility field: it can be opted into, but now contains only
        # public-safe aggregated groups and never raw repository names.
        "repositories": public_groups if include_repositories else [],
        "meta": {
            "repositoryNamesPublished": False,
            "publicGroupingPublished": True,
            "unclassifiedRepositories": next(
                (
                    item["repositories"]
                    for item in categories
                    if item["name"] == "Other"
                ),
                0,
            ),
        },
    }


def format_number(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def print_summary(payload: dict[str, Any], output_path: Path) -> None:
    totals = payload["totals"]
    duplicate_commits = payload["meta"].get("duplicateCommitsExcluded", 0)

    print()
    print(f"{C.green}[done]{C.reset} statistics collected")
    print(f"  {C.cyan}repositories{C.reset}  {format_number(totals['repositories'])}")
    print(f"  {C.cyan}commits{C.reset}       {format_number(totals['commits'])}")
    print(
        f"  {C.cyan}lines{C.reset}         "
        f"{C.green}+{format_number(totals['linesAdded'])}{C.reset} "
        f"{C.red}-{format_number(totals['linesDeleted'])}{C.reset}"
    )
    print(f"  {C.cyan}net lines{C.reset}      {format_number(totals['netLines'])}")
    print(f"  {C.cyan}active days{C.reset}   {format_number(totals['activeDays'])}")
    if duplicate_commits:
        print(
            f"  {C.cyan}deduplicated{C.reset}  "
            f"{format_number(duplicate_commits)} commits"
        )
    print(f"{C.dim}written: {output_path}{C.reset}")


def validate_public_payload(payload: dict[str, Any]) -> None:
    expected_keys = {
        "schemaVersion",
        "generatedAt",
        "source",
        "totals",
        "byYear",
        "byMonth",
        "projects",
        "categories",
        "repositories",
        "meta",
    }
    unexpected_keys = set(payload) - expected_keys
    if unexpected_keys:
        raise ValueError(
            "Statistics payload contains unexpected top-level fields: "
            + ", ".join(sorted(unexpected_keys))
        )

    if payload.get("schemaVersion") != 1 or payload.get("source") != "local-git":
        raise ValueError("Statistics payload has an unsupported schema or source.")
    generated_at = payload.get("generatedAt")
    if not isinstance(generated_at, str):
        raise ValueError("Statistics payload has no generatedAt value.")
    try:
        datetime.fromisoformat(generated_at)
    except ValueError as exc:
        raise ValueError("Statistics payload has an invalid generatedAt value.") from exc

    totals = payload.get("totals")
    total_fields = {
        "repositories",
        "commits",
        "linesAdded",
        "linesDeleted",
        "netLines",
        "activeDays",
        "firstCommitAt",
        "lastCommitAt",
    }
    if not isinstance(totals, dict) or set(totals) != total_fields:
        raise ValueError("Statistics payload has invalid totals.")
    for field in total_fields - {"firstCommitAt", "lastCommitAt"}:
        if not isinstance(totals[field], int):
            raise ValueError(f"Statistics total {field} must be an integer.")

    by_year = payload.get("byYear")
    by_month = payload.get("byMonth")
    if not isinstance(by_year, list) or not isinstance(by_month, list):
        raise ValueError("Statistics payload has invalid period arrays.")

    metric_fields = {"commits", "linesAdded", "linesDeleted", "netLines"}
    periods = [
        *((item, "year") for item in by_year),
        *((item, "month") for item in by_month),
    ]
    for period, label in periods:
        if not isinstance(period, dict) or set(period) != metric_fields | {label}:
            raise ValueError(f"Statistics payload contains invalid {label} fields.")
        if not isinstance(period[label], str) or any(
            not isinstance(period[field], int) for field in metric_fields
        ):
            raise ValueError(f"Statistics payload contains invalid {label} values.")

    repositories = payload.get("repositories")
    if repositories != []:
        raise ValueError(
            "The public repositories field must be empty before publication."
        )

    meta = payload.get("meta")
    if not isinstance(meta, dict) or meta.get("repositoryNamesPublished") is not False:
        raise ValueError("Repository names must not be published.")
    meta_fields = {
        "repositoryNamesPublished",
        "publicGroupingPublished",
        "unclassifiedRepositories",
        "scanErrors",
        "duplicateCommitsExcluded",
    }
    if set(meta) != meta_fields:
        raise ValueError("Statistics payload contains invalid metadata fields.")
    if meta.get("scanErrors") != 0:
        raise ValueError("Statistics with scan errors cannot be published.")

    projects = payload.get("projects")
    categories = payload.get("categories")
    if not isinstance(projects, list) or not isinstance(categories, list):
        raise ValueError("Statistics payload has invalid public grouping arrays.")

    project_names = {item.get("name") for item in projects if isinstance(item, dict)}
    category_names = {item.get("name") for item in categories if isinstance(item, dict)}
    if len(project_names) != len(projects) or not project_names.issubset(
        PUBLIC_PROJECT_NAMES
    ):
        raise ValueError("Statistics payload contains a non-public project name.")
    if len(category_names) != len(categories) or not category_names.issubset(
        PUBLIC_CATEGORY_NAMES
    ):
        raise ValueError("Statistics payload contains an unsupported category name.")

    group_fields = {
        "name",
        "repositories",
        "commits",
        "linesAdded",
        "linesDeleted",
        "netLines",
        "activeDays",
        "firstCommitAt",
        "lastCommitAt",
    }
    for group in [*projects, *categories]:
        if set(group) != group_fields:
            raise ValueError("Statistics payload contains invalid public group fields.")


def command_publish(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    base_dir = config_path.parent if config_path.exists() else Path.cwd()

    input_value = args.input or config.get("output", ".codex/stats.json")
    input_path = resolve_path(str(input_value), base_dir)
    if not input_path.is_file():
        raise RuntimeError(f"Statistics file does not exist: {input_path}")

    payload = load_config(input_path)
    validate_public_payload(payload)

    wrangler_config = resolve_path(str(args.wrangler_config), Path.cwd())
    if not wrangler_config.is_file():
        raise RuntimeError(f"Wrangler configuration does not exist: {wrangler_config}")

    npx = shutil.which("npx.cmd" if os.name == "nt" else "npx")
    if npx is None:
        raise RuntimeError("npx was not found in PATH.")

    command = [
        npx,
        "wrangler",
        "kv",
        "key",
        "put",
        str(args.key),
        "--binding",
        str(args.binding),
        "--path",
        str(input_path),
        "--local" if args.local else "--remote",
        "--config",
        str(wrangler_config),
    ]

    print(
        f"{C.green}nickitache@nickitache{C.reset}:"
        f"{C.cyan}~{C.reset}$ dev-stats publish",
        flush=True,
    )
    print(f"{C.dim}[validate]{C.reset} public payload is safe", flush=True)
    destination = "local KV" if args.local else "Cloudflare KV"
    print(
        f"{C.dim}[upload]{C.reset} {input_path} -> "
        f"{destination} {args.binding}/{args.key}",
        flush=True,
    )

    if args.dry_run:
        print(f"{C.yellow}[dry-run]{C.reset} upload skipped")
        return 0

    process = subprocess.run(command, check=False)
    if process.returncode != 0:
        raise RuntimeError(f"Wrangler upload failed with exit code {process.returncode}.")

    print(f"{C.green}[done]{C.reset} statistics published")
    return 0


def command_run(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    base_dir = config_path.parent if config_path.exists() else Path.cwd()

    configured_roots = [str(value) for value in config.get("roots", [])]
    root_values = list(args.root or configured_roots)
    if not root_values:
        root_values = [str(Path.cwd())]

    roots = [resolve_path(value, base_dir) for value in root_values]
    identities = parse_identities(config, args)

    excluded = set(DEFAULT_EXCLUDED_DIRS)
    excluded.update(str(value) for value in config.get("excludeDirectories", []))

    include_merges = bool(config.get("includeMerges", False))
    include_repositories = bool(config.get("includeRepositories", False))
    if args.include_repositories:
        include_repositories = True

    project_selectors, work_selector = parse_grouping(config, base_dir)

    output_value = args.output or config.get("output", ".codex/stats.json")
    output_path = resolve_path(str(output_value), base_dir)

    print(f"{C.green}nickitache@nickitache{C.reset}:{C.cyan}~{C.reset}$ dev-stats run")
    print(f"{C.dim}[scan]{C.reset} roots: {len(roots)}")

    repositories = discover_repositories(roots, excluded)
    print(f"{C.dim}[scan]{C.reset} git repositories found: {len(repositories)}")

    results: list[dict[str, Any]] = []
    seen_commit_groups: dict[str, str] = {}
    duplicate_commits = 0
    errors = 0

    for index, repo in enumerate(repositories, start=1):
        group_name = classify_repository(repo, project_selectors, work_selector)
        try:
            result, repository_duplicates = scan_repository(
                repo,
                identities,
                include_merges,
                seen_commit_groups,
                group_name,
            )
            duplicate_commits += repository_duplicates
            if result is None:
                continue

            results.append(result)
            duplicate_suffix = (
                f", {format_number(repository_duplicates)} duplicate hashes excluded"
                if repository_duplicates
                else ""
            )
            print(
                f"{C.green}[ok]{C.reset} "
                f"{index:>3}/{len(repositories):<3} "
                f"{repo.name}: {format_number(result['commits'])} commits, "
                f"+{format_number(result['linesAdded'])}/"
                f"-{format_number(result['linesDeleted'])}"
                f"{duplicate_suffix}"
            )
        except ValueError:
            raise
        except Exception as exc:
            errors += 1
            print(f"{C.yellow}[skip]{C.reset} {repo}: {exc}")

    payload = build_payload(
        results,
        include_repositories,
        project_selectors,
        work_selector,
    )
    payload["meta"]["scanErrors"] = errors
    payload["meta"]["duplicateCommitsExcluded"] = duplicate_commits

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")

    print_summary(payload, output_path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dev-stats",
        description="Collect public-safe aggregate statistics from local Git repositories.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="scan repositories and write stats JSON")
    run_parser.add_argument(
        "--config",
        default="dev-stats.config.json",
        help="configuration file (default: dev-stats.config.json)",
    )
    run_parser.add_argument(
        "--root",
        action="append",
        help="repository search root; may be specified multiple times",
    )
    run_parser.add_argument(
        "--author-name",
        action="append",
        help="exact Git author name to include; may be repeated",
    )
    run_parser.add_argument(
        "--author-email",
        action="append",
        help="exact Git author email to include; may be repeated",
    )
    run_parser.add_argument(
        "--output",
        help="output JSON path; overrides config",
    )
    run_parser.add_argument(
        "--include-repositories",
        action="store_true",
        help="include public grouped totals in the legacy repositories field",
    )
    run_parser.set_defaults(handler=command_run)

    publish_parser = subparsers.add_parser(
        "publish",
        help="validate and upload a generated snapshot to Cloudflare KV",
    )
    publish_parser.add_argument(
        "--config",
        default="dev-stats.config.json",
        help="collector configuration file (default: dev-stats.config.json)",
    )
    publish_parser.add_argument(
        "--input",
        help="statistics JSON path; defaults to the configured output",
    )
    publish_parser.add_argument(
        "--wrangler-config",
        default="wrangler.jsonc",
        help="Wrangler configuration file (default: wrangler.jsonc)",
    )
    publish_parser.add_argument(
        "--binding",
        default="STATS",
        help="Workers KV binding name (default: STATS)",
    )
    publish_parser.add_argument(
        "--key",
        default=STATS_KV_KEY,
        help=f"Workers KV key (default: {STATS_KV_KEY})",
    )
    publish_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and show the upload target without writing to Cloudflare",
    )
    publish_parser.add_argument(
        "--local",
        action="store_true",
        help="write to Wrangler's local KV instead of Cloudflare",
    )
    publish_parser.set_defaults(handler=command_publish)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        code = args.handler(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        code = 130
    except Exception as exc:
        print(f"{C.red}error:{C.reset} {exc}", file=sys.stderr)
        code = 1

    raise SystemExit(code)


if __name__ == "__main__":
    main()
