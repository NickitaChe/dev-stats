from __future__ import annotations

import argparse
import json
import os
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


@dataclass(frozen=True)
class AuthorIdentity:
    name: str | None = None
    email: str | None = None


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
) -> dict[str, Any] | None:
    command = [
        "log",
        "--all",
        "--date=iso-strict",
        f"--pretty=format:{COMMIT_PREFIX}%x1f%H%x1f%an%x1f%ae%x1f%aI",
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

            _commit_sha, author_name, author_email, authored_at = parts[:4]
            current_matches = matches_author(author_name, author_email, identities)

            if not current_matches:
                current_month = None
                current_year = None
                continue

            try:
                moment = datetime.fromisoformat(authored_at)
            except ValueError:
                current_matches = False
                continue

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

    if commits == 0:
        return None

    return {
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
    }


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


def build_payload(
    results: list[dict[str, Any]],
    include_repositories: bool,
    aliases: dict[str, str],
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

    repositories: list[dict[str, Any]] = []
    if include_repositories:
        for item in sorted(results, key=lambda value: value["commits"], reverse=True):
            raw_name = item["name"]
            repositories.append(
                {
                    "name": aliases.get(raw_name, raw_name),
                    "commits": item["commits"],
                    "linesAdded": item["linesAdded"],
                    "linesDeleted": item["linesDeleted"],
                    "netLines": item["netLines"],
                    "activeDays": len(item["activeDays"]),
                    "firstCommitAt": iso_or_none(item["firstCommit"]),
                    "lastCommitAt": iso_or_none(item["lastCommit"]),
                }
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
        "repositories": repositories,
        "meta": {
            "repositoryNamesPublished": include_repositories,
        },
    }


def format_number(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def print_summary(payload: dict[str, Any], output_path: Path) -> None:
    totals = payload["totals"]

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
    print(f"{C.dim}written: {output_path}{C.reset}")


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

    aliases = {
        str(key): str(value)
        for key, value in config.get("repositoryAliases", {}).items()
    }

    output_value = args.output or config.get("output", "public/data/stats.json")
    output_path = resolve_path(str(output_value), base_dir)

    print(f"{C.green}nickitache@nickitache{C.reset}:{C.cyan}~{C.reset}$ dev-stats run")
    print(f"{C.dim}[scan]{C.reset} roots: {len(roots)}")

    repositories = discover_repositories(roots, excluded)
    print(f"{C.dim}[scan]{C.reset} git repositories found: {len(repositories)}")

    results: list[dict[str, Any]] = []
    errors = 0

    for index, repo in enumerate(repositories, start=1):
        try:
            result = scan_repository(repo, identities, include_merges)
            if result is None:
                continue

            results.append(result)
            print(
                f"{C.green}[ok]{C.reset} "
                f"{index:>3}/{len(repositories):<3} "
                f"{repo.name}: {format_number(result['commits'])} commits, "
                f"+{format_number(result['linesAdded'])}/"
                f"-{format_number(result['linesDeleted'])}"
            )
        except Exception as exc:
            errors += 1
            print(f"{C.yellow}[skip]{C.reset} {repo}: {exc}")

    payload = build_payload(results, include_repositories, aliases)
    payload["meta"]["scanErrors"] = errors

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
        help="publish repository names and per-repository totals",
    )
    run_parser.set_defaults(handler=command_run)

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
