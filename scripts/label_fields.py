"""Step 2a-2: Write each field's name into its box so we can see which is which."""
from pathlib import Path

import pymupdf

RAW_DIR = Path("data/raw")
OUT_DIR = Path("data/processed/inspect")
RED = (1, 0, 0)


def label_pdf(pdf_path):
    doc = pymupdf.open(pdf_path)  # works on a copy in memory; the raw file is not changed

    for page in doc:
        for w in page.widgets():
            if w.field_type_string == "Text":
                w.text_fontsize = 6
                w.text_color = RED
                w.field_value = w.field_name
                w.update()
            else:
                # Checkboxes and signatures: print the name just to the right of the box
                page.insert_text(
                    (w.rect.x1 + 1, w.rect.y1),
                    w.field_name,
                    fontsize=4,
                    color=RED,
                )

    pdf_out = OUT_DIR / f"{pdf_path.stem}_labeled.pdf"
    doc.save(pdf_out)

    # Save page 1 as a large image so small labels stay readable
    png_out = OUT_DIR / f"{pdf_path.stem}_labeled.png"
    doc[0].get_pixmap(dpi=200).save(png_out)
    doc.close()

    print(f"{pdf_path.name} -> {png_out}")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for pdf in sorted(RAW_DIR.glob("*.pdf")):
        label_pdf(pdf)


if __name__ == "__main__":
    main()