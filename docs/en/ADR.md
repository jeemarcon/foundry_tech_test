# Architecture Decision Records

Record of the architecture decisions made during pipeline development, along with the context and consequences of each.

## ADR 001: Choice of delivery target areas

**Context:**
The challenge requires choosing at least 2 of the 5 defined target areas (Event-Driven Pipeline, Data Architecture, Versioning, Scalability, Data Quality), with evaluation focused on pipeline design and engineering decisions. The choice also considered technical knowledge and prior experience with each area.

**Decision:**
Chose Data Quality and Event-Driven Pipeline as target areas. The choice considered not only prior technical knowledge of each area, but also the actual content of the case: the pipeline's stages, the source of the data, and the maturity of the provided base. Data Quality was the more direct choice, being one of the areas of strongest prior technical expertise and, at the same time, one of the most evident in the scenario itself: data scraped from a public source carries a concrete risk of silent failures. That hypothesis was confirmed already in the first manual, step-by-step run, when translation failures occurred silently exactly as expected (see DQ-001). Event-Driven Pipeline was the second choice: a concrete learning opportunity in a specific technical gap (event-driven execution outside the context of managed tooling), reinforced by reading the original `main.py`, which revealed that the pipeline's execution, in a fixed order, could be better managed and optimized, instead of depending on a static order. Data Architecture was evaluated and discarded as a target area, despite being the option with the most prior familiarity (layered architectures): the project has a single data source and only two final files required by the case, with no multiple sources to integrate and no BI/analytics consumption that would justify formalizing additional aggregation layers. Formalizing that kind of architecture here would mean borrowing and forcing terminology (a "gold" layer with no real computation or aggregation) for what is really a plain folder split, with no real need behind it. Although the formal choices were these two, development repeatedly ran into decisions belonging to other target areas (for example, rate limiting under Scalability, see DQ-001), which is a natural consequence of a well-built engineering project, not an undeclared scope expansion.

**Consequences:**
Execution on Event-Driven Pipeline involves less prior expertise, requiring more study time within the test's deadline. On the other hand, it represents a concrete learning opportunity outside the current technical comfort zone.

## ADR 002: LLM provider swap: local Ollama to hosted Gemini

**Context:**
The case's scaffold configures a local LLM via Ollama by default, orchestrated by `compose.yaml`, which reserves 16GB of RAM for that container alone. The machine used for development does not support that requirement, and the case statement itself allows swapping the LLM provider via environment variables (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`), with no need to change the scripts' code.

**Decision:**
Replaced local Ollama with the Gemini API (Google AI Studio), using its OpenAI-compatible endpoint. The chosen model was `gemini-3.5-flash-lite`, since it supports image input (required by the description script, which sends rendered PDF pages) and, among the free options evaluated, had the largest available quota margin for this project's call volume. The `ollama` service and its dependency in `compose.yaml` were removed.

**Consequences:**
The pipeline now depends on internet access and an external API key, instead of running fully offline. On the other hand, it becomes runnable on any machine regardless of hardware capacity, at no cost within the expected volume (~10 books).

## ADR 003: Dispatch granularity in the Event-Driven Pipeline

**Context:**
With the Event-Driven Pipeline target area adopted (see ADR 001), it was necessary to evaluate which granularity level to use for dispatch between stages. Three alternatives were considered: a real orchestrator (Airflow or Temporal), item-level reactivity (each book triggers the next stage as soon as it is ready), and stage-level reactivity (the next stage is triggered when the previous stage's output file is updated). A real orchestrator would bring scheduling, native retry with backoff, and ready-made observability, but would require additional infrastructure (scheduler, metadata database, workers) disproportionate to this case's volume, deadline, and hardware 😅. Item-level granularity would allow downstream processing to start earlier and offer greater theoretical parallelism, but its real gains in speed, cost, and memory depend on high volume or highly variable latency between items; at this case's scale (10 books, LLM calls with similar duration to one another), that gain would be architectural and would not translate into measurable impact.

**Decision:**
Adopted stage-level granularity: each pipeline stage triggers the next by reacting to its output file being updated, not to a fixed schedule. The trigger condition uses the `status`/`reason` fields (see DQ-001 in `data-quality-log.md`) as the decision criterion, not simply the file's existence, so that the next stage only advances on records that actually succeeded in the previous stage. This approach directly reuses the work already done in the Data Quality target area and connects the two target areas chosen in ADR 001.

**Consequences:**
The pipeline gains real reactivity, without depending on manual execution or a fixed schedule, and without the operational complexity of a full orchestrator or the risk of an item-level model, which would require additional decisions about what to do with a partially completed pipeline. On the other hand, the solution does not offer real parallelism between items within the same stage, nor the observability and automatic retry features of a dedicated orchestrator; if the project's data volume or latency requirements grew significantly, this decision would need to be revisited.

## ADR 004: Dispatch mechanism between stages of the Event-Driven Pipeline

**Context:**
With stage-level granularity defined (see ADR 003), a technical mechanism was needed to detect when a stage is ready to trigger the next one. Two alternatives were evaluated: a real filesystem watcher (the `watchdog` library, explicitly cited in the case statement as a valid event-driven pattern) and a dependency-graph dispatcher declared in code, reacting to each stage's completion instead of file events. Mapping the real dependencies between the 8 scripts also showed that several stages (`02_hash`, `03_describe`, `04_translate`, `06_covers`) depend only on `01_download` and not on each other, but the original `main.py` ran them in a fixed sequence (1 through 8), without taking advantage of this real, measurable parallelism.

**Decision:**
Adopted the dependency-graph dispatcher, not the file watcher. In `watchdog`, the pipeline itself is the one writing the observed files (the subprocesses it launches), and each stage writes its output incrementally (one record saved at a time), generating multiple modification events per file. The dispatcher implemented in `main.py` declares the dependency graph of the 8 stages and, at the end of each one, checks which others already have all their dependencies satisfied, triggering all of them at once via `ThreadPoolExecutor`. Stages whose dependency failed (non-zero exit code) or was skipped are marked as `skipped` in cascade, preserving the isolation-by-status/reason model already used in the Data Quality target area, now also at the stage level.

**Consequences:**
The pipeline gains real parallelism between independent stages, confirmed in an actual run: `02_hash`, `03_describe`, `04_translate`, and `06_covers` fired simultaneously right after `01_download`, and `08_universal_metadata` fired as soon as `02_hash` and `06_covers` finished, without waiting for `03_describe`/`04_translate`/`05_translate_descriptions` to complete, reducing total execution time. As a positive side effect, a stage's failure no longer stops the entire pipeline: the original `main.py` stopped (`break`) on the first error, abandoning even downstream stages with no real dependency on the one that failed; now only the stages that actually depend on that failure are skipped, and independent branches continue normally. On the other hand, the solution does not generalize to a scenario where the data were produced by a real external system, outside the pipeline's own control; in that case, a file watcher or a message queue would be the more suitable choice.

The real parallelism brought a negative side effect: since each stage runs in its own thread/subprocess, prints from concurrent stages interleaved on the console with no control, making the log harder to read during execution (traceability in the generated files was never affected, each stage writes its own JSON independently). **Fixed by capturing each subprocess's output and reprinting it line by line with a `[stage]` prefix, instead of letting each subprocess inherit the console directly. - (done)**
