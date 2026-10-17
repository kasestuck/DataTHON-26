"""Connect existing human observations to the Back 2 visual catalog."""
import argparse
import json
from pathlib import Path

from backend.analyzer.annotations import apply_groups, confirm_groups, load_proposals
from backend.analyzer.catalog import validate_catalog
from backend.analyzer.labels import write_catalog, write_labels
from backend.scripts.prepare_annotations import snapshot_workbook


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--excel", default="dataset/open_labels.xlsx")
    parser.add_argument("--videos-dir", default="dataset/videos")
    parser.add_argument("--proposals", default="backend/data/annotations/grouping_proposals.json")
    parser.add_argument("--labels", default="backend/data/labels.csv")
    parser.add_argument("--catalog", default="backend/data/violation_types.json")
    parser.add_argument("--description-column", default="D")
    parser.add_argument("--confirmations", help="Explicit human review JSON: source_sha256 and events[{source_row,type_id}]")
    parser.add_argument("--report", default="backend/data/reports/annotation_import.json")
    args = parser.parse_args()
    snapshot_workbook(args.excel, Path(args.proposals).parent)
    proposals = load_proposals(args.proposals)
    labels, catalog, report = apply_groups(args.excel, args.videos_dir, proposals,
                                          description_column=args.description_column)
    # Human review may correct a suggested ID or add an approved visual category.
    catalog = validate_catalog(proposals["catalog"])
    pending_catalog = Path(args.proposals).parent / "catalog_pending_review.json"
    write_catalog(catalog, pending_catalog)
    report["proposed_examples_per_type"] = report["examples_per_type"]
    confirmation = load_proposals(args.confirmations) if args.confirmations else None
    labels, catalog = confirm_groups(labels, catalog, confirmation)
    from collections import Counter
    report["examples_per_type"] = dict(Counter(row["violation_type"] for row in labels
                                               if row["review_status"] == "confirmed"))
    report["explicit_human_type_approval"] = bool(report["examples_per_type"])
    report["needs_review"] = [{"video": row["video"], "timecode_sec": row["timecode_sec"],
        "source_row": row["source_row"], "reasons": row["notes"].split(";")}
        for row in labels if row["review_status"] == "needs_review"]
    # Keep prior manual labels as a private immutable content-addressed snapshot.
    import hashlib
    previous = Path(args.labels)
    if previous.exists():
        data = previous.read_bytes()
        saved = Path(args.proposals).parent / ("labels_before_" + hashlib.sha256(data).hexdigest() + ".csv")
        if not saved.exists():
            saved.write_bytes(data)
    write_labels(labels, args.labels)
    write_labels([row for row in labels if row["review_status"] == "needs_review"],
                 Path(args.labels).with_name("needs_review.csv"))
    write_catalog(catalog, args.catalog)
    output = Path(args.report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
