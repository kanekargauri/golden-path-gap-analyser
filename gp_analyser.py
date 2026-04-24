#!/usr/bin/env python3
"""
Golden Path Gap Analyser

Analyses GitHub org repositories against a defined Golden Path, combining:
  - Actual tools used (detected from dependency files)
  - Team ownership (from GitHub Teams API or a CSV)
  - Cloud provider costs (from an AWS Cost Explorer CSV export)

Usage:
  export GITHUB_TOKEN=ghp_...

  # Use bundled golden_path.yml
  python gp_analyser.py --org <org> --teams "platform-core,platform-infra"

  # Use your own Golden Path definition
  python gp_analyser.py --org <org> --teams "platform-core" --golden-path my_golden_path.yml

  # Full run with cost + ownership data
  python gp_analyser.py \\
    --org <org> \\
    --teams "platform-core,platform-infra" \\
    --golden-path golden_path.yml \\
    --cloud-costs cloud_costs.csv \\
    --ownership ownership.csv \\
    --output report.json
"""

import os
import re
import json
import base64
import argparse
from collections import defaultdict
from pathlib import Path
from typing import Optional

import yaml
from github import Github, GithubException
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

console = Console()

# Default golden path config shipped with the tool
DEFAULT_GOLDEN_PATH = Path(__file__).parent / "golden_path.yml"

# ─────────────────────────────────────────────────────────────────────────────
# Tool detection patterns — applied against raw dependency file content
# ─────────────────────────────────────────────────────────────────────────────

TOOL_DETECTORS = {
    # Cache
    "Redis":            [r'"(io)?redis"', r'go-redis', r'\bredis[=\s>"\']', r'\bjedis\b', r'redis-py'],
    "ValKey":           [r'valkey'],
    "Memcached":        [r'memcached', r'pymemcache'],

    # Relational
    "MySQL":            [r'mysql2', r'mysqlclient', r'pymysql', r'mysql-connector',
                         r'"sequelize"', r'SQLAlchemy.*mysql', r'django-mysql', r'djongo'],
    "PostgreSQL":       [r'"pg"', r'psycopg', r'asyncpg', r'pg-promise'],
    "SQLite":           [r'sqlite3', r'better-sqlite3'],

    # Document / NoSQL
    "MongoDB":          [r'"mongodb"', r'"mongoose"', r'pymongo', r'djongo',
                         r'mongodb-memory-server'],
    "Cassandra":        [r'cassandra-driver', r'"cassandra"'],
    "DynamoDB":         [r'dynamodb', r'client-dynamodb'],
    "Elasticsearch":    [r'elasticsearch[="\s]', r'opensearch-py'],
    "Neo4j":            [r'\bneo4j\b'],

    # Messaging / streaming
    "Kafka":            [r'kafkajs', r'kafka-clients', r'confluent-kafka', r'kafka-python',
                         r'kafka-avro-serializer', r'confluent\.kafka', r'librdkafka'],
    "SQS":              [r'sqs-consumer', r'client-sqs', r'aws[._]sqs'],
    "Kinesis":          [r'client-kinesis', r'amazon_kclpy', r'aws-kcl', r'aws[._]kinesis'],
    "SNS":              [r'client-sns', r'aws[._]sns'],
    "RabbitMQ":         [r'amqplib', r'\bpika\b'],
    "BullMQ":           [r'"bullmq"', r'"bull"'],

    # Observability
    "StatsD/TIG":       [r'hot-shots', r'statsd', r'statsd-telegraf', r'go-statsd', r'gostatsd'],
    "OpenTelemetry":    [r'opentelemetry', r'@opentelemetry'],
    "New Relic":        [r'newrelic', r'new-relic'],
    "Datadog":          [r'"datadog"', r'ddtrace', r'dogstatsd', r'DogStatsd'],
    "Amplitude":        [r'amplitude'],
    "Sentry":           [r'@sentry/', r'sentry-sdk'],

    # External services / platforms
    "Firebase":         [r'firebase-admin', r'"firebase"'],
    "Twilio":           [r'\btwilio\b'],
    "Stripe":           [r'\bstripe\b'],
    "AWS Glue":         [r'glue[_-]context', r'awsglue'],
    "Directus":         [r'"directus"'],
    "Google APIs":      [r'googleapis', r'google-auth'],
    "Slack API":        [r'@slack/', r'slack-sdk'],
    "PyTorch":          [r'\btorch\b', r'pytorch'],

    # Frameworks (for context)
    "Express":          [r'"express"'],
    "Fastify":          [r'"fastify"'],
    "Django":           [r'Django[>=<]', r'"django"'],
    "FastAPI":          [r'fastapi'],
    "Echo (Go)":        [r'labstack/echo'],
    "Spring":           [r'spring-boot', r'springframework'],
    "AdonisJS":         [r'"@adonisjs/'],
}

