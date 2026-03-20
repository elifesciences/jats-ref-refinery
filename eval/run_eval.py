"""Evaluation script for jats-ref-refinery.

Strips existing DOIs and PMIDs from JATS XML fixtures, runs the enrichment
pipeline, and compares found PIDs to report precision, recall, and F1 score.

After each run, writes:
  eval/results/latest.json         — machine-readable scores
  eval/results/latest_detail.json  — per-ref outcomes (skip with --no-detail)
  eval/results/latest.png          — bar chart (precision / recall / F1)
  eval/README.md                   — human-readable summary with embedded chart

Usage:
    uv run python eval/run_eval.py [--verbose] [--delay SECS]
                                   [--no-detail] [fixture ...]

    --verbose    Print per-ref breakdown (TP / FP / FN)
    --delay      Seconds to wait between fixtures (default: 0.5)
    --no-detail  Skip writing per-ref outcomes to latest_detail.json
    fixture      One or more paths to JATS XML files.
                 Defaults to all *.xml files in eval/fixtures/.
"""

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

logging.getLogger("matplotlib").setLevel(logging.WARNING)

import matplotlib.pyplot as plt  # noqa: E402
from lxml import etree  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.enricher import enrich_jats  # noqa: E402

_RESULTS_DIR = Path(__file__).parent / "results"
_README = Path(__file__).parent / "README.md"


def _extract_ground_truth(
    tree: etree._ElementTree,
) -> dict[str, dict[str, str]]:
    """Return {ref_id: {"doi": ..., "pmid": ...}} for every <ref> that has
    at least one <pub-id> in the source XML."""
    ground_truth: dict[str, dict[str, str]] = {}
    for ref_el in tree.getroot().iter("ref"):
        ref_id = ref_el.get("id", "")
        doi = ""
        pmid = ""
        for pub_id_el in ref_el.iter("pub-id"):
            id_type = pub_id_el.get("pub-id-type", "")
            if id_type == "doi" and not doi:
                doi = (pub_id_el.text or "").strip().lower()
            elif id_type == "pmid" and not pmid:
                pmid = (pub_id_el.text or "").strip()
        if doi or pmid:
            ground_truth[ref_id] = {"doi": doi, "pmid": pmid}
    return ground_truth


def _strip_pub_ids(raw_xml: bytes) -> bytes:
    """Remove all <pub-id> elements from the XML and return the modified
    bytes."""
    parser = etree.XMLParser(
        remove_blank_text=False, resolve_entities=False, load_dtd=False
    )
    tree = etree.parse(BytesIO(raw_xml), parser)
    for pub_id_el in list(tree.getroot().iter("pub-id")):
        parent = pub_id_el.getparent()
        if parent is not None:
            parent.remove(pub_id_el)
    doctype = tree.docinfo.doctype
    out = BytesIO()
    tree.write(out, encoding="UTF-8", xml_declaration=True, doctype=doctype)
    return out.getvalue()


def _extract_recovered(
    raw_xml: bytes,
) -> dict[str, dict[str, str]]:
    """Return {ref_id: {"doi": ..., "pmid": ...}} for every <ref> that has
    a <pub-id> in the enriched output."""
    parser = etree.XMLParser(
        remove_blank_text=False, resolve_entities=False, load_dtd=False
    )
    tree = etree.parse(BytesIO(raw_xml), parser)
    recovered: dict[str, dict[str, str]] = {}
    for ref_el in tree.getroot().iter("ref"):
        ref_id = ref_el.get("id", "")
        doi = ""
        pmid = ""
        for pub_id_el in ref_el.iter("pub-id"):
            id_type = pub_id_el.get("pub-id-type", "")
            if id_type == "doi" and not doi:
                doi = (pub_id_el.text or "").strip().lower()
            elif id_type == "pmid" and not pmid:
                pmid = (pub_id_el.text or "").strip()
        if doi or pmid:
            recovered[ref_id] = {"doi": doi, "pmid": pmid}
    return recovered


