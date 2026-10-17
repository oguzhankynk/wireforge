"""Coding-rule findings in the lifecycle documents: code_standard.md section and rule DS-9."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wireforge import docs_markdown as md
from wireforge import findings as rule_findings
from wireforge import results as test_results

if TYPE_CHECKING:
    from wireforge.lifecycle import DocsContext

NOT_PROVIDED = (
    "No external rule checker results provided. docs/CODING_RULES.md maps the coding-rule "
    "categories of MISRA C++ and AUTOSAR C++14 to the checks that run on this code; a conformance "
    "claim needs the results of a licensed checker, which `--rule-findings` takes."
)


def unjustified(
    ctx: DocsContext,
) -> tuple[list[rule_findings.RuleFinding], list[test_results.Deviation]]:
    """Return the findings without a deviation and the rule deviations nobody used."""
    return rule_findings.match_rules(list(ctx.rule_findings), list(ctx.deviations))


def findings(ctx: DocsContext) -> list[tuple[str, str, str]]:
    """Return (rule, message, pointer) for DS-9: a finding without a deviation."""
    if not ctx.rules_provided:
        return []
    open_findings, _ = unjustified(ctx)
    return [
        (
            "DS-9",
            f"{gap.tool} {gap.rule} in {gap.file}: {gap.count} finding(s) without a deviation",
            "",
        )
        for gap in rule_findings.group(open_findings)
    ]


def section(ctx: DocsContext) -> list[str]:
    """Return the "Rule findings" section of code_standard.md."""
    lines = ["## Rule findings", ""]
    if not ctx.rules_provided:
        return [*lines, NOT_PROVIDED, ""]
    everything = list(ctx.rule_findings)
    open_findings, unused = unjustified(ctx)
    per_tool = [
        (
            tool,
            sum(item.tool == tool for item in everything),
            sum(item.tool == tool for item in open_findings),
        )
        for tool in sorted({item.tool for item in everything})
    ]
    scope = (
        f"{len(everything)} findings in generated code and the runtime; "
        f"{ctx.outside_scope} elsewhere (outside scope)."
    )
    lines += [scope, "", *md.table(("Tool", "Findings", "Without a deviation"), per_tool), ""]
    counts: dict[tuple[str, str], list[int]] = {}
    for item in everything:
        counts.setdefault((item.tool, item.rule), [0, 0])[0] += 1
    for item in open_findings:
        counts[item.tool, item.rule][1] += 1
    rows = [(tool, rule, found[0], found[1]) for (tool, rule), found in sorted(counts.items())]
    lines += [*md.table(("Tool", "Rule", "Findings", "Without a deviation"), rows), ""]
    gaps = rule_findings.group(open_findings)
    lines += ["### Findings without a deviation", ""]
    lines += md.table(
        ("Tool", "Rule", "File", "Lines"),
        [(g.tool, g.rule, g.file, ", ".join(str(n) for n in g.lines)) for g in gaps],
    )
    lines += ["", "### Unused deviations", ""]
    lines += [f"- {i.tool} {i.rule} {i.file}: {i.rationale}" for i in unused] or ["none"]
    return [*lines, ""]