# EOL base image patterns — applied to Dockerfile content
EOL_BASE_IMAGES = [
    (r'python:2\.', "Python 2 base image (EOL Jan 2020)"),
    (r'python:3\.[0-7][\s\-]', "Python 3.0–3.7 (EOL)"),
    (r'node:1[0246][\s\-.]', "Node 10/12/14/16 (EOL)"),
    (r'openjdk:8[\s\-]', "Java 8 OpenJDK (EOL)"),
    (r'amazoncorretto:8', "Amazon Corretto 8 (EOL)"),
    (r'ubuntu:1[468]\.', "Ubuntu 14/16/18 (EOL)"),
    (r'ubuntu:20\.', "Ubuntu 20.04 (EOL Apr 2025)"),
    (r'alpine:3\.[0-9][":\s]', "Alpine 3.0–3.9 (EOL)"),
    (r'debian:(jessie|stretch|buster)', "Debian Jessie/Stretch/Buster (EOL)"),
]

DEPENDENCY_FILES = [
    "package.json", "requirements.txt", "go.mod", "Pipfile",
    "pom.xml", "build.gradle", "Dockerfile", "docker-compose.yml",
]


# ─────────────────────────────────────────────────────────────────────────────
# Loaders
# ─────────────────────────────────────────────────────────────────────────────

def load_golden_path(path: Optional[str]) -> dict:
    resolved = Path(path) if path else DEFAULT_GOLDEN_PATH
    if not resolved.exists():
        console.print(f"[red]Golden Path file not found: {resolved}[/]")
        raise SystemExit(1)
    with open(resolved) as f:
        gp = yaml.safe_load(f)
    source = "provided" if path else "default (bundled golden_path.yml)"
    console.print(f"[dim]Golden Path loaded from {source}: {resolved}[/]")
    return gp


def load_ownership(path: Optional[str]) -> dict:
    """Returns {repo_name: {system, team, status, ...}}"""
    if not path:
        return {}
    import csv
    mapping = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            repo = row.get("repo", "").strip()
            if repo:
                mapping[repo] = {k: v.strip() for k, v in row.items()}
    return mapping


def load_cloud_costs(path: Optional[str]) -> dict:
    """Returns {service: {month: cost, 'latest': cost, 'total': cost}}"""
    if not path:
        return {}
    import csv
    costs = {}
    with open(path) as f:
        reader = csv.DictReader(f)
        months = [h for h in reader.fieldnames if h != "Service"]
        for row in reader:
            svc = row["Service"].strip()
            vals = {}
            for m in months:
                try:
                    vals[m] = float(row[m].replace(",", "").replace('"', "").strip())
                except (ValueError, KeyError):
                    vals[m] = 0.0
            vals["total"]  = sum(vals.values())
            vals["latest"] = vals.get(months[-1], 0.0)
            costs[svc] = vals
    return costs


# ─────────────────────────────────────────────────────────────────────────────
# GitHub helpers
# ─────────────────────────────────────────────────────────────────────────────

def fetch_team_repos(org, team_slugs: list) -> dict:
    """Returns {repo_name: [team_slug, ...]}"""
    repo_teams = defaultdict(list)
    for slug in team_slugs:
        try:
            team = org.get_team_by_slug(slug)
            for repo in team.get_repos():
                repo_teams[repo.name].append(slug)
        except GithubException as e:
            console.print(f"[yellow]Warning: team '{slug}' not found — {e.data.get('message','')}")
    return dict(repo_teams)


def fetch_dep_files(repo) -> dict:
    """Fetch dependency files from repo root. Returns {filename: content}."""
    found = {}
    for fname in DEPENDENCY_FILES:
        try:
            f = repo.get_contents(fname)
            if isinstance(f, list):
                continue  # path resolved to a directory, skip
            content = base64.b64decode(f.content).decode("utf-8", errors="replace")
            found[fname] = content
        except GithubException:
            pass
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_tools(dep_files: dict) -> dict:
    """Returns {tool_name: [filenames it was detected in]}"""
    detected = {}
    for tool, patterns in TOOL_DETECTORS.items():
        sources = [
            fname for fname, content in dep_files.items()
            if any(re.search(p, content, re.IGNORECASE) for p in patterns)
        ]
        if sources:
            detected[tool] = sources
    return detected


