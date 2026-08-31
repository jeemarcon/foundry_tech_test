# Data Quality Log

Log of data quality issues identified during pipeline development, their root cause, and the solution applied.

## DQ-001: Missing description translations for some books

**Identified in:** running `make translate-descriptions`

**Symptom:** Not all books had their descriptions translated into EN/ES/FR: some records ended up with null translation fields in `description_translations.json`, with no signal that a failure had occurred. The same null, also with no signal, appeared in `localized_catalog.json`, the final output required by the case.

**Root cause:** The script `05_translate_descriptions.py` fires 3 LLM calls per book (one per language) in sequence, with no pause between them. With 10 books, this exceeds the Gemini API free tier's limit of 15 requests/minute (error 429). The script does not handle this error, discards the attempt, and moves on, leaving that language's field null both in the intermediate file and, with no handling at all, in the final output.

**Observed evidence:**
```
LLM error: Error code: 429 - You exceeded your current quota [...]
limit: 15, model: gemini-3.5-flash-lite, quotaId: GenerateRequestsPerMinutePerProjectPerModel-FreeTier
Please retry in 49.484914876s.
```

Example of the null in the intermediate file (code `19322`, `description_translations.json`):
```json
"es": {"text": null, "status": "Failed", "reason": "LLM_error"},
"fr": {"text": null, "status": "Failed", "reason": "LLM_error"}
```

**Target areas affected:** Data Quality (incomplete data with no signal) and Scalability (missing rate limiting/backpressure).

**Solution (Data Quality, traceability):** explicit status/reason signaling per record in `03_describe.py`, and per-language status/reason plus an aggregated `translation_complete` in `05_translate_descriptions.py`, instead of silent failure. *(done)*

**Solution (Data Quality, traceability columns leaking into the final output):** reviewing `localized_catalog.json` after it was ready, it was noticed that the traceability columns created above (`status`/`reason`) were being mirrored into the final output, instead of staying restricted to the intermediate file. `07_localized_catalog.py` copied `title`/`description` directly from the intermediate JSON without extracting the text value from inside the status/reason object. PT (the original field, with no translation) stayed a plain string, while EN/ES/FR turned into nested objects in the final output, breaking the schema's type consistency:
```json
"description": {
  "pt": "[...]",
  "en": {"text": "[...]", "status": "Success", "reason": null},
  "es": {"text": null, "status": "Failed", "reason": "LLM_error"},
  "fr": {"text": null, "status": "Failed", "reason": "LLM_error"}
}
```
The logic is the same as DQ-004: the missing value (`null`) is acceptable and correct in the final deliverable when the translation genuinely failed. The *why* behind the absence is what should be recorded only at the intermediate layer, not the traceability structure itself. Fixed with an `extract_text()` helper in `07_localized_catalog.py`, applied to extracting `title` and `description` in EN/ES/FR: it returns `value["text"]` when the field is a status/reason object, or the value itself when it is already a string/null. `localized_catalog.json` goes back to having only text values or `null` in each language, consistent with PT, without carrying the traceability structure through to the deliverable. *(done)* Confirmed via manual re-run: code `19322` now shows `"es": null, "fr": null` in the final output, instead of the nested objects.

**Solution (Scalability)** (`feature/scalability`): pause between LLM calls (`LLM_CALL_DELAY_SECONDS`, 4.5s) in `03_describe.py`/`04_translate.py`/`05_translate_descriptions.py`, to respect the requests-per-minute limit. *(done)* Confirmed via re-running `make translate`: code `18957`, which previously had `es` failing with a 503 error, now shows `es`/`fr` as `"status": "Success"`, and all 10 books completed with no LLM errors.

