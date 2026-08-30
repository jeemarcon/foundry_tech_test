import subprocess
import sys
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"

# Grafo de dependência real do pipeline (ver ADR 004 em docs/ADR.md). Cada etapa
# só dispara quando todas as etapas listadas em "depends_on" tiverem terminado
# com sucesso; etapas sem dependência entre si (ex.: hash, describe, translate
# e covers, que só dependem de download) rodam em paralelo. Declarado em ordem
# topológica de propósito, mas a propagação de skip abaixo não depende disso.
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


def run_stage(stage: str) -> bool:
    info = STAGES[stage]
    print(f"\n{'=' * 60}")
    print(f"  {info['description']}")
    print(f"  Running: {info['script']}")
    print(f"{'=' * 60}\n")

    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / info["script"])],
        cwd=str(SCRIPTS_DIR.parent),
    )
    return result.returncode == 0


def propagate_skips(state: dict) -> None:
    """Marca como 'skipped' toda etapa pendente que dependa de algo que falhou
    ou foi pulado. Roda até estabilizar, para não depender da ordem de STAGES."""
    changed = True
    while changed:
        changed = False
        for stage, info in STAGES.items():
            if state[stage] != "pending":
                continue
            if any(state[dep] in ("failed", "skipped") for dep in info["depends_on"]):
                state[stage] = "skipped"
                print(f"\n[skip] {stage}: dependência não concluída com sucesso")
                changed = True


def main():
    print("Domínio Público Data Pipeline (dispatch orientado a dados)")
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
                # Nada rodando e nada ficou pronto, mas ainda há etapas pendentes:
                # só acontece com um grafo de dependência inconsistente.
                for stage, status in state.items():
                    if status == "pending":
                        state[stage] = "skipped"
                        print(f"\n[skip] {stage}: nunca ficou pronto (grafo inconsistente)")
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
