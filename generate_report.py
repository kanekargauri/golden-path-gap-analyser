#!/usr/bin/env python3
"""
Generate a PDF report from gp_analyser.py JSON output.

Usage:
  python generate_report.py --input report.json --output report.pdf
"""

import json
import argparse
from collections import Counter, defaultdict
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

# ── Palette ───────────────────────────────────────────────────────────────────
C_GREEN   = colors.HexColor("#1a7a4a")
C_RED     = colors.HexColor("#c0392b")
C_YELLOW  = colors.HexColor("#d68910")
C_BLUE    = colors.HexColor("#1a5276")
C_MAGENTA = colors.HexColor("#7d3c98")
C_CYAN    = colors.HexColor("#117a8b")
C_LGREY   = colors.HexColor("#f4f6f7")
C_MGREY   = colors.HexColor("#d5d8dc")
C_DGREY   = colors.HexColor("#2c3e50")
C_WHITE   = colors.white

STATUS_COLOR = {
    "compliant":   C_GREEN,
    "violation":   C_RED,
    "gap":         C_YELLOW,
    "operational": C_CYAN,
    "emerging":    C_MAGENTA,
}
STATUS_LABEL = {
    "compliant":   "✓ Compliant",
    "violation":   "✗ Violation",
    "gap":         "△ Gap",
    "operational": "● Operational",
    "emerging":    "★ Emerging",
}

# ── Styles ────────────────────────────────────────────────────────────────────
def build_styles():
    base = getSampleStyleSheet()
    def S(name, **kw):
        return ParagraphStyle(name, **kw)

    return {
        "title":    S("title",    fontName="Helvetica-Bold", fontSize=26, textColor=C_DGREY,
                       spaceAfter=6, leading=32),
        "subtitle": S("subtitle", fontName="Helvetica",      fontSize=13, textColor=C_BLUE,
                       spaceAfter=4),
        "meta":     S("meta",     fontName="Helvetica",      fontSize=9,  textColor=colors.grey,
                       spaceAfter=16),
        "h1":       S("h1",       fontName="Helvetica-Bold", fontSize=16, textColor=C_DGREY,
                       spaceBefore=18, spaceAfter=8),
        "h2":       S("h2",       fontName="Helvetica-Bold", fontSize=12, textColor=C_BLUE,
                       spaceBefore=12, spaceAfter=6),
        "h3":       S("h3",       fontName="Helvetica-Bold", fontSize=10, textColor=C_DGREY,
                       spaceBefore=8,  spaceAfter=4),
        "body":     S("body",     fontName="Helvetica",      fontSize=9,  textColor=C_DGREY,
                       leading=14, spaceAfter=4),
        "small":    S("small",    fontName="Helvetica",      fontSize=8,  textColor=colors.grey,
                       leading=12),
        "code":     S("code",     fontName="Courier",        fontSize=8,  textColor=C_DGREY,
                       leading=12),
        "callout":  S("callout",  fontName="Helvetica-Bold", fontSize=10, textColor=C_RED,
                       leading=14, spaceAfter=4),
    }


def hr(color=C_MGREY, thickness=0.5):
    return HRFlowable(width="100%", thickness=thickness, color=color, spaceAfter=8, spaceBefore=4)


def stat_table(stats: list):
    """stats = [(label, value, color), ...]"""
    data = [[Paragraph(f"<b>{v}</b>", ParagraphStyle("sv", fontName="Helvetica-Bold",
                        fontSize=22, textColor=c, alignment=TA_CENTER)),
             Paragraph(lbl, ParagraphStyle("sl", fontName="Helvetica", fontSize=9,
                        textColor=colors.grey, alignment=TA_CENTER))]
            for lbl, v, c in stats]

    # transpose to 2 rows
    row1 = [d[0] for d in data]
    row2 = [d[1] for d in data]
    t = Table([row1, row2], colWidths=[3.8*cm]*len(stats))
    t.setStyle(TableStyle([
        ("ALIGN",       (0,0), (-1,-1), "CENTER"),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("BACKGROUND",  (0,0), (-1,-1), C_LGREY),
        ("GRID",        (0,0), (-1,-1), 0.5, C_MGREY),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [C_LGREY, colors.white]),
        ("TOPPADDING",  (0,0), (-1,-1), 10),
        ("BOTTOMPADDING",(0,0),(-1,-1), 10),
    ]))
    return t


