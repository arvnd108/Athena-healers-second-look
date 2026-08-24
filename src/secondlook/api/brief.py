"""Tumour-board brief: server-rendered HTML, print stylesheet, no Jinja2.

One view. Adding jinja2 for a single f-string page would be a new dependency
this issue does not need.
"""

from __future__ import annotations

from secondlook.query.contracts import CaseSummary, FindingDetail


def render_brief(summary: CaseSummary, findings: list[FindingDetail]) -> str:
    alteration_rows = (
        "".join(f"<li>{_esc(a.gene)} {_esc(a.variant)}</li>" for a in summary.alterations)
        or "<li>None recorded</li>"
    )
    finding_blocks = []
    for finding in findings:
        refs = _esc(str(finding.evidence_ref))
        finding_blocks.append(
            "<article class='finding'>"
            f"<h3>{_esc(finding.claim)}</h3>"
            f"<p>class: {_esc(finding.evidence_class)} · status: {_esc(finding.status)}</p>"
            f"<p>question: {_esc(finding.question_text)}</p>"
            f"<p>evidence: {refs}</p>"
            f"<p>assumptions: {_esc('; '.join(finding.assumptions) or 'none')}</p>"
            "</article>"
        )
    findings_html = "".join(finding_blocks) or "<p>No active findings.</p>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Brief — {_esc(summary.label)}</title>
  <style>
    @media print {{
      body {{ font-size: 11pt; }}
      nav, .no-print {{ display: none; }}
    }}
    body {{ font-family: Georgia, serif; margin: 2rem; color: #111; }}
    h1, h2 {{ font-family: system-ui, sans-serif; }}
    .meta {{ color: #444; }}
  </style>
</head>
<body>
  <h1>Tumour-board brief: {_esc(summary.label)}</h1>
  <p class="meta">{_esc(summary.cancer_type)}
     · age {summary.age_years if summary.age_years is not None else "unrecorded"}
     · stage {_esc(summary.stage or "unrecorded")}</p>
  <h2>Alterations</h2>
  <ul>{alteration_rows}</ul>
  <h2>Questions</h2>
  <p>open {summary.question_counts.get("open", 0)},
     answered {summary.question_counts.get("answered", 0)},
     suppressed {summary.question_counts.get("suppressed", 0)}</p>
  <h2>Findings</h2>
  {findings_html}
</body>
</html>
"""


def _esc(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
