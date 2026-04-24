# Golden Path Gap Analyser

Analyses GitHub org repositories against a defined Golden Path, combining three signals into one report:

- **Actual tools used** — detected from dependency files (`package.json`, `go.mod`, `requirements.txt`, `Dockerfile`, etc.)
- **Team ownership** — pulled from GitHub Teams API or a CSV
- **Cloud provider costs** — mapped against GP compliance from an AWS Cost Explorer export

---

## What it does

For each repository owned by the specified GitHub team(s), the analyser:

1. Fetches dependency files from the repo root
2. Detects tools in use — databases, caches, messaging systems, observability stacks, frameworks, and external services
3. Scores each repo against your Golden Path definition
4. Flags EOL runtimes (Python 2.7, Node 16, Java 8, Ubuntu 18.04, etc.)
5. Shows tool proliferation across repos — how many services use Kafka vs SQS vs Kinesis, for example
6. Maps your cloud cost line items to GP status: ✅ compliant, ❌ violation, ⚠️ gap, 🔧 operational, 🆕 emerging

---

## Setup

```bash
pip install -r requirements.txt
```

You need a GitHub personal access token with `repo` and `read:org` scopes.

```bash
export GITHUB_TOKEN=ghp_...
```

---

## Usage

### Quickstart — uses the bundled `golden_path.yml`

```bash
python gp_analyser.py --org <your-org> --teams <team-slug>
```

### Multiple teams

```bash
python gp_analyser.py \
  --org <your-org> \
  --teams "platform-core,platform-infra,platform-security"
```

### Custom Golden Path definition

```bash
python gp_analyser.py \
  --org <your-org> \
  --teams "platform-core" \
  --golden-path /path/to/your_golden_path.yml
```

### Full run — Golden Path + cloud costs + ownership + JSON export

```bash
python gp_analyser.py \
  --org <your-org> \
  --teams "platform-core,platform-infra" \
  --golden-path golden_path.yml \
  --cloud-costs cloud_costs.csv \
  --ownership ownership.csv \
  --output report.json
```

---

## Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--org` | ✅ | — | GitHub organisation name |
| `--teams` | ✅ | — | Comma-separated team slugs (find slugs at `github.com/orgs/<org>/teams`) |
| `--golden-path` | ❌ | `golden_path.yml` | Path to your Golden Path YAML definition |
| `--cloud-costs` | ❌ | — | AWS Cost Explorer CSV export |
| `--ownership` | ❌ | — | CSV mapping repos to systems and teams |
| `--output` | ❌ | — | Export full results to JSON |
| `--token` | ❌ | `$GITHUB_TOKEN` | GitHub token (prefer env var) |

---

## Input file formats

### `golden_path.yml`

Defines your engineering standards. A suggested default is bundled at the repo root — edit it to match your organisation. Key sections:

```yaml
languages:
  backend:
    preferred: [Go]
    allowed: [Go, NodeJs]
    avoid: [Java, Ruby]

databases:
  cache:
    preferred: [ValKey]
    avoid: [Redis, ElastiCache]
  nosql:
    preferred: [MongoDB]
  avoid: [Cassandra, DynamoDB, Neo4j]

observability:
  preferred: [StatsD/TIG, OpenTelemetry]
  avoid: [New Relic, Datadog, CloudWatch]

cloud_services:
  "ElastiCache":
    status: violation
    note: "GP mandates ValKey"
  "Simple Queue Service":
    status: gap
    note: "Messaging standard not yet defined"
```

See the full annotated default at [`golden_path.yml`](./golden_path.yml).

### `cloud_costs.csv`

Standard AWS Cost Explorer export format — download directly from the AWS console:

```
Service,Oct 2025 ($),Nov 2025 ($),Mar 2026 ($)
Elastic Compute Cloud,10000,10500,12000
ElastiCache,3000,2800,2300
...
```

The analyser uses the **last column** as the current month's cost. See [`sample/cloud_costs.csv`](./sample/cloud_costs.csv) for a template.

### `ownership.csv`

Optional — maps repos to systems and teams for richer reporting:

```csv
repo,system,team,status
my-auth-service,Auth,platform-core,active
my-gateway,GraphQL Gateway,platform-core,active
```

See [`sample/ownership.csv`](./sample/ownership.csv) for a template.

---

## Output

Four tables are printed to the terminal:

**Repository Tech Matrix** — per-repo language, detected tools (DB, messaging, observability), and GP score.

**Issues** — split into three severity levels:
- 🔴 Critical — EOL runtimes (Python 2.7, Node 16, Java 8, Ubuntu 18.04, etc.)
- ❌ Violations — tools that directly contradict the Golden Path
- ⚠️ Gaps — tools not mentioned in the GP, or preferred alternatives not being used

**Tool Proliferation** — every detected tool ranked by how many repos use it, with GP status.

**Cloud Cost × GP Status** — each cost line item classified and totalled by status, so you can see exactly how much spend is compliant vs in violation vs ungoverned.

Plus a summary panel: repos analysed, fully compliant count, and overall GP score.

Results can be exported to JSON with `--output report.json` for use in dashboards or CI pipelines.

---

## How tool detection works

The analyser scans dependency files at the repo root and matches against known patterns:

| File | What's extracted |
|---|---|
| `package.json` | npm dependencies |
| `requirements.txt` / `Pipfile` | Python packages |
| `go.mod` | Go modules |
| `pom.xml` / `build.gradle` | Java/Kotlin deps |
| `Dockerfile` | Base image (EOL detection) |
| `docker-compose.yml` | Local service dependencies |

Tools detected include: Redis, ValKey, MySQL, PostgreSQL, MongoDB, Cassandra, DynamoDB, Elasticsearch, Neo4j, Kafka, SQS, Kinesis, SNS, RabbitMQ, StatsD/TIG, OpenTelemetry, New Relic, Datadog, Amplitude, Sentry, Firebase, Twilio, AWS Glue, Directus, and more.

---

## CI/CD usage

Run in a Jenkins pipeline or GitHub Actions to track GP compliance over time:

```bash
python gp_analyser.py \
  --org "$ORG" \
  --teams "$TEAMS" \
  --golden-path golden_path.yml \
  --cloud-costs cloud_costs.csv \
  --output report.json
```

Archive `report.json` as a build artifact for audit trails or downstream processing.

---

## Adding a new tool detector

Edit `TOOL_DETECTORS` in `gp_analyser.py`:

```python
TOOL_DETECTORS = {
    ...
    "MyTool": [r'"mytool"', r'mytool-client', r'go-mytool'],
    ...
}
```

Then classify it in `golden_path.yml` under `observability.avoid`, `databases.avoid`, or `messaging.allowed` as appropriate.