def data_table(headers, rows, col_widths, style_extra=None):
    header_row = [Paragraph(f"<b>{h}</b>", ParagraphStyle("th", fontName="Helvetica-Bold",
                   fontSize=8, textColor=C_WHITE)) for h in headers]
    body_rows = []
    for row in rows:
        body_rows.append([
            Paragraph(str(cell), ParagraphStyle("td", fontName="Helvetica", fontSize=8,
                       textColor=C_DGREY, leading=11))
            for cell in row
        ])

    style = TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  C_DGREY),
        ("TEXTCOLOR",     (0,0), (-1,0),  C_WHITE),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [C_WHITE, C_LGREY]),
        ("GRID",          (0,0), (-1,-1), 0.3, C_MGREY),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ("RIGHTPADDING",  (0,0), (-1,-1), 6),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
    ])
    if style_extra:
        for s in style_extra:
            style.add(*s)

    t = Table([header_row] + body_rows, colWidths=col_widths, repeatRows=1)
    t.setStyle(style)
    return t


# ── Report sections ───────────────────────────────────────────────────────────

def section_cover(story, styles, data, generated_at):
    story.append(Spacer(1, 1.5*cm))
    story.append(Paragraph("Golden Path Gap Analysis", styles["title"]))
    story.append(Paragraph("Engineering Standards Compliance Report", styles["subtitle"]))
    story.append(Paragraph(f"Generated: {generated_at}", styles["meta"]))
    story.append(hr(C_BLUE, 1.5))
    story.append(Spacer(1, 0.5*cm))

    repos  = data["repos"]
    total  = len(repos)
    fully  = sum(1 for r in repos if not r["failures"])
    t_chk  = sum(len(r["passes"]) + len(r["failures"]) for r in repos)
    t_pass = sum(len(r["passes"]) for r in repos)
    eol    = sum(1 for r in repos if any("EOL" in f for f in r["failures"]))
    pct    = t_pass / max(t_chk, 1) * 100

    story.append(stat_table([
        ("Repos Scanned",        str(total),           C_BLUE),
        ("Fully GP-Compliant",   f"{fully}",            C_GREEN),
        ("Overall GP Score",     f"{pct:.1f}%",         C_GREEN if pct > 70 else C_YELLOW if pct > 50 else C_RED),
        ("EOL Runtime Repos",    str(eol),              C_RED),
        ("Checks Passed",        f"{t_pass}/{t_chk}",   C_BLUE),
    ]))
    story.append(Spacer(1, 1*cm))


def section_language(story, styles, repos):
    story.append(Paragraph("Language Distribution", styles["h1"]))
    story.append(hr())

    langs = Counter(r["lang"] for r in repos)
    total = len(repos)

    gp_backend  = {"Go"}
    gp_allowed  = {"TypeScript", "NodeJs", "JavaScript"}
    gp_ds       = {"Python"}
    gp_mobile   = {"Kotlin", "Swift"}
    gp_avoid    = {"Java", "Ruby", "PHP"}

    def lang_status(l):
        if l in gp_backend:  return ("✓ GP preferred",  C_GREEN)
        if l in gp_allowed:  return ("△ Allowed",        C_YELLOW)
        if l in gp_ds:       return ("✓ DS standard",    C_GREEN)
        if l in gp_mobile:   return ("✓ Mobile std",     C_GREEN)
        if l in gp_avoid:    return ("✗ Avoid",          C_RED)
        return ("△ Not defined",  C_YELLOW)

    rows = []
    extra_style = []
    for i, (lang, count) in enumerate(langs.most_common()):
        label, color = lang_status(lang)
        bar = "█" * int(count / total * 30)
        rows.append([lang, str(count), f"{count/total*100:.1f}%", bar, label])
        col = 4
        extra_style.append(("TEXTCOLOR", (col, i+1), (col, i+1), color))
        extra_style.append(("FONTNAME",  (col, i+1), (col, i+1), "Helvetica-Bold"))

    story.append(data_table(
        ["Language", "Repos", "%", "Distribution", "GP Status"],
        rows,
        [3.5*cm, 1.8*cm, 1.8*cm, 5*cm, 4*cm],
        extra_style,
    ))
    story.append(Spacer(1, 0.3*cm))


