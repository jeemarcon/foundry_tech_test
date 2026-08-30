# Architecture Decision Records

Record of the architecture decisions made during pipeline development, along with the context and consequences of each.

## ADR 001: Choice of delivery pillars

**Context:**
The challenge requires choosing at least 2 of the 5 defined delivery areas (Event-Driven Pipeline, Data Architecture, Versioning, Scalability, Data Quality), with evaluation focused on engineering decisions, not on the provided scripts. My choice considered both prior technical knowledge and alignment with the Data Foundry Engineer role.

**Decision:**
Chose Data Quality and Event-Driven Pipeline as pillars. Data Quality was selected based on prior knowledge and experience, as well as direct alignment with the role's requirements (deduplication, normalization, data consistency). Event-Driven Pipeline was selected as a concrete learning opportunity within the scope of the test, and also because it represents a competency explicitly mentioned in the role (orchestration via Airflow/Temporal). Data Architecture was evaluated and discarded as a formal pillar since there was no real need for aggregation layers in this project. A basic raw/processed separation is kept as good practice regardless.

**Consequences:**
The delivery gains direct alignment with the role's technical requirements and demonstrates willingness to learn outside the current technical comfort zone. On the other hand, execution on Event-Driven Pipeline involves less prior expertise, requiring more study time within the test's deadline.

## ADR 002: LLM provider swap: local Ollama to hosted Gemini

**Context:**
The case's scaffold configures a local LLM via Ollama by default, orchestrated by `compose.yaml`, which reserves 16GB of RAM for that container alone. The machine used for development does not support that requirement, and the case statement itself allows swapping the LLM provider via environment variables (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`), with no need to change the scripts' code.

**Decision:**
Replaced local Ollama with the Gemini API (Google AI Studio), using its OpenAI-compatible endpoint. The chosen model was `gemini-3.5-flash-lite`, since it supports image input (required by the description script, which sends rendered PDF pages) and, among the free options evaluated, had the largest available quota margin for this project's call volume. The `ollama` service and its dependency in `compose.yaml` were removed.

**Consequences:**
The pipeline now depends on internet access and an external API key, instead of running fully offline. On the other hand, it becomes runnable on any machine regardless of hardware capacity, at no cost within the expected volume (~10 books).

## ADR 003: Failure signaling in description translations

**Context:**
The script `05_translate_descriptions.py` fails silently when it hits the Gemini API's rate limit (see DQ-001 in `data-quality-log.md`), leaving missing translations with no signal of the cause. Each language (EN/ES/FR) is an independent LLM call and can fail for different reasons within the same book.

**Decision:**
Added two-level status signaling: a per-language `status`/`reason` field, recording success or the specific failure reason of each individual translation, and an aggregated `translation_complete` field at the book level, summarizing whether all of that record's translations completed successfully. The per-language level serves auditing and precise retry; the aggregated level serves as a simple decision signal for the pipeline's next stages, including the Event-Driven pillar.

**Consequences:**
`description_translations.json` now carries more structure per language, requiring an adjustment to the format read by `07_localized_catalog.py`. On the other hand, this gains real failure traceability and a concrete basis for the Event-Driven pillar to decide whether to move on to the next stage.

## ADR 004: Dispatch granularity in the Event-Driven Pipeline

**Context:**
With the Event-Driven Pipeline pillar adopted (see ADR 001), it was necessary to evaluate which granularity level to use for dispatch between stages. Three alternatives were considered: a real orchestrator (Airflow or Temporal), item-level reactivity (each book triggers the next stage as soon as it is ready), and stage-level reactivity (the next stage is triggered when the previous stage's output file is updated). A real orchestrator would bring scheduling, native retry with backoff, and ready-made observability, but would require additional infrastructure (scheduler, metadata database, workers) disproportionate to this case's volume and deadline. Item-level granularity would allow downstream processing to start earlier and offer greater theoretical parallelism, but its real gains in speed, cost, and memory depend on high volume or highly variable latency between items; at this case's scale (10 books, LLM calls with similar duration to one another), that gain would be architectural and would not translate into measurable impact.

**Decision:**
Adopted stage-level granularity: each pipeline stage triggers the next by reacting to its output file being updated, not to a fixed schedule. The trigger condition uses the `status`/`reason` fields (see ADR 003) as the decision criterion, not simply the file's existence, so that the next stage only advances on records that actually succeeded in the previous stage. This approach directly reuses the work already done in the Data Quality pillar and connects the two pillars chosen in ADR 001.

**Consequences:**
The pipeline gains real reactivity, without depending on manual execution or a fixed schedule, and without the operational complexity of a full orchestrator or the risk of an item-level model, which would require additional decisions about what to do with a partially completed pipeline. On the other hand, the solution does not offer real parallelism between items within the same stage, nor the observability and automatic retry features of a dedicated orchestrator; if the project's data volume or latency requirements grew significantly, this decision would need to be revisited.

## ADR 005: Dispatch mechanism between stages of the Event-Driven Pipeline

**Context:**
With stage-level granularity defined (see ADR 004), a technical mechanism was needed to detect when a stage is ready to trigger the next one. Two alternatives were evaluated: a real filesystem watcher (the `watchdog` library, explicitly cited in the case statement as a valid event-driven pattern) and a dependency-graph dispatcher declared in code, reacting to each stage's completion instead of file events. Mapping the real dependencies between the 8 scripts also showed that several stages (`02_hash`, `03_describe`, `04_translate`, `06_covers`) depend only on `01_download` and not on each other, but the original `main.py` ran them in a fixed sequence (1 through 8), without taking advantage of this real, measurable parallelism.

**Decision:**
Adopted the dependency-graph dispatcher, not the file watcher. In `watchdog`, the pipeline itself is the one writing the observed files (the subprocesses it launches), and each stage writes its output incrementally (one record saved at a time), generating multiple modification events per file; using those events as a trigger would require additional debounce logic to avoid triggering the next stage prematurely or duplicately, with no real gain in this project, since there is no true external producer writing this data. The dispatcher implemented in `main.py` declares the dependency graph of the 8 stages and, at the end of each one, checks which others already have all their dependencies satisfied, triggering all of them at once via `ThreadPoolExecutor`. Stages whose dependency failed (non-zero exit code) or was skipped are marked as `skipped` in cascade, preserving the isolation-by-status/reason model already used in the Data Quality pillar, now also at the stage level.

**Consequences:**
The pipeline gains real parallelism between independent stages, confirmed in an actual run: `02_hash`, `03_describe`, `04_translate`, and `06_covers` fired simultaneously right after `01_download`, and `08_universal_metadata` fired as soon as `02_hash` and `06_covers` finished, without waiting for `03_describe`/`04_translate`/`05_translate_descriptions` to complete, reducing total execution time. As a positive side effect, a stage's failure no longer stops the entire pipeline, which was the original `main.py`'s behavior: now only the stages that actually depend on that failure are skipped, and independent branches continue normally. On the other hand, the solution does not generalize to a scenario where the data were produced by a real external system, outside the pipeline's own control; in that case, a file watcher or a message queue would be the more suitable choice.

The real parallelism brought a negative side effect: since each stage runs in its own thread/subprocess, prints from concurrent stages interleaved on the console with no control, making the log harder to read during execution (traceability in the generated files was never affected, each stage writes its own JSON independently). Fixed by capturing each subprocess's output and reprinting it line by line with a `[stage]` prefix, instead of letting each subprocess inherit the console directly. *(done)*