def detect_eol_issues(dep_files: dict) -> list:
    issues = []
    dockerfile = dep_files.get("Dockerfile", "")
    for pattern, label in EOL_BASE_IMAGES:
        if re.search(pattern, dockerfile, re.IGNORECASE):
            issues.append(label)
    return issues


# ─────────────────────────────────────────────────────────────────────────────
# GP scoring
# ─────────────────────────────────────────────────────────────────────────────

def score_repo(lang: str, tools: dict, eol_issues: list, gp: dict) -> tuple:
    """Returns (passes: list[str], failures: list[str])"""
    passes, failures = [], []

    # Language
    preferred = gp.get("languages", {}).get("backend", {}).get("preferred", [])
    allowed   = gp.get("languages", {}).get("backend", {}).get("allowed", preferred)
    avoid_langs = gp.get("languages", {}).get("avoid", [])
    if preferred:
        if lang in preferred:
            passes.append(f"Language: {lang} ✓ GP preferred")
        elif lang in avoid_langs:
            failures.append(f"Language: {lang} is explicitly avoided in GP")
        elif lang in allowed:
            failures.append(f"Language: {lang} allowed but not preferred — GP prefers {', '.join(preferred)}")
        else:
            failures.append(f"Language: {lang} not in GP — GP prefers {', '.join(preferred)}")

    # Cache
    cache_preferred = gp.get("databases", {}).get("cache", {}).get("preferred", [])
    cache_avoid     = gp.get("databases", {}).get("cache", {}).get("avoid", [])
    for avoid in cache_avoid:
        if avoid in tools:
            failures.append(f"Cache: {avoid} in use — GP prefers {', '.join(cache_preferred)}")
    if any(p in tools for p in cache_preferred):
        passes.append(f"Cache: uses GP-preferred {[p for p in cache_preferred if p in tools]}")

    # DB — avoid list
    for avoid in gp.get("databases", {}).get("avoid", []):
        if avoid in tools:
            failures.append(f"Database: {avoid} not in GP")

    # Messaging
    msg_standard = gp.get("messaging", {}).get("standard")
    msg_avoid    = gp.get("messaging", {}).get("avoid", [])
    for avoid in msg_avoid:
        if avoid in tools:
            failures.append(f"Messaging: {avoid} not in GP")
    if msg_standard:
        detected_msg = [t for t in ["Kafka","SQS","Kinesis","SNS","RabbitMQ","BullMQ"] if t in tools]
        if detected_msg and msg_standard not in tools:
            failures.append(f"Messaging: uses {', '.join(detected_msg)} — GP standard is {msg_standard}")
        elif msg_standard in tools:
            passes.append(f"Messaging: uses GP standard {msg_standard}")

    # Observability
    obs_preferred = gp.get("observability", {}).get("preferred", [])
    obs_avoid     = gp.get("observability", {}).get("avoid", [])
    for avoid in obs_avoid:
        if avoid in tools:
            failures.append(f"Observability: {avoid} not in GP — use {', '.join(obs_preferred)}")
    if any(p in tools for p in obs_preferred):
        passes.append(f"Observability: uses {[p for p in obs_preferred if p in tools]}")
    elif obs_preferred:
        failures.append(f"Observability: no GP tool detected — expected {', '.join(obs_preferred)}")

    # EOL
    if eol_issues:
        for issue in eol_issues:
            failures.append(f"EOL runtime: {issue}")
    else:
        passes.append("Runtime: no EOL base images detected")

    return passes, failures


# ─────────────────────────────────────────────────────────────────────────────
# Cloud cost classification
# ─────────────────────────────────────────────────────────────────────────────

def classify_cloud_costs(cloud_costs: dict, gp: dict) -> list:
    cloud_gp = gp.get("cloud_services", {})
    rows = []
    for svc, vals in cloud_costs.items():
        cost = vals.get("latest", 0)
        if cost == 0:
            continue
        entry  = cloud_gp.get(svc, {})
        status = entry.get("status", "gap")
        note   = entry.get("note", "Not mapped in golden_path.yml — classify manually")
        rows.append({"service": svc, "cost": cost, "status": status, "note": note})
    return sorted(rows, key=lambda r: -r["cost"])