def section_eol(story, styles, repos):
    eol_repos = [(r["repo"], r["lang"], f)
                 for r in repos for f in r["failures"] if "EOL" in f]
    if not eol_repos:
        return

    story.append(PageBreak())
    story.append(Paragraph("🔴  Critical — EOL Runtime Issues", styles["h1"]))
    story.append(Paragraph(
        f"{len(eol_repos)} EOL runtime(s) detected across {len(set(r for r,_,_ in eol_repos))} repos. "
        "These represent active security vulnerabilities — unpatched CVEs, no vendor support, "
        "and no security backports. These should be resolved before any other GP work.",
        styles["body"],
    ))
    story.append(hr(C_RED, 1))

    rows = [(repo, lang, issue.replace("EOL runtime: ","")) for repo, lang, issue in eol_repos]
    extra = [("TEXTCOLOR", (0, i+1), (0, i+1), C_RED) for i in range(len(rows))]
    story.append(data_table(
        ["Repository", "Language", "Issue"],
        rows,
        [5*cm, 3*cm, 9.5*cm],
        extra,
    ))


def section_violations(story, styles, repos):
    story.append(PageBreak())
    story.append(Paragraph("GP Violations & Gaps", styles["h1"]))
    story.append(hr())

    failure_counter = Counter()
    failure_repos   = defaultdict(list)
    for r in repos:
        for f in r["failures"]:
            if "EOL" in f:
                continue
            key = f
            failure_counter[key] += 1
            failure_repos[key].append(r["repo"])

    # Group by category
    categories = defaultdict(list)
    for failure, count in failure_counter.most_common():
        if "Language:" in failure:
            categories["Language"].append((failure, count, failure_repos[failure]))
        elif "Cache:" in failure:
            categories["Cache"].append((failure, count, failure_repos[failure]))
        elif "Observability:" in failure:
            categories["Observability"].append((failure, count, failure_repos[failure]))
        elif "Database:" in failure:
            categories["Database"].append((failure, count, failure_repos[failure]))
        elif "Messaging:" in failure:
            categories["Messaging"].append((failure, count, failure_repos[failure]))
        else:
            categories["Other"].append((failure, count, failure_repos[failure]))

    for cat, items in categories.items():
        story.append(Paragraph(cat, styles["h2"]))
        rows = []
        for failure, count, affected in items:
            label = failure.split(":", 1)[-1].strip()
            repo_str = ", ".join(affected[:6]) + ("…" if len(affected) > 6 else "")
            rows.append([str(count), label, repo_str])
        story.append(data_table(
            ["# Repos", "Issue", "Affected Repos"],
            rows,
            [1.8*cm, 6*cm, 9.7*cm],
        ))
        story.append(Spacer(1, 0.2*cm))


def section_tool_proliferation(story, styles, repos):
    story.append(PageBreak())
    story.append(Paragraph("Tool Proliferation", styles["h1"]))
    story.append(Paragraph(
        "All tools detected from dependency files across the org, ranked by adoption.",
        styles["body"],
    ))
    story.append(hr())

    tool_counts = Counter()
    for r in repos:
        for t in r["tools"]:
            tool_counts[t] += 1

    violations = {"Redis","New Relic","Datadog","Cassandra","Neo4j","DynamoDB","BullMQ"}
    compliant  = {"MySQL","PostgreSQL","MongoDB","StatsD/TIG","OpenTelemetry",
                  "Amplitude","ValKey","PyTorch"}

    def tstatus(t):
        if t in violations: return "✗ Violation", C_RED
        if t in compliant:  return "✓ Compliant", C_GREEN
        return "△ Gap / Ambiguous", C_YELLOW

    rows = []
    extra = []
    for i, (tool, count) in enumerate(tool_counts.most_common()):
        label, color = tstatus(tool)
        rows.append([tool, str(count), label])
        extra.append(("TEXTCOLOR", (2, i+1), (2, i+1), color))
        extra.append(("FONTNAME",  (2, i+1), (2, i+1), "Helvetica-Bold"))

    story.append(data_table(
        ["Tool", "# Repos Using It", "GP Status"],
        rows,
        [5*cm, 3.5*cm, 9*cm],
        extra,
    ))