def _doi_version_match(a: str, b: str) -> bool:
    """Return True if two DOIs refer to different versions of the same article.

    Applies to publishers that use single-digit version suffixes:
      - eLife (10.7554)      e.g. 10.7554/elife.89482.2
      - F1000Research (10.12688)  e.g. 10.12688/f1000research.12345.2

    Strips any trailing .N suffix from both DOIs before comparing.
    """
    _VERSIONED_PREFIXES = ("10.7554/", "10.12688/")
    if not any(a.startswith(p) for p in _VERSIONED_PREFIXES):
        return False
    if not any(b.startswith(p) for p in _VERSIONED_PREFIXES):
        return False
    return re.sub(r'\.\d$', '', a) == re.sub(r'\.\d$', '', b)


def _score_pid(
    truth: dict[str, str],
    recovered: dict[str, str],
    pid: str,
) -> str:
    """Return "TP", "FP", "FN", "NEW", or "TN" for a single PID type.

    "NEW" means the service found a PID that was absent from the ground truth
    — this cannot be evaluated and is excluded from precision/recall/F1.
    "FP" is only returned when the ground truth has a value and the recovered
    value differs.
    """
    t = truth.get(pid, "")
    r = recovered.get(pid, "")
    if t and r:
        if t == r or _doi_version_match(t, r):
            return "TP"
        return "FP"
    if t and not r:
        return "FN"
    if not t and r:
        return "NEW"
    return "TN"


def _compute_metrics(
    ground_truth: dict[str, dict[str, str]],
    recovered: dict[str, dict[str, str]],
    verbose: bool,
    fixture_name: str,
    save_detail: bool = False,
) -> tuple[dict[str, int], list[dict]]:
    """Print per-ref breakdown (if verbose) and return (counts, detail_rows).

    detail_rows contains one entry per non-TN outcome and is only populated
    when save_detail is True.
    """
    tp = fp = fn = new = 0
    detail_rows: list[dict] = []

    for ref_id in sorted(ground_truth):
        truth = ground_truth[ref_id]
        rec = recovered.get(ref_id, {})

        for pid in ("doi", "pmid"):
            outcome = _score_pid(truth, rec, pid)
            if outcome == "TP":
                tp += 1
            elif outcome == "FP":
                fp += 1
            elif outcome == "FN":
                fn += 1
            elif outcome == "NEW":
                new += 1

            if verbose and outcome not in ("TN", "NEW"):
                t_val = truth.get(pid, "—")
                r_val = rec.get(pid, "—")
                print(
                    f"  [{outcome}] {fixture_name} {ref_id} {pid}"
                    f"  truth={t_val}  recovered={r_val}"
                )

            if save_detail and outcome != "TN":
                detail_rows.append({
                    "fixture": fixture_name,
                    "ref_id": ref_id,
                    "pid": pid,
                    "outcome": outcome,
                    "truth": truth.get(pid, ""),
                    "recovered": rec.get(pid, ""),
                })

    return {"tp": tp, "fp": fp, "fn": fn, "new": new}, detail_rows


def _metrics_from_counts(counts: dict[str, int]) -> dict[str, float]:
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) else 0.0
    )
    return {"precision": precision, "recall": recall, "f1": f1}


async def _eval_fixture(
    path: Path, verbose: bool, delay: float = 0.5,
    save_detail: bool = False,
) -> tuple[dict, list[dict]]:
    """Run evaluation for one fixture. Returns (result dict, detail rows)."""
    raw_xml = path.read_bytes()

    parser = etree.XMLParser(
        remove_blank_text=False, resolve_entities=False, load_dtd=False
    )
    tree = etree.parse(BytesIO(raw_xml), parser)
    ground_truth = _extract_ground_truth(tree)

    if not ground_truth:
        print(f"  [SKIP] {path.name} — no refs with PIDs found")
        return (
            {
                "name": path.stem, "skipped": True,
                "tp": 0, "fp": 0, "fn": 0, "new": 0,
                "precision": 0.0, "recall": 0.0, "f1": 0.0,
            },
            [],
        )

    stripped_xml = _strip_pub_ids(raw_xml)
    enriched_xml = await enrich_jats(stripped_xml)
    await asyncio.sleep(delay)
    recovered = _extract_recovered(enriched_xml)

    print(f"\n{path.name}  ({len(ground_truth)} refs with ground-truth PIDs)")
    counts, detail_rows = _compute_metrics(
        ground_truth, recovered, verbose, path.name, save_detail
    )
    m = _metrics_from_counts(counts)

    tp, fp, fn, new = counts["tp"], counts["fp"], counts["fn"], counts["new"]
    print(
        f"  precision={m['precision']:.3f}  recall={m['recall']:.3f}"
        f"  F1={m['f1']:.3f}  (TP={tp} FP={fp} FN={fn} NEW={new})"
    )

    return (
        {"name": path.stem, "skipped": False,
         "tp": tp, "fp": fp, "fn": fn, "new": new, **m},
        detail_rows,
    )


