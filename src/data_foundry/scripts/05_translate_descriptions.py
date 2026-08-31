import json
import time

from openai import OpenAI

from data_foundry.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_CALL_DELAY_SECONDS,
    LLM_MODEL,
    OUTPUT_DIR,
)

TARGET_LANGUAGES = {"en": "English", "es": "Spanish", "fr": "French"}

client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)


def translate_text(text: str, target_lang: str) -> str | None:
    prompt = (
        f"Translate the following Portuguese text to {target_lang}. "
        f"Return ONLY the translated text, nothing else.\n\n"
        f"{text}"
    )

    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            timeout=60,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:  # noqa: BLE001 (boundary catch: logged, converted to an explicit Failed status/reason for this record instead of crashing the batch)
        print(f"  LLM error: {e}")
    finally:
        time.sleep(LLM_CALL_DELAY_SECONDS)
    return None


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    desc_path = OUTPUT_DIR / "descriptions.json"
    if not desc_path.exists():
        print("descriptions.json not found. Run 03_describe.py first.")
        return

    with open(desc_path, encoding="utf-8") as f:
        descriptions = json.load(f)

    trans_path = OUTPUT_DIR / "description_translations.json"
    if trans_path.exists():
        with open(trans_path, encoding="utf-8") as f:
            translations = json.load(f)
    else:
        translations = {}

    print(
        f"Translating {len(descriptions)} descriptions to {', '.join(TARGET_LANGUAGES.values())}..."
    )
    print(f"Using model: {LLM_MODEL} via {LLM_BASE_URL}")

    for i, (code, entry) in enumerate(descriptions.items()):
        title = entry.get("title", code)

        if translations.get(code, {}).get("translation_complete"):
            print(
                f"[{i + 1}/{len(descriptions)}] {code} — already translated, skipping"
            )
            continue

        if entry.get("status") != "Success":
            print(
                f"[{i + 1}/{len(descriptions)}] {title[:50]}... — skipped (description failed)"
            )
            translations[code] = {
                "status": "Skipped",
                "reason": "upstream_description_failed",
                "translation_complete": False,
            }
            with open(trans_path, "w", encoding="utf-8") as f:
                json.dump(translations, f, ensure_ascii=False, indent=2)
            continue

        description = entry["description"]
        print(f"[{i + 1}/{len(descriptions)}] {title[:50]}...")

        entry_translations = {"original": description}
        for lang_key, lang_name in TARGET_LANGUAGES.items():
            translated = translate_text(description, lang_name)
            if translated:
                entry_translations[lang_key] = {
                    "text": translated,
                    "status": "Success",
                }
                print(f"  {lang_key}: {translated[:60]}")
            else:
                entry_translations[lang_key] = {
                    "text": None,
                    "status": "Failed",
                    "reason": "LLM_error",
                }
                print(f"  {lang_key}: failed")

        entry_translations["translation_complete"] = all(
            entry_translations[lang]["status"] == "Success" for lang in TARGET_LANGUAGES
        )

        translations[code] = entry_translations

        with open(trans_path, "w", encoding="utf-8") as f:
            json.dump(translations, f, ensure_ascii=False, indent=2)

    complete = sum(1 for t in translations.values() if t.get("translation_complete"))
    print(f"\nDone. {complete}/{len(translations)} descriptions fully translated.")
    print(f"Output saved to {trans_path}")


if __name__ == "__main__":
    main()