Worth a precision on the cause, to avoid conflating the two problems: the pause eliminates error 429 (quota exceeded, the original root cause documented above), but does not eliminate error 503 (the provider's own momentary unavailability), which is a different failure, outside the client's control. This was confirmed in a later re-run with 20 books (a scale test for the Event-Driven target area): even with the pause active, one description call suffered an isolated 503 error. The pipeline correctly isolated that failure (the record was marked `Failed`, and only that specific book's description translation was skipped), and a new run completed processing with no manual intervention. That is why the solution here does not try to prevent 100% of LLM failures: it removes the cause that was under control (the quota) and relies on the isolate-and-reprocess model via status/reason (see ADR 003) to absorb the failures that are not, a more realistic posture for an external service than trying to shield against every momentary instability from the provider.

## DQ-002: The "most accessed" ranking is affected by the scraping itself (01_download.py)

**Identified in:** running `01_download.py` twice in a row, without deleting the `catalog.json` generated by the first run.

**Symptom:** No visible effect at first: the same 10 books came back, in the same order, on both runs. What drew attention was the `accesses` field: it went up by exactly +1, across all ten books, between the first and second run.

**Root cause:** `LIST_URL` asks the site for the 10 most accessed books (`colunaOrdenar=NU_PAGE_HITS&ordem=desc`), an ordering done by the Domínio Público server itself. When visiting each book's detail page to collect metadata, `01_download.py` counts as a visit on that page, incrementing the same field (`NU_PAGE_HITS`) used as the sort criterion for the next query. The scraping influences the very ranking that decides which books it will scrape next time.

**Observed evidence:** Two consecutive runs of `make download` returned the same 10 codes, in the same order, but with `accesses` incremented by +1 for each of the ten books on the second run.

**Target areas affected:** Data Quality (reproducibility of the scraped dataset is not guaranteed over time, even with no explicit randomness in the code).

**Solution:** Skip fetching the detail page (`get_download_url_and_metadata`) for codes that already exist in the `metadata.json` saved from a previous run, reusing the data already collected instead of visiting the page again. The listing (`LIST_URL`) is still fetched on every run, it does not affect the access counter, only visiting the detail page does. This fix has no effect on the first run, but prevents the undue increment on any subsequent run: manual reruns during development, tests, resuming after a partial failure mid-processing.

**Scope note:** this solution assumes the pipeline runs as a single snapshot, with no scheduled periodic execution, given the case's scope ("process at least 10 books", with no mention of recurrence). In a real periodic-execution scenario, the correct answer would not be to keep overwriting the catalog on every run (that would reintroduce both the orphan risk and the pollution of the site's access counter), it would be to fetch the listing on a defined cadence and merge new codes with the existing catalog, preserving already-known records (most likely an upsert).

## DQ-003: "size" field with an incorrect value at the source (01_download.py)

**Identified in:** manual review of `catalog.json` after scraping.

**Symptom:** The book with code `19322` ("Populações meridionais do Brasil") appears with `size: "0.00 KB"` in `catalog.json`, despite having been downloaded successfully (`downloaded: true`).

**Observed evidence:**

Entry in `catalog.json`:
```json
{
  "code": "19322",
  "title": "Populações meridionais do Brasil",
  "author": "Oliveira Viana",
  "source": "[sf] Senado Federal",
  "format": ".pdf",
  "size": "0.00\r\n              KB",
  "accesses": "9,469",
  "download_url": "https://dominiopublico.mec.gov.br/pesquisa/DetalheObraDownload.do?select_action=&co_obra=19322&co_midia=2",
  "downloaded": true
}
```

Actual size of the downloaded file, verified via PowerShell:
```
Get-Item "data\pdfs\19322.pdf" | Select-Object Name, Length

Name       Length
----       ------
19322.pdf 1356796
```

1,356,796 bytes (~1.3 MB), far from the "0.00 KB" reported by the site.

**Root cause:** The `size` field in `catalog.json` comes directly from the size column on the site's listing page (`parse_listing`, `cells[6]`), with no validation. It is a value displayed by Domínio Público itself, and is incorrect at the source for this specific record, it does not reflect the actual file.

**Target areas affected:** Data Quality (data present and validly formatted, but numerically incorrect, different from missing data). Also relevant because `universal_metadata.json`, the final output required by the case, includes "file size" as a required field: this error could leak into the final deliverable without this fix.

**Solution:** Not implemented, by a conscious decision. I traced the path to the final output (`universal_metadata.json`) and confirmed that the flawed field in `catalog.json` is not propagated: the final assembly stage recomputes `size_bytes` from `hashes.json` (or, in its absence, from the actual file size on disk), evidenced by `"size_bytes": 1356796` in the final output, matching exactly the actual measured file size. Since the blast radius is proven to be contained before reaching the required deliverable, I chose to document the finding without spending time fixing the `size` field in `catalog.json` directly. A conscious prioritization given the case's deadline, not an oversight.

## DQ-004: "year" field missing for non-thesis books (01_download.py)

**Identified in:** review of `universal_metadata.json`, noticing `year: null` across all 10 records.

**Symptom:** 100% of the books in `universal_metadata.json` have `year: null`.

**Root cause:** `parse_detail_page` looks for the "Ano da Tese" ("Year of the Thesis") label to populate the `year` field. That label appears in the detail page's template regardless of the work's type, but is only actually filled in for academic theses. Manually verified on the site (code 15713 and others): the label appears, but the value next to it is blank at the source itself, confirming this is not an extraction failure, it is a real absence of data at the source, for this type of collection ("History").

**Target areas affected:** Data Quality (evaluating whether missing data is a defect or a real characteristic of the source, before trying to "fix" it).

**Solution:** Kept as `null` in the final output (`universal_metadata.json`), a decision backed by best-practices research (see sources), which advises against filling a genuine absence with an artificial placeholder. However, a lightweight, scoped signal was added to the intermediate `metadata.json` (produced by `01_download.py`): when `year` is not extracted, `parse_detail_page` records `year_status` as `"empty_in_source"` (the "Ano da Tese" label was found, value blank at the source, the case confirmed here) or `"label_not_found"` (the label did not even appear, a sign of a possible real extraction problem). This signal is not propagated to the final output, serving only as internal diagnostics to avoid repeating the manual site investigation should the same pattern appear again.

**Sources:**
- https://sqlpad.io/tutorial/fill-missing-values-sql-coalesce-window-functions/
- https://dqops.com/common-data-quality-issues/

## DQ-005: PDF content not validated before being written to disk (01_download.py)

**Identified in:** code review, during a systematic sweep for Data Quality weak points across the pipeline (not an incident observed while running the pipeline, see note below).

**Symptom:** `download_pdf` only validates `status_code == 200` and `len(resp.content) > 1000` before writing `{code}.pdf` and marking `downloaded: True`. There is no check that the downloaded content is actually a PDF.

**Root cause:** the site has anti-bot protection, handled in `fetch_page` (checking for a "challenge" at the start of the HTML), but that same protection has no equivalent in `download_pdf`. If the download endpoint responds with an error page or captcha in HTML, with status 200 and more than 1000 bytes, that content passes both existing checks, gets written as `{code}.pdf`, and the record is marked as successfully downloaded.

**Note on the nature of the finding:** unlike DQ-001 through DQ-004, this is not a problem observed in an actual pipeline run. It was found through code review (with Claude Code's support), deliberately looking for validation gaps before closing out the Data Quality target area. Recorded here as a preventive finding, not an incident.

**Target areas affected:** Data Quality (a corrupted/invalid file indistinguishable from a healthy one in the rest of the pipeline).

**Propagation (if not fixed):** high. The invalid file would be hashed normally in `02_hash.py` (hashing an HTML, not the book), could produce a silent error or a low-quality description in `03_describe.py`/`06_covers.py` (`fitz` could fail to open or render garbage), and in `08_universal_metadata.py` it would have `document_hash` and `size_bytes` filled in normally, looking like a healthy record in the final output required by the case.

**Solution:** `download_pdf` now checks the file's signature (`resp.content[:5] == b"%PDF-"`, the first bytes of any valid PDF) before writing. If the signature does not match, the content is discarded (nothing is written to disk) and the function returns `False`, leaving `downloaded: False` in `catalog.json`. No new status/reason field was needed here: the retry mechanism in `main()` already decides whether to download again by checking `pdf_path.exists()`, so it was enough to make that check receive accurate information. Before, it was receiving a false positive. *(done)*

## DQ-006: Cover cache treats failure as final, with no status/reason (06_covers.py)

**Identified in:** code review, in the same systematic sweep as DQ-005 (a preventive finding, not an observed incident: checked against the real `covers.json`, all 10 books extracted their cover successfully).

**Symptom:** `06_covers.py` uses `if code in covers: continue` to decide whether it has already processed that book. When `extract_cover` fails, the code writes `covers[code] = None`, which is already a present key in the dictionary. In practice, a failure becomes permanent: no future run tries to extract that cover again, even if the cause was transient (or fixed, as in the DQ-005 case).

**Root cause:** the same pattern already fixed in `04_translate.py`, and originally present in `05_translate_descriptions.py` before DQ-001: using key presence as a synonym for success, instead of checking the result of the previous attempt. Here it is even poorer, since the failure does not even carry `status`/`reason`, just a context-free `None`.

**Target areas affected:** Data Quality (broken retry and lack of traceability on why a cover was not extracted).

**Propagation:** high and direct. `08_universal_metadata.py:48-49` does `cover.get("path") if cover else None`, with `cover` equal to `None`, both `cover_path` and `cover_hash` become `null` in `universal_metadata.json`, two fields required by the case, indistinguishable from "never properly attempted".

**Solution:** the skip condition now checks `(covers.get(code) or {}).get("status") == "Success"`, instead of just key presence. Both success and failure now write an object with `status` (`"Success"` or `"Failed"`, with `reason: "extraction_error"` in the latter case), in the same format already used in `03_describe.py`/`04_translate.py`/`05_translate_descriptions.py`. Old entries in the previous format (without `status`) are reprocessed once and migrate to the new format on their own. `08_universal_metadata.py` needed no changes: a failed `cover` is still a truthy dictionary with `path`/`hash` set to `None`, producing the same `null` as before in the final output. *(done)*

## DQ-007: "Success" defined only by a non-empty response, without validating content (03_describe.py)

**Identified in:** code review (a preventive finding: checked against the 10 real descriptions in `descriptions.json`, none trigger the check described below).

**Symptom:** `descriptions[code]["status"] = "Success"` was decided only by `if description:` (a non-empty string), with no check on whether the content makes sense. A refusal from the vision LLM (e.g. "I cannot analyze this image") or an abnormally short response both count as success.

**Root cause:** the existing validation is about form (the call returned something), not content. Since `05_translate_descriptions.py` only looks at the previous stage's `status == "Success"`, a bad description would be translated into EN/ES/FR normally, multiplying the low-quality data across 4 languages in `localized_catalog.json`, all marked as successful from start to finish.

**Target areas affected:** Data Quality (validating content, not just form, before propagating a piece of data as trustworthy).

**Conscious scope:** semantically validating whether a description is correct is not feasible within the case's deadline (that would, in practice, be a whole other classifier). The solution here is a cheap heuristic, not proof of quality: a minimum length of 40 characters and a short list of known refusal phrases in PT/EN. It reduces the risk, it does not eliminate it.

**Design decision:** the heuristic's finding becomes a new, separate field, `quality_flag` (`"too_short"` or `"possible_refusal"`), instead of downgrading `status` to `"Failed"`. `status` keeps meaning "the LLM call worked technically", `quality_flag` signals "the content might not be trustworthy", without mixing the two concepts. Consequence: a flagged description is still translated in `05` and still appears in `localized_catalog.json` normally, the signal stays only in `descriptions.json`, for manual review (feasible with 10 books).

**Solution:** added the `looks_suspicious()` function, applied to every successfully generated description. When it triggers, it records `quality_flag` alongside the record. None of the 10 real descriptions trigger the heuristic today. *(done)*

**Related finding, same file:** the resume check in `03_describe.py` (`if code in descriptions: continue`) had the same flaw as DQ-006/DQ-001 before their fix: it did not check `status`, so a `"Failed"` description (`render_failed` or `llm_error`) stayed frozen forever. Fixed to `descriptions.get(code, {}).get("status") == "Success"`, in the same pattern already used in `04`/`05`/`06`. Also fixed a side effect found while reading the code: when `render_failed` occurred, a `continue` skipped saving to disk for that iteration; if it was the last PDF in the loop, the failure was never persisted. Both fixes were unified into a single save point per iteration.

## DQ-008: "downloaded" field never checked by any downstream stage (01_download.py)

**Identified in:** a reflection triggered by a question about whether `04_translate.py` should validate the title before translating, the same way the check already existing for description in `05_translate_descriptions.py` works. Investigating that specific question showed the title cannot come malformed (it is filtered during the listing scrape itself, see Root cause below), but the following reflection revealed the real problem: it was not about the title in isolation, it was that no stage after `01_download.py` checked the `catalog` as a whole before processing a record (a preventive finding, not an observed incident: across the 10 and later 20 real books processed, every download succeeded).

**Symptom:** `01_download.py` writes `entry["downloaded"] = True/False` on each `catalog.json` record, but no downstream stage checks that field before processing the record. `04_translate.py`, `07_localized_catalog.py`, and `08_universal_metadata.py` all iterate over the whole `catalog` without checking `downloaded`.

**Root cause:** `02_hash.py`, `03_describe.py`, and `06_covers.py` discover a failed download indirectly, because they look for the physical file in `PDF_DIR` (no PDF on disk for that code, so they never process the record). But `04_translate.py` translates the title directly from `catalog.json`, with no dependency on a file on disk, so it would process a book that never downloaded as if nothing were wrong (the title itself is not at risk of coming malformed: `parse_listing` only adds an entry to the catalog when `code` and `title` are already filled in from the listing scrape, so that specific field is never the problem). And `07_localized_catalog.py`/`08_universal_metadata.py` also iterate over `catalog` without checking `downloaded`, assembling a complete record in the final output for a book with no content at all.

**Target areas affected:** Data Quality (an existing signal that goes unused, an incomplete record in the pipeline with no traceability).

**Propagation (if not fixed):** a book that failed to download would appear in `localized_catalog.json` and `universal_metadata.json` with most fields null (hash, cover, description) and yet with a translated title, indistinguishable from any other cause of null (an LLM error, a corrupted cover), with no clue that the problem started at the download stage.

**Solution:** `download_pdf()` in `01_download.py` now returns `(bool, reason)` instead of just `bool`, distinguishing `"http_error"`, `"response_too_small"`, `"html_response"`, `"invalid_pdf_signature"`, and `"request_exception"`. The `downloaded` field was replaced with `status`/`reason` on each `catalog.json` entry, in the same pattern already used in `03`/`04`/`05`/`06` (including the `"no_download_url"` case, when the detail page has no download link). `04_translate.py` now checks `entry.get("status") == "Success"` before translating the title; when it fails, it writes `status: "Skipped"`, `reason: "upstream_download_failed"`, in the same pattern already used in `05_translate_descriptions.py` for description. *(done)*

**Design decision (not propagating status/reason to the final files):** adding `status`/`reason` to `localized_catalog.json`/`universal_metadata.json` as well was evaluated, but the idea was discarded. A single status per record in these two files would need to aggregate the result of download, hash, describe, translate (title and description, in 3 languages), and covers at once to be honest, and would still get it wrong in legitimate cases: `year` being null is expected and correct (DQ-004), so an aggregate would have to treat that field as an exception; and a book that downloaded normally but had one description translation fail would still show up as a success if the status only looked at download. Either way, a single field would lie by omission. Best practice in data quality is to measure each dimension (completeness, validity) separately, not collapse them into a single per-record status; the project itself already follows that principle with `year_status`, `quality_flag`, and `translation_complete`, all scoped to the dimension they describe. The nulls in the final output, combined with which specific field is null, already form a diagnosable signature: a book that never downloaded shows `title.pt` filled in (it comes from the listing scrape, not the PDF) but everything else null; a book with only one description translation failing shows only that language as null. Full traceability (the why) stays in the intermediate files (`catalog.json`, `translations.json`), as with every other finding in this log.

**Sources:**
- https://www.getdbt.com/blog/data-quality-dimensions
- https://montecarlo.ai/blog-6-data-quality-dimensions-examples