def _write_results(
    fixture_results: list[dict],
    overall: dict,
    run_at: str,
    detail_rows: list[dict] | None = None,
) -> None:
    """Write latest.json, latest.png, README.md, and optionally
    latest_detail.json."""
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # JSON
    payload = {
        "run_at": run_at,
        "overall": overall,
        "fixtures": fixture_results,
    }
    (_RESULTS_DIR / "latest.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    # Detail JSON (only when --save-detail was passed)
    if detail_rows is not None:
        (_RESULTS_DIR / "latest_detail.json").write_text(
            json.dumps(detail_rows, indent=2), encoding="utf-8"
        )

    # Chart
    chart_path = _RESULTS_DIR / "latest.png"
    _write_chart(fixture_results, overall, chart_path)

    # README
    _write_readme(fixture_results, overall, run_at)

    print(f"\nResults written to {_RESULTS_DIR.relative_to(Path.cwd())}/")


def _write_chart(
    fixture_results: list[dict],
    overall: dict,
    path: Path,
) -> None:
    rows = [r for r in fixture_results if not r.get("skipped")]
    f1_scores = [r["f1"] for r in rows]

    fig, (ax_summary, ax_hist) = plt.subplots(
        1, 2, figsize=(12, 5),
        gridspec_kw={"width_ratios": [1, 2]},
    )

    # Left panel: overall precision / recall / F1
    metrics = ["Precision", "Recall", "F1"]
    values = [overall["precision"], overall["recall"], overall["f1"]]
    colors = ["#4c72b0", "#55a868", "#c44e52"]
    bars = ax_summary.bar(metrics, values, color=colors, width=0.5)
    for bar, val in zip(bars, values):
        ax_summary.text(
            bar.get_x() + bar.get_width() / 2,
            val + 0.02,
            f"{val:.3f}",
            ha="center", va="bottom", fontsize=11, fontweight="bold",
        )
    ax_summary.set_ylim(0, 1.15)
    ax_summary.set_ylabel("Score")
    ax_summary.set_title(
        f"Overall ({overall['tp']} TP / {overall['fp']} FP"
        f" / {overall['fn']} FN / {overall['new']} NEW)"
    )

    # Right panel: histogram of per-fixture F1 scores.
    # Use fine bins (0.02-wide)
    lo = max(0.0, min(f1_scores) - 0.02) if f1_scores else 0.0
    bins = [lo + i * 0.02 for i in range(int((1.0 - lo) / 0.02) + 2)]
    counts, edges, patches = ax_hist.hist(
        f1_scores, bins=bins, color="#c44e52", edgecolor="white",
        linewidth=0.6,
    )
    for count, patch, left, right in zip(
        counts, patches, edges[:-1], edges[1:]
    ):
        if count > 0:
            cx = patch.get_x() + patch.get_width() / 2
            ax_hist.text(
                cx, count + 0.3, str(int(count)),
                ha="center", va="bottom", fontsize=8,
            )
            ax_hist.text(
                cx, -max(counts) * 0.07,
                f"{left:.2f}–{right:.2f}",
                ha="center", va="top", fontsize=7, rotation=45,
            )
    ax_hist.set_xlabel("F1 score", labelpad=30)
    ax_hist.set_ylabel("Number of fixtures")
    ax_hist.set_title(
        f"Per-fixture F1 distribution ({len(rows)} fixtures)"
    )
    ax_hist.set_xticks([])
    ax_hist.set_xlim(lo, 1.0 + 0.02)

    fig.suptitle("jats-ref-refinery — eval scores", fontsize=13,
                 fontweight="bold", y=1.01)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _write_readme(
    fixture_results: list[dict],
    overall: dict,
    run_at: str,
) -> None:
    rows = [r for r in fixture_results if not r.get("skipped")]

    table_rows = "\n".join(
        f"| {r['name']} | {r['precision']:.3f} | {r['recall']:.3f}"
        f" | {r['f1']:.3f} | {r['tp']} | {r['fp']} | {r['fn']}"
        f" | {r['new']} |"
        for r in rows
    )
    overall_row = (
        f"| **OVERALL** | **{overall['precision']:.3f}**"
        f" | **{overall['recall']:.3f}** | **{overall['f1']:.3f}**"
        f" | **{overall['tp']}** | **{overall['fp']}**"
        f" | **{overall['fn']}** | **{overall['new']}** |"
    )

    readme = f"""\
# jats-ref-refinery — eval results

Last run: {run_at}

![Eval scores](results/latest.png)

## Per-fixture scores

| Fixture | Precision | Recall | F1 | TP | FP | FN | NEW |
|---------|----------:|-------:|---:|---:|---:|---:|----:|
{table_rows}
{overall_row}

> **NEW** = PID found by the service but absent from ground truth — \
excluded from scoring.
> Run `uv run python eval/run_eval.py --verbose` for per-ref breakdown.

## Inspecting results

Per-ref outcomes are written to `results/latest_detail.json` after every run.
Each entry has `fixture`, `ref_id`, `pid`, `outcome`, `truth`, and `recovered`.

**Find all false positives:**
```bash
jq '[.[] | select(.outcome == "FP")]' eval/results/latest_detail.json
```

**Find all false negatives (missed PIDs):**
```bash
jq '[.[] | select(.outcome == "FN")]' eval/results/latest_detail.json
```

**Find all outcomes for a specific fixture:**
```bash
jq '[.[] | select(.fixture == "my-fixture")]' eval/results/latest_detail.json
```

**Count FPs by fixture:**
```bash
jq 'group_by(.fixture) | map({{fixture: .[0].fixture, \
fp: (map(select(.outcome == "FP")) | length)}})' \
eval/results/latest_detail.json
```

Pass `--no-detail` to skip writing this file.
"""
    _README.write_text(readme, encoding="utf-8")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print per-ref TP/FP/FN breakdown",
    )
    parser.add_argument(
        "--delay", "-d", type=float, default=0.5, metavar="SECS",
        help="Seconds to wait between fixtures (default: 0.5)",
    )
    parser.add_argument(
        "--no-detail", action="store_true",
        help="Skip writing per-ref outcomes to latest_detail.json",
    )
    parser.add_argument(
        "fixtures", nargs="*",
        help="JATS XML files to evaluate (default: eval/fixtures/*.xml)",
    )
    args = parser.parse_args()

    fixture_dir = Path(__file__).parent / "fixtures"
    paths = (
        [Path(f) for f in args.fixtures]
        if args.fixtures
        else sorted(fixture_dir.glob("*.xml"))
    )

    if not paths:
        print("No fixture files found. Add JATS XML files to eval/fixtures/.")
        sys.exit(1)

    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    fixture_results = []
    all_detail_rows: list[dict] = []
    totals = {"tp": 0, "fp": 0, "fn": 0, "new": 0}

    save_detail = not args.no_detail
    for path in paths:
        result, detail_rows = await _eval_fixture(
            path, args.verbose, args.delay, save_detail
        )
        fixture_results.append(result)
        all_detail_rows.extend(detail_rows)
        for k in totals:
            totals[k] += result[k]

    overall_metrics = _metrics_from_counts(totals)
    overall = {**totals, **overall_metrics}

    tp, fp, fn, new = totals["tp"], totals["fp"], totals["fn"], totals["new"]
    print(
        f"\n{'=' * 60}"
        f"\nOVERALL  precision={overall_metrics['precision']:.3f}"
        f"  recall={overall_metrics['recall']:.3f}"
        f"  F1={overall_metrics['f1']:.3f}"
        f"  (TP={tp} FP={fp} FN={fn} NEW={new})"
    )

    _write_results(
        fixture_results, overall, run_at,
        detail_rows=all_detail_rows if save_detail else None,
    )


if __name__ == "__main__":
    asyncio.run(main())