# ─────────────────────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────────────────────

STATUS_COLOR = {
    "compliant":   "green",
    "violation":   "red",
    "gap":         "yellow",
    "operational": "cyan",
    "emerging":    "magenta",
}
STATUS_ICON = {
    "compliant":   "✅",
    "violation":   "❌",
    "gap":         "⚠️ ",
    "operational": "🔧",
    "emerging":    "🆕",
}

DB_TOOLS  = {"MySQL","PostgreSQL","MongoDB","Cassandra","DynamoDB","Elasticsearch",
             "Neo4j","SQLite","Redis","ValKey","Memcached"}
MSG_TOOLS = {"Kafka","SQS","Kinesis","SNS","RabbitMQ","BullMQ"}
OBS_TOOLS = {"StatsD/TIG","OpenTelemetry","New Relic","Datadog","Amplitude","Sentry"}


def render_repo_matrix(results: list, ownership: dict):
    t = Table(title="Repository Tech Matrix", box=box.SIMPLE_HEAVY, show_lines=True)
    t.add_column("Repo",          style="bold", min_width=24)
    t.add_column("Teams",         min_width=18)
    t.add_column("Lang",          min_width=10)
    t.add_column("DB / Cache",    min_width=28)
    t.add_column("Messaging",     min_width=22)
    t.add_column("Observability", min_width=20)
    t.add_column("GP Score",      justify="center", min_width=9)

    for r in results:
        repo   = r["repo"]
        passes = r["passes"]
        fails  = r["failures"]
        total  = len(passes) + len(fails)
        score  = len(passes)
        color  = "green" if score == total else ("yellow" if score >= total * 0.6 else "red")

        teams_str = ", ".join(r.get("teams", [])) or ownership.get(repo, {}).get("team", "—")
        db_str    = ", ".join(k for k in r["tools"] if k in DB_TOOLS)  or "—"
        msg_str   = ", ".join(k for k in r["tools"] if k in MSG_TOOLS) or "—"
        obs_str   = ", ".join(k for k in r["tools"] if k in OBS_TOOLS) or "—"

        t.add_row(repo, teams_str[:28], r["lang"],
                  db_str[:28], msg_str[:22], obs_str[:22],
                  Text(f"{score}/{total}", style=color))
    console.print(t)


def render_issues(results: list):
    critical, violations, gaps = [], [], []
    for r in results:
        for f in r["failures"]:
            entry = (r["repo"], r["lang"], f)
            if "EOL" in f:
                critical.append(entry)
            elif "not in GP" in f or "violation" in f.lower() or "avoided" in f:
                violations.append(entry)
            else:
                gaps.append(entry)

    if critical:
        t = Table(title="🔴 Critical — EOL / Security Risks", box=box.SIMPLE_HEAVY, show_lines=True)
        t.add_column("Repo", style="bold red", min_width=24)
        t.add_column("Lang", min_width=10)
        t.add_column("Issue")
        for repo, lang, issue in critical:
            t.add_row(repo, lang, issue)
        console.print(t)

    if violations:
        t = Table(title="❌ GP Violations", box=box.SIMPLE_HEAVY, show_lines=True)
        t.add_column("Repo", style="bold", min_width=24)
        t.add_column("Issue", style="red")
        for repo, _, issue in violations:
            t.add_row(repo, issue)
        console.print(t)

    if gaps:
        t = Table(title="⚠️  GP Gaps (not defined / deviates from preferred)", box=box.SIMPLE_HEAVY, show_lines=True)
        t.add_column("Repo", style="bold", min_width=24)
        t.add_column("Issue", style="yellow")
        for repo, _, issue in gaps:
            t.add_row(repo, issue)
        console.print(t)


