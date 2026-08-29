import json

from data_foundry.config import OUTPUT_DIR


def load_json(name: str) -> dict | list:
    path = OUTPUT_DIR / name
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {} if name != "catalog.json" else []

def extract_text(value):
    if isinstance(value, dict):
        return value.get("text")
    return value


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    catalog = load_json("catalog.json")
    if not catalog:
        print("catalog.json not found. Run 01_download.py first.")
        return

    translations = load_json("translations.json")
    descriptions = load_json("descriptions.json")
    desc_translations = load_json("description_translations.json")

    localized = []
    for entry in catalog:
        code = entry["code"]

        title_trans = translations.get(code, {})
        desc_data = descriptions.get(code, {})
        desc_trans = desc_translations.get(code, {})

        record = {
            "id": code,
            "title": {
                "pt": entry["title"],
                "en": extract_text(title_trans.get("en")),
                "es": extract_text(title_trans.get("es")),
                "fr": extract_text(title_trans.get("fr")),
            },
            "description": {
                "pt": desc_data.get("description"),
                "en": extract_text(desc_trans.get("en")),
                "es": extract_text(desc_trans.get("es")),
                "fr": extract_text(desc_trans.get("fr")),
            },
            "author": entry.get("author"),
            "source": entry.get("source"),
        }
        localized.append(record)

    output_path = OUTPUT_DIR / "localized_catalog.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(localized, f, ensure_ascii=False, indent=2)

    complete = sum(
        1 for r in localized if r["title"].get("en") and r["description"].get("pt")
    )
    print(f"Done. {len(localized)} entries assembled ({complete} fully localized).")
    print(f"Output saved to {output_path}")


if __name__ == "__main__":
    main()