def section_cloud_costs(story, styles, cloud_costs):
    if not cloud_costs:
        return
    story.append(PageBreak())
    story.append(Paragraph("Cloud Cost × Golden Path", styles["h1"]))
    story.append(Paragraph(
        "Cloud provider spend classified against the Golden Path for the latest available month.",
        styles["body"],
    ))
    story.append(hr())

    totals = defaultdict(float)
    for item in cloud_costs:
        totals[item["status"]] += item["cost"]
    grand = sum(totals.values())

    # Cost summary stats
    story.append(stat_table([
        ("Total Spend",   f"${grand:,.0f}",                         C_BLUE),
        ("GP Compliant",  f"${totals['compliant']:,.0f}  ({totals['compliant']/grand*100:.0f}%)",  C_GREEN),
        ("GP Violations", f"${totals['violation']:,.0f}  ({totals['violation']/grand*100:.0f}%)",  C_RED),
        ("GP Gaps",       f"${totals['gap']:,.0f}  ({totals['gap']/grand*100:.0f}%)",              C_YELLOW),
        ("Operational",   f"${totals['operational']:,.0f}  ({totals['operational']/grand*100:.0f}%)", C_CYAN),
    ]))
    story.append(Spacer(1, 0.5*cm))

    rows = []
    extra = []
    for i, item in enumerate(cloud_costs):
        label = STATUS_LABEL.get(item["status"], item["status"])
        color = STATUS_COLOR.get(item["status"], C_DGREY)
        rows.append([item["service"], f"${item['cost']:,.0f}", label, item["note"]])
        extra.append(("TEXTCOLOR", (2, i+1), (2, i+1), color))
        extra.append(("FONTNAME",  (2, i+1), (2, i+1), "Helvetica-Bold"))

    story.append(data_table(
        ["Service", "Cost/mo", "GP Status", "Note"],
        rows,
        [4.5*cm, 2*cm, 2.8*cm, 8.2*cm],
        extra,
    ))


def section_repo_matrix(story, styles, repos):
    story.append(PageBreak())
    story.append(Paragraph("Repository Tech Matrix", styles["h1"]))
    story.append(Paragraph(
        "All scanned repositories with detected tools and GP score. "
        "Score = checks passed / total checks.",
        styles["body"],
    ))
    story.append(hr())

    db_tools  = {"MySQL","PostgreSQL","MongoDB","Cassandra","DynamoDB","Elasticsearch",
                 "Neo4j","SQLite","Redis","ValKey","Memcached"}
    msg_tools = {"Kafka","SQS","Kinesis","SNS","RabbitMQ","BullMQ"}
    obs_tools = {"StatsD/TIG","OpenTelemetry","New Relic","Datadog","Amplitude","Sentry"}

    rows = []
    extra = []
    for i, r in enumerate(sorted(repos, key=lambda x: x["repo"])):
        passes = len(r["passes"])
        fails  = len(r["failures"])
        total  = passes + fails
        score  = f"{passes}/{total}"
        pct    = passes / total if total else 1
        color  = C_GREEN if pct == 1 else (C_YELLOW if pct >= 0.6 else C_RED)

        db_str  = ", ".join(t for t in r["tools"] if t in db_tools)  or "—"
        msg_str = ", ".join(t for t in r["tools"] if t in msg_tools) or "—"
        obs_str = ", ".join(t for t in r["tools"] if t in obs_tools) or "—"
        eol_flag = "🔴" if r["eol_issues"] else ""

        rows.append([eol_flag + r["repo"], r["lang"], db_str[:30], msg_str[:24], obs_str[:24], score])
        extra.append(("TEXTCOLOR", (5, i+1), (5, i+1), color))
        extra.append(("FONTNAME",  (5, i+1), (5, i+1), "Helvetica-Bold"))

    story.append(data_table(
        ["Repository", "Language", "DB / Cache", "Messaging", "Observability", "Score"],
        rows,
        [4.5*cm, 2.2*cm, 3.5*cm, 3*cm, 3*cm, 1.3*cm],
        extra,
    ))


# ── Main ──────────────────────────────────────────────────────────────────────

def generate(input_path: str, output_path: str):
    with open(input_path) as f:
        data = json.load(f)

    generated_at = datetime.now().strftime("%B %d, %Y  %H:%M")
    styles = build_styles()

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.8*cm,  bottomMargin=1.8*cm,
        title="Golden Path Gap Analysis Report",
        author="GP Gap Analyser",
    )

    story = []
    repos       = data["repos"]
    cloud_costs = data.get("cloud_costs", [])

    section_cover(story, styles, data, generated_at)
    section_language(story, styles, repos)
    section_eol(story, styles, repos)
    section_violations(story, styles, repos)
    section_tool_proliferation(story, styles, repos)
    section_cloud_costs(story, styles, cloud_costs)
    section_repo_matrix(story, styles, repos)

    doc.build(story)
    print(f"Report written → {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate PDF report from GP analyser JSON output.")
    parser.add_argument("--input",  default="report.json", help="Path to JSON output from gp_analyser.py")
    parser.add_argument("--output", default="gp_report.pdf", help="Output PDF path")
    args = parser.parse_args()
    generate(args.input, args.output)


if __name__ == "__main__":
    main()
