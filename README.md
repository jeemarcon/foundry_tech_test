# Data Foundry Challenge

Pipeline that scrapes book metadata and PDFs from the [Domínio Público](https://dominiopublico.mec.gov.br/) catalog, extracts and translates descriptions with a vision LLM, and assembles two localized/normalized output datasets.

## Target Areas

This delivery focuses on two of the five target areas defined by the case:

- **Data Quality**: traceability (status/reason per record, per stage), validation before propagating data downstream, and explicit decisions on how to handle missing or malformed source data. See [`docs/en/data-quality-log.md`](docs/en/data-quality-log.md).
- **Event-Driven Pipeline**: stages dispatch based on the real dependency graph between them instead of a fixed sequential order, allowing independent stages to run in parallel and isolating failures to only the stages that actually depend on them.

The rationale for this choice, and the target areas considered and set aside, is in [`docs/en/ADR.md`](docs/en/ADR.md) (ADR 001). Both documents are also available in Portuguese, the language they were originally written in, under [`docs/pt/`](docs/pt/).

## Requirements

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (for `make run`), **or**
- Python 3.12 and [`uv`](https://docs.astral.sh/uv/) (for `make run-all` and individual stages)
- A Gemini API key ([Google AI Studio](https://aistudio.google.com/apikey)), free tier is enough for the default volume of this pipeline

## Setup

Copy the environment file and fill in an API key:

```bash
cp .env.example .env
```

By default `.env.example` points to the Gemini API, which is the LLM provider used in this delivery (see ADR 002 for why). Ollama and OpenAI examples are also included as commented-out alternatives: none of the LLM-calling scripts hardcode a provider, `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` fully control it, so switching back to Ollama needs no code change, only these three variables pointed at your own Ollama instance (the `ollama` service was removed from `compose.yaml` in ADR 002, so it needs to be run separately, not via this project's `docker compose`).

## How to Run

Full pipeline via Docker:

```bash
make run
```

Full pipeline locally, without Docker:

```bash
make setup    # installs dependencies with uv
make run-all
```

Both commands run the same entry point (`src/data_foundry/main.py`) and produce the same output files under `data/output/`. `make run` was validated end-to-end for this delivery; `make run-all` was the primary path used during development, since Docker Desktop was not available on the development machine for most of it (see ADR 002).

Individual stages are also available, in case one needs to be inspected or re-run in isolation:

```bash
make download                # scrape catalog and download PDFs
make hash                    # calculate document hashes
make describe                 # generate descriptions via vision LLM
make translate                # translate titles
make translate-descriptions   # translate descriptions
make covers                   # extract cover pages
make localized-catalog        # assemble localized_catalog.json
make universal-metadata       # assemble universal_metadata.json
```

Tests and linting:

```bash
make test
make lint
```

## Configuration

The number of books processed is controlled by `MIN_BOOKS` in `src/data_foundry/config.py`, currently set to 10 (the minimum required by the case). Since `data/` is not versioned (it is generated at runtime and listed in `.gitignore`), a fresh clone always produces exactly `MIN_BOOKS` entries on its first run.

## Architecture

The pipeline is composed of 8 independent scripts under `src/data_foundry/scripts/`, orchestrated by `src/data_foundry/main.py`. Instead of running them in a fixed order, `main.py` declares the real dependency graph between stages and dispatches each one as soon as everything it depends on has finished successfully:

```
01_download
├── 02_hash
├── 03_describe
│   └── 05_translate_descriptions
├── 04_translate
└── 06_covers

07_localized_catalog   depends on: 01_download, 03_describe, 04_translate, 05_translate_descriptions
08_universal_metadata  depends on: 01_download, 02_hash, 06_covers
```

`02_hash`, `03_describe`, `04_translate` and `06_covers` only depend on `01_download` and not on each other, so they run in parallel via a thread pool as soon as the download stage succeeds. `05_translate_descriptions` follows right after, once `03_describe` finishes, since it depends on that stage's output. `08_universal_metadata` fires as soon as `02_hash` and `06_covers` are done, without waiting for the translation stages it does not depend on. If a stage fails or is skipped, every stage that depends on it (directly or transitively) is marked as skipped in cascade, instead of the whole pipeline stopping.

Each stage's own output file carries the actual data quality signal (`status`/`reason` per record, see `docs/en/data-quality-log.md`), which is also what the next stage uses to decide whether to process a given record, not just the presence of a file. The reasoning behind this dispatch model, the alternatives considered (a real orchestrator, a filesystem watcher, per-item granularity), and the trade-offs of each are documented in ADR 003 and ADR 004 in `docs/en/ADR.md`.

## Output Files

`data/output/localized_catalog.json`: one entry per book, with `id`, `author`, and `title`/`description` localized to PT/EN/ES/FR.

`data/output/universal_metadata.json`: one entry per book, with `id`, `document_hash`, `cover_path`, `accesses`, `size_bytes`, `category`, and `year`.

Neither file carries a `status`/`reason` field: a `null` value in a given field is a legitimate outcome in some cases (for example, `year` is genuinely absent at the source for most books in this catalog, see DQ-004) and a real failure signal in others, and collapsing both into a single per-record status would misrepresent one or the other. That traceability is kept in the intermediate files instead (`catalog.json`, `descriptions.json`, `translations.json`, `description_translations.json`, `covers.json`), each scoped to the dimension it describes. The full reasoning is in DQ-008 in `docs/en/data-quality-log.md`.

## Design Decisions and Trade-offs

Every non-trivial engineering decision made during this delivery, including the ones later reversed, is recorded in two living documents:

- [`docs/en/ADR.md`](docs/en/ADR.md) ([PT](docs/pt/ADR.md)): architecture-level decisions (target area selection, LLM provider, dispatch mechanism and granularity for the Event-Driven pipeline).
- [`docs/en/data-quality-log.md`](docs/en/data-quality-log.md) ([PT](docs/pt/data-quality-log.md)): data quality issues found during development, their root cause, and the solution applied to each, including issues found by code review rather than by an observed failure.

Some notable decisions worth highlighting here:

- The scaffold's local Ollama LLM was replaced with the Gemini API, since the development machine did not meet the 16GB RAM the Ollama container requires (ADR 002).
- Failure in any stage is signaled explicitly (`status`/`reason`, and an aggregated `translation_complete` where applicable) instead of being left as a silent `null`, and that signal is what downstream stages check before processing a record, not just the presence of an output file (DQ-001, DQ-006, DQ-008).
- A fixed delay between LLM calls was added to stay under the Gemini free tier's rate limit, but it only removes one class of failure (429, quota). A transient provider-side failure (503) is not prevented by that pause: it is deliberately left to be absorbed by the same isolate-and-reprocess model used for every other failure in the pipeline, a more realistic approach for an external dependency than trying to shield against every momentary instability from the provider (DQ-001).

## AI Assistance

An AI assistant (Claude) was used throughout this project's development, as a pair-programming and documentation partner: reviewing code, drafting and translating documentation, and helping troubleshoot the local environment setup. Every engineering and documentation decision in this project, and the reasoning recorded for it in docs/, were made by the author and reflect the author's own judgment.
