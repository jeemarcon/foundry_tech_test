import base64
import json
import time
from pathlib import Path

import fitz
from openai import OpenAI

from data_foundry.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_CALL_DELAY_SECONDS,
    LLM_MODEL,
    OUTPUT_DIR,
    PDF_DIR,
)

MAX_PAGES = 1

client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)


def pdf_pages_to_base64(pdf_path: Path, max_pages: int = MAX_PAGES) -> list[str]:
    images = []
    try:
        doc = fitz.open(str(pdf_path))
        for i in range(min(max_pages, len(doc))):
            page = doc[i]
            pix = page.get_pixmap(dpi=150)
            png_bytes = pix.tobytes("png")
            b64 = base64.b64encode(png_bytes).decode("utf-8")
            images.append(b64)
        doc.close()
    except Exception as e:  # noqa: BLE001 (boundary catch: logged, returns an empty page list for this record instead of crashing the batch)
        print(f"  Error rendering PDF: {e}")
    return images


def format_metadata(meta: dict) -> str:
    parts = []
    for key, value in meta.items():
        if value and key not in ("code", "download_url"):
            parts.append(f"- {key}: {value}")
    return "\n".join(parts)


MIN_DESCRIPTION_LENGTH = 40
REFUSAL_PATTERNS = [
    "não consigo",
    "não posso analisar",
    "não é possível analisar",
    "i cannot",
    "i'm unable",
    "i am unable",
    "as an ai",
]


def looks_suspicious(text: str) -> str | None:
    if len(text.strip()) < MIN_DESCRIPTION_LENGTH:
        return "too_short"
    lowered = text.lower()
    for pattern in REFUSAL_PATTERNS:
        if pattern in lowered:
            return "possible_refusal"
    return None


def describe_document(
    images: list[str], title: str, metadata: dict | None = None
) -> str | None:
    meta_ctx = ""
    if metadata:
        meta_ctx = f"\n\nDocument metadata:\n{format_metadata(metadata)}\n"

    content = [
        {
            "type": "text",
            "text": (
                f"This document is titled '{title}'.{meta_ctx}\n"
                "Based on these pages and metadata, provide a concise description (2-3 sentences) "
                "of what this document is about. Include the main topic, methodology "
                "if visible, and key findings if apparent. Respond in Portuguese."
            ),
        },
    ]
    for b64 in images:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            }
        )

    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": content}],
            timeout=120,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:  # noqa: BLE001 (boundary catch: logged, converted to an explicit Failed status/reason for this record instead of crashing the batch)
        print(f"  LLM error: {e}")
    finally:
        time.sleep(LLM_CALL_DELAY_SECONDS)
    return None


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    catalog_path = OUTPUT_DIR / "catalog.json"
    if catalog_path.exists():
        with open(catalog_path, encoding="utf-8") as f:
            catalog = {e["code"]: e for e in json.load(f)}
    else:
        catalog = {}
        print("Warning: catalog.json not found. Titles will be 'Unknown'.")

    metadata_path = OUTPUT_DIR / "metadata.json"
    if metadata_path.exists():
        with open(metadata_path, encoding="utf-8") as f:
            metadata = json.load(f)
    else:
        metadata = {}
        print(
            "Warning: metadata.json not found. Descriptions will lack metadata context."
        )

    pdf_files = sorted(PDF_DIR.glob("*.pdf"))
    if not pdf_files:
        print("No PDFs found in data/pdfs/. Run 01_download.py first.")
        return

    desc_path = OUTPUT_DIR / "descriptions.json"
    if desc_path.exists():
        with open(desc_path, encoding="utf-8") as f:
            descriptions = json.load(f)
    else:
        descriptions = {}

    print(f"Generating descriptions for {len(pdf_files)} PDFs...")
    print(f"Using model: {LLM_MODEL} via {LLM_BASE_URL}")

    for i, pdf in enumerate(pdf_files):
        code = pdf.stem
        if descriptions.get(code, {}).get("status") == "Success":
            print(f"[{i + 1}/{len(pdf_files)}] {code}, already described, skipping")
            continue

        title = catalog.get(code, {}).get("title", "Unknown")
        print(f"[{i + 1}/{len(pdf_files)}] {title[:60]}...")

        images = pdf_pages_to_base64(pdf)
        if not images:
            print("  Could not render pages")
            descriptions[code] = {
                "title": title,
                "description": None,
                "status": "Failed",
                "reason": "render_failed",
            }
        else:
            meta = metadata.get(code)
            description = describe_document(images, title, meta)

            if description:
                descriptions[code] = {
                    "title": title,
                    "description": description,
                    "status": "Success",
                }
                flag = looks_suspicious(description)
                if flag:
                    descriptions[code]["quality_flag"] = flag
                    print(f"  Warning: description may be low quality ({flag})")
                print(f"  → {description[:100]}...")
            else:
                descriptions[code] = {
                    "title": title,
                    "description": description,
                    "status": "Failed",
                    "reason": "llm_error",
                }
                print("  → No description generated")

        with open(desc_path, "w", encoding="utf-8") as f:
            json.dump(descriptions, f, ensure_ascii=False, indent=2)

    described = sum(1 for d in descriptions.values() if d.get("status") == "Success")
    print(f"\nDone. {described}/{len(descriptions)} documents described.")
    print(f"Output saved to {desc_path}")


if __name__ == "__main__":
    main()
