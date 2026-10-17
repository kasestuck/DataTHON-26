import argparse
import json
from pathlib import Path

from backend.analyzer.labels import import_open_labels, merge_manual, read_labels, write_catalog, write_labels


def main():
    parser = argparse.ArgumentParser(description="Import local Excel verdicts or manual classifications")
    parser.add_argument("--excel", default="dataset/open_labels.xlsx")
    parser.add_argument("--videos-dir", default="dataset/videos")
    parser.add_argument("--output", default="backend/data/labels.csv")
    parser.add_argument("--catalog", default="backend/data/violation_types.json")
    parser.add_argument("--manual", help="CSV/XLSX manual classification; updates existing labels")
    parser.add_argument("--sheet")
    parser.add_argument("--columns", type=json.loads, help='JSON mapping of canonical keys to actual column names')
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()
    if args.manual:
        catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
        labels, catalog = merge_manual(read_labels(args.output), args.manual, catalog)
        write_catalog(catalog, args.catalog)
        report = {"rows": len(labels), "confirmed_types": len(catalog)}
    else:
        labels, report = import_open_labels(args.excel, args.videos_dir, args.columns, args.sheet, args.allow_missing)
        if Path(args.output).exists():
            parser.error("Output exists; choose a new --output to avoid overwriting manual classifications")
    write_labels(labels, args.output)
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
