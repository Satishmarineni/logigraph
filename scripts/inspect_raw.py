"""Step 1: Look at the raw files before building anything."""
from pathlib import Path

import fitz  # PyMuPDF: reads PDFs and their text layer

RAW_DIR = Path("data/raw")
MIN_CHARS = 50  # a page with fewer characters than this is treated as scanned


def inspect_pdf(path):
    doc = fitz.open(path)
    text_pages = 0

    for page in doc:
        if len(page.get_text().strip()) >= MIN_CHARS:
            text_pages += 1

    preview = doc[0].get_text().strip()[:300] if len(doc) else ""

    print(f"  Pages         : {len(doc)}")
    print(f"  Text pages    : {text_pages}")
    print(f"  Scanned pages : {len(doc) - text_pages}")
    print(f"  Page 1 preview: {preview!r}")
    doc.close()


def main():
    if not RAW_DIR.exists():
        print(f"Folder not found: {RAW_DIR.resolve()}")
        return

    files = sorted(p for p in RAW_DIR.rglob("*") if p.is_file())
    if not files:
        print("data/raw is empty.")
        return

    for f in files:
        size_kb = f.stat().st_size / 1024
        print(f"\n{f.name}  ({f.suffix.lower()}, {size_kb:.1f} KB)")
        if f.suffix.lower() == ".pdf":
            inspect_pdf(f)

    print(f"\nTotal files: {len(files)}")


if __name__ == "__main__":
    main()