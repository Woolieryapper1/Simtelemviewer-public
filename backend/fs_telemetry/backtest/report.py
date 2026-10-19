"""The backtest report, as markdown (and a simple HTML page with the same tables)."""

from __future__ import annotations

import html
import math
from datetime import datetime

from .harness import SourceResult

STATUS_COLOURS = ("pass", "FAIL", "better")
SOURCE_NAMES ={"synthetic": "Synthetic runs (known answer)", "ac": "Assetto Corsa runs", "iracing": "iRacing runs"}


def _fmt(x) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "-"
    return f"{x:.4g}" if isinstance(x, float) else str(x)


def _tables(results: list[SourceResult], passed: bool, extra: list[str]) -> list[tuple]:
    """The report as a list of blocks: ("h1"/"h2"/"h3"/"p", text) or ("table", header, rows)."""
    blocks: list[tuple] = [("h1", "Backtest report"),
                           ("p", f"{datetime.now():%Y-%m-%d %H:%M} - gate: **{'PASS' if passed else 'FAIL'}**")]
    blocks += [("p", line) for line in extra]
    failures = [(r, v) for r in results for v in r.verdicts if v.status == "FAIL"]
    if failures:
        blocks.append(("table", ["Estimator", "Runs", "Metric", "Value", "Baseline", "Why"],
                       [[r.adapter.name, r.source, v.metric, _fmt(v.value), _fmt(v.baseline), v.why]
                        for r, v in failures]))
    if not results:
        blocks.append(("p", "No runs to check."))
    for name in dict.fromkeys(r.adapter.name for r in results):
        mine = [r for r in results if r.adapter.name == name]
        blocks += [("h2", mine[0].adapter.title), ("p", mine[0].adapter.notes)]
        for r in mine:
            n_ok = sum(m.values is not None for m in r.measured)
            blocks.append(("h3", f"{SOURCE_NAMES.get(r.source, r.source)}: {n_ok} of {len(r.measured)} estimated"))
            blocks += [("p", note) for note in r.notes]
            blocks.append(("table", ["Metric", "Value", "Baseline", "Status", ""],
                           [[v.metric, _fmt(v.value), _fmt(v.baseline), v.status, v.why] for v in r.verdicts]))
            keys = list(dict.fromkeys(k for m in r.measured if m.values for k in m.values))
            rows = [[m.run.id, m.run.group, *(_fmt(m.values.get(k)) for k in keys)] for m in r.measured if m.values]
            if rows:
                blocks.append(("table", ["Run", "Car / setup", *keys], rows))
            skipped = [[m.run.id, m.error or "estimator gave no answer (e.g. missing channels)"]
                       for m in r.measured if m.values is None]
            if skipped:
                blocks.append(("table", ["Not estimated", "Why"], skipped))
    return blocks


def markdown(results: list[SourceResult], passed: bool, extra: list[str] = ()) -> str:
    out = []
    for block in _tables(results, passed, list(extra)):
        kind = block[0]
        if kind in ("h1", "h2", "h3"):
            out.append("#" * int(kind[1]) + " " + block[1])
        elif kind == "p":
            out.append(block[1])
        else:
            header, rows = block[1], block[2]
            cell = lambda c: str(c).replace("|", "/")  # noqa: E731
            out.append("\n".join(["| " + " | ".join(map(cell, header)) + " |", "|" + "---|" * len(header),
                                  *("| " + " | ".join(map(cell, row)) + " |" for row in rows)]))
    return "\n\n".join(out) + "\n"


def html_page(results: list[SourceResult], passed: bool, extra: list[str] = ()) -> str:
    parts = []
    for block in _tables(results, passed, list(extra)):
        kind = block[0]
        if kind == "table":
            head = "".join(f"<th>{html.escape(str(h))}</th>" for h in block[1])
            # status cells (pass / FAIL / better) get coloured
            cell = lambda c: f'<td class="{c}">{c}</td>' if c in STATUS_COLOURS else f"<td>{html.escape(str(c))}</td>"  # noqa: E731
            body = "".join("<tr>" + "".join(map(cell, row)) + "</tr>" for row in block[2])
            parts.append(f'<div class="scroll"><table><tr>{head}</tr>{body}</table></div>')
        else:
            text = html.escape(block[1]).replace("**PASS**", "<b class='pass'>PASS</b>").replace(
                "**FAIL**", "<b class='FAIL'>FAIL</b>")
            parts.append(f"<{kind}>{text}</{kind}>")
    style = ("body{background:#18181b;color:#e4e4e7;font:14px system-ui,sans-serif;margin:24px}"
             "table{border-collapse:collapse;margin:8px 0}td,th{border:1px solid #3f3f46;padding:3px 8px;"
             "text-align:left;white-space:nowrap}th{background:#27272a}.scroll{overflow-x:auto}"
             ".FAIL{color:#f87171;font-weight:600}.pass{color:#4ade80}.better{color:#818cf8}h2{color:#a5b4fc}")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>Backtest report</title>"
            f"<style>{style}</style></head><body>{''.join(parts)}</body></html>\n")