def render_tool_proliferation(results: list, gp: dict):
    tool_repos = defaultdict(list)
    for r in results:
        for tool in r["tools"]:
            tool_repos[tool].append(r["repo"])

    obs_avoid   = set(gp.get("observability", {}).get("avoid", []))
    cache_avoid = set(gp.get("databases", {}).get("cache", {}).get("avoid", []))
    cache_pref  = set(gp.get("databases", {}).get("cache", {}).get("preferred", []))
    obs_pref    = set(gp.get("observability", {}).get("preferred", []))
    db_avoid    = set(gp.get("databases", {}).get("avoid", []))
    msg_avoid   = set(gp.get("messaging", {}).get("avoid", []))

    def tool_status(tool):
        if tool in obs_avoid or tool in cache_avoid or tool in db_avoid or tool in msg_avoid:
            return "violation", "red"
        if tool in cache_pref or tool in obs_pref:
            return "compliant", "green"
        return "gap", "yellow"

    t = Table(title="Tool Proliferation Across Analysed Repos", box=box.SIMPLE_HEAVY, show_lines=True)
    t.add_column("Tool",       min_width=22)
    t.add_column("# Repos",    justify="right", min_width=8)
    t.add_column("GP Status",  min_width=16)
    t.add_column("Repos")
    for tool, repos in sorted(tool_repos.items(), key=lambda x: -len(x[1])):
        status, color = tool_status(tool)
        t.add_row(
            tool,
            str(len(repos)),
            Text(f"{STATUS_ICON[status]} {status}", style=color),
            ", ".join(repos[:6]) + ("…" if len(repos) > 6 else ""),
        )
    console.print(t)


def render_cloud_costs(classified: list):
    if not classified:
        return
    t = Table(title="Cloud Cost × Golden Path Status (latest month)", box=box.SIMPLE_HEAVY, show_lines=True)
    t.add_column("Service",  min_width=32)
    t.add_column("Cost/mo",  justify="right", min_width=10)
    t.add_column("Status",   min_width=16)
    t.add_column("Note")

    totals = defaultdict(float)
    for row in classified:
        status = row["status"]
        color  = STATUS_COLOR.get(status, "white")
        totals[status] += row["cost"]
        t.add_row(
            row["service"],
            f"${row['cost']:,.0f}",
            Text(f"{STATUS_ICON.get(status,'⚠️ ')} {status}", style=color),
            row["note"],
        )
    console.print(t)

    grand = sum(totals.values())
    summary = Table(title="Cost by GP Status", box=box.MINIMAL, show_header=False)
    summary.add_column("Status", min_width=22)
    summary.add_column("Amount", justify="right")
    for status in ["compliant", "violation", "gap", "operational", "emerging"]:
        if status in totals:
            pct = totals[status] / grand * 100
            summary.add_row(
                Text(f"{STATUS_ICON[status]} {status}", style=STATUS_COLOR[status]),
                f"${totals[status]:>9,.0f}  ({pct:.1f}%)",
            )
    summary.add_row("─" * 22, "─" * 22)
    summary.add_row("Total", f"${grand:>9,.0f}")
    console.print(summary)


# ─────────────────────────────────────────────────────────────────────────────
# Core analyser
# ─────────────────────────────────────────────────────────────────────────────

def fetch_all_repos(org) -> dict:
    """Returns {repo_name: []} for all non-archived repos in the org."""
    return {
        repo.name: []
        for repo in org.get_repos()
        if not repo.archived
    }


