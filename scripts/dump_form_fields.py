"""Step 2a: List every fillable form field in the raw PDFs."""
import json
from pathlib import Path

import pymupdf

RAW_DIR = Path("data/raw")
OUT_DIR = Path("data/processed/form_fields")


def dump_fields(pdf_path):
    doc = pymupdf.open(pdf_path)
    fields = []

    for page_no, page in enumerate(doc, start=1):
        for w in page.widgets():
            r = w.rect
            fields.append({
                "page": page_no,
                "name": w.field_name,
                "type": w.field_type_string,       # Text, CheckBox, ComboBox, ...
                "label": w.field_label,            # tooltip text, if the form has one
                "value": w.field_value,
                "choices": w.choice_values,        # options for dropdown fields
                "rect": [round(r.x0), round(r.y0), round(r.x1), round(r.y1)],
            })

    doc.close()

    # Sort top-to-bottom, then left-to-right, so the list follows the page layout
    fields.sort(key=lambda f: (f["page"], f["rect"][1], f["rect"][0]))
    return fields


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for pdf in sorted(RAW_DIR.glob("*.pdf")):
        fields = dump_fields(pdf)
        if not fields:
            continue

        out_file = OUT_DIR / f"{pdf.stem}.json"
        out_file.write_text(json.dumps(fields, indent=2), encoding="utf-8")

        types = {}
        for f in fields:
            types[f["type"]] = types.get(f["type"], 0) + 1

        print(f"\n{pdf.name}: {len(fields)} fields -> {out_file}")
        print(f"  Field types: {types}")
        print("  First 15 fields (top of page first):")
        for f in fields[:15]:
            print(f"    {f['name']!r:40} {f['type']:10} rect={f['rect']} label={f['label']!r}")


if __name__ == "__main__":
    main()