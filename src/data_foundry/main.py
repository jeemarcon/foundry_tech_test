import os
import subprocess
import sys
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"

# Real dependency graph of the pipeline (see ADR 003 in docs/en/ADR.md or docs/pt/ADR.md). A stage
# only fires once every stage listed in "depends_on" has finished successfully;
# stages with no dependency on each other (e.g. hash, describe, translate and
# covers, which only depend on download) run in parallel. Declared in
# topological order on purpose, but the skip propagation below does not rely on that.
STAGES = {
    "download": {
        "script": "01_download.py",
        "description": "Scrape catalog and download PDFs",
        "depends_on": [],
    },
    "hash": {
        "script": "02_hash.py",
        "description": "Calculate document hashes",
        "depends_on": ["download"],
    },
    "describe": {
        "script": "03_describe.py",
        "description": "Generate descriptions via vision LLM",
        "depends_on": ["download"],
    },
    "translate": {
        "script": "04_translate.py",
        "description": "Translate titles",
        "depends_on": ["download"],
    },
    "translate_descriptions": {
        "script": "05_translate_descriptions.py",
        "description": "Translate descriptions",
        "depends_on": ["describe"],
    },
    "covers": {
        "script": "06_covers.py",
        "description": "Extract cover pages",
        "depends_on": ["download"],
    },
    "localized_catalog": {
        "script": "07_localized_catalog.py",
        "description": "Assemble localized catalog",
        "depends_on": ["download", "translate", "describe", "translate_descriptions"],
    },
    "universal_metadata": {
        "script": "08_universal_metadata.py",
        "description": "Assemble universal metadata",
        "depends_on": ["download", "hash", "covers"],
    },
}

# Guards interleaved prints from concurrent stages: each stage's subprocess
# output is captured and re-printed line by line with a [stage] prefix instead
# of inheriting stdout directly, so parallel stages don't garble each other's
# output in the console (see ADR 004).
_print_lock = threading.Lock()


def run_stage(stage: str) -> bool:
    info = STAGES[stage]
    tag = f"[{stage}]"

    with _print_lock:
        print(f"\n{tag} {'=' * 50}")
        print(f"{tag} {info['description']}")
        print(f"{tag} Running: {info['script']}")
        print(f"{tag} {'=' * 50}\n")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    process = subprocess.Popen(
        [sys.executable, str(SCRIPTS_DIR / info["script"])],
        cwd=str(SCRIPTS_DIR.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    for line in process.stdout:
        with _print_lock:
            print(f"{tag} {line.rstrip()}")
    process.wait()
    return process.returncode == 0


def propagate_skips(state: dict) -> None:
    """Marks as 'skipped' every pending stage that depends on something that
    failed or was skipped. Runs until it stabilizes, so it does not rely on
    the order of STAGES."""
    changed = True
    while changed:
        changed = False
        for stage, info in STAGES.items():
            if state[stage] != "pending":
                continue
            if any(state[dep] in ("failed", "skipped") for dep in info["depends_on"]):
                state[stage] = "skipped"
                print(f"\n[skip] {stage}: dependency did not complete successfully")
                changed = True


def main():
    print("Domínio Público Data Pipeline (data-driven stage dispatch)")
    print("=" * 60)

    state = {stage: "pending" for stage in STAGES}
    futures = {}

    with ThreadPoolExecutor(max_workers=len(STAGES)) as executor:
        while any(status == "pending" for status in state.values()) or futures:
            propagate_skips(state)

            ready = [
                stage
                for stage, info in STAGES.items()
                if state[stage] == "pending"
                and all(state[dep] == "ok" for dep in info["depends_on"])
            ]
            for stage in ready:
                state[stage] = "running"
                futures[executor.submit(run_stage, stage)] = stage

            if not futures:
                # Nothing running and nothing became ready, but stages are
                # still pending: only happens with an inconsistent dependency graph.
                for stage, status in state.items():
                    if status == "pending":
                        state[stage] = "skipped"
                        print(f"\n[skip] {stage}: never became ready (inconsistent graph)")
                break

            done, _ = wait(list(futures.keys()), return_when=FIRST_COMPLETED)
            for future in done:
                stage = futures.pop(future)
                state[stage] = "ok" if future.result() else "failed"

    print(f"\n{'=' * 60}")
    print("Pipeline Summary")
    print(f"{'=' * 60}")
    icons = {"ok": "+", "failed": "X", "skipped": "-"}
    for stage, info in STAGES.items():
        print(f"  [{icons[state[stage]]}] {info['script']}: {state[stage]}")


if __name__ == "__main__":
    main()