def analyse(token: str, org_name: str, team_slugs: list, gp: dict,
            ownership: dict, cloud_costs: dict, output: Optional[str],
            all_repos: bool = False) -> int:

    g = Github(token, per_page=100)
    try:
        org = g.get_organization(org_name)
    except GithubException as e:
        console.print(f"[red]Cannot access org '{org_name}': {e.data.get('message', e)}")
        return 1

    if all_repos:
        scope_label = "all repos"
        console.print(Panel(
            f"[bold cyan]Golden Path Gap Analyser[/]\n"
            f"Org: [yellow]{org_name}[/]  |  Scope: [yellow]entire org[/]",
            expand=False,
        ))
        console.print("\n[cyan]Fetching all repos in org…[/]")
        repo_teams = fetch_all_repos(org)
    else:
        scope_label = ', '.join(team_slugs)
        console.print(Panel(
            f"[bold cyan]Golden Path Gap Analyser[/]\n"
            f"Org: [yellow]{org_name}[/]  |  Teams: [yellow]{scope_label}[/]",
            expand=False,
        ))
        repo_teams = fetch_team_repos(org, team_slugs)
        if not repo_teams:
            console.print("[red]No repos found for the specified teams. Check team slugs.")
            return 1

    console.print(f"\n[cyan]Found {len(repo_teams)} active repos. Scanning dependency files…[/]\n")

    results = []
    for repo_name, teams in sorted(repo_teams.items()):
        try:
            repo = org.get_repo(repo_name)
            if repo.archived:
                continue
            lang       = repo.language or "Unknown"
            dep_files  = fetch_dep_files(repo)
            tools      = detect_tools(dep_files)
            eol_issues = detect_eol_issues(dep_files)
            passes, failures = score_repo(lang, tools, eol_issues, gp)
            results.append({
                "repo":      repo_name,
                "teams":     teams,
                "lang":      lang,
                "tools":     tools,
                "eol_issues": eol_issues,
                "passes":    passes,
                "failures":  failures,
            })
        except GithubException as e:
            console.print(f"[yellow]Skipped {repo_name}: {e.data.get('message','')}")

    render_repo_matrix(results, ownership)
    console.print()
    render_issues(results)
    console.print()
    render_tool_proliferation(results, gp)

    if cloud_costs:
        console.print()
        classified = classify_cloud_costs(cloud_costs, gp)
        render_cloud_costs(classified)

    total_repos    = len(results)
    fully_ok       = sum(1 for r in results if not r["failures"])
    total_checks   = sum(len(r["passes"]) + len(r["failures"]) for r in results)
    total_pass     = sum(len(r["passes"]) for r in results)

    console.print(Panel(
        f"[bold]Repos analysed:[/]      {total_repos}\n"
        f"[bold]Fully GP-compliant:[/]  {fully_ok}/{total_repos}\n"
        f"[bold]Overall GP score:[/]    {total_pass}/{total_checks} checks passing "
        f"({total_pass / max(total_checks, 1) * 100:.1f}%)",
        title="Summary",
        expand=False,
    ))

    if output:
        export = {
            "org":   org_name,
            "scope": "all-repos" if all_repos else team_slugs,
            "repos": [
                {
                    "repo":       r["repo"],
                    "teams":      r["teams"] or ["—"],
                    "lang":       r["lang"],
                    "tools":      list(r["tools"].keys()),
                    "eol_issues": r["eol_issues"],
                    "passes":     r["passes"],
                    "failures":   r["failures"],
                    "score":      f"{len(r['passes'])}/{len(r['passes'])+len(r['failures'])}",
                }
                for r in results
            ],
        }
        if cloud_costs:
            export["cloud_costs"] = classify_cloud_costs(cloud_costs, gp)
        with open(output, "w") as f:
            json.dump(export, f, indent=2)
        console.print(f"\n[green]Report exported → {output}[/]")

    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Analyse GitHub org repos against a Golden Path definition.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Quickstart — uses bundled golden_path.yml
  python gp_analyser.py --org myorg --teams platform-core

  # Custom Golden Path
  python gp_analyser.py --org myorg --teams platform-core --golden-path my_gp.yml

  # Full run with cost + ownership data
  python gp_analyser.py \\
    --org myorg \\
    --teams "platform-core,platform-infra,platform-security" \\
    --golden-path golden_path.yml \\
    --cloud-costs cloud_costs.csv \\
    --ownership ownership.csv \\
    --output report.json
        """,
    )
    parser.add_argument("--org",         required=True,  help="GitHub org name")
    parser.add_argument("--teams",       default=None,   help="Comma-separated GitHub team slugs (omit if using --all-repos)")
    parser.add_argument("--all-repos",   action="store_true", help="Scan all non-archived repos in the org instead of specific teams")
    parser.add_argument("--golden-path", default=None,   help="Path to Golden Path YAML (default: bundled golden_path.yml)")
    parser.add_argument("--cloud-costs", default=None,   help="AWS Cost Explorer CSV export (Service, Month1, Month2, …)")
    parser.add_argument("--ownership",   default=None,   help="CSV mapping repos to systems and teams (columns: repo, system, team, status)")
    parser.add_argument("--output",      default=None,   help="Export full results to JSON")
    parser.add_argument("--token",       default=None,   help="GitHub token (or set GITHUB_TOKEN env var)")
    args = parser.parse_args()

    if not args.all_repos and not args.teams:
        parser.error("Provide --teams <slugs> or --all-repos to scan the entire org.")

    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        parser.error("Provide --token or export GITHUB_TOKEN.")

    gp          = load_golden_path(args.golden_path)
    ownership   = load_ownership(args.ownership)
    cloud_costs = load_cloud_costs(args.cloud_costs)
    team_slugs  = [t.strip() for t in args.teams.split(",")] if args.teams else []

    raise SystemExit(analyse(token, org_name=args.org, team_slugs=team_slugs,
                             gp=gp, ownership=ownership, cloud_costs=cloud_costs,
                             output=args.output, all_repos=args.all_repos))


if __name__ == "__main__":
    main()
