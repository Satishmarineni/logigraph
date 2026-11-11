"""Step 1b: Look inside each PDF page: text, images, form fields."""
from pathlib import Path

import pymupdf

RAW_DIR = Path("data/raw")
OUT_DIR = Path("data/processed/inspect")


def inspect_pdf(path):
    print("\n" + "=" * 70)
    print(path.name)
    print("=" * 70)

    doc = pymupdf.open(path)

    for page_no, page in enumerate(doc, start=1):
        text = page.get_text().strip()
        images = page.get_images(full=True)
        widgets = list(page.widgets())

        print(f"\n--- Page {page_no} ---")
        print(f"Text characters : {len(text)}")
        print(f"Images          : {len(images)}")
        print(f"Form fields     : {len(widgets)}")

        filled = [w for w in widgets if w.field_value not in (None, "", "Off")]
        print(f"Filled fields   : {len(filled)}")

        for w in filled[:25]:  # show the first 25 filled fields
            print(f"   {w.field_name!r:45} = {w.field_value!r}")

        # Save full text and a picture of the page for checking
        stem = f"{path.stem}_page{page_no}"
        (OUT_DIR / f"{stem}.txt").write_text(text, encoding="utf-8")
        page.get_pixmap(dpi=120).save(OUT_DIR / f"{stem}.png")

    doc.close()


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for pdf in sorted(RAW_DIR.glob("*.pdf")):
        inspect_pdf(pdf)

    print(f"\nPage text and images saved in: {OUT_DIR.resolve()}")


if __name__ == "__main__":
    main()