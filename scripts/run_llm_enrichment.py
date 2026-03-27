"""Batch 2: LLM-based enrichment for existing chunks.

Requires SageMaker (GRAPHRAG_USE_SAGEMAKER=true) or Ollama.
Processes: GraphRAG, L2 category (future), term definition enrichment (future).

Currently wraps run_graphrag_parallel.py — extend with L2/definition logic later.

Usage:
    # GraphRAG only (current)
    GRAPHRAG_USE_SAGEMAKER=true AWS_PROFILE=jeongbeomkim GRAPHRAG_WORKERS=8 \
        uv run python scripts/run_llm_enrichment.py graphrag drp g-espa partnertalk hax

    # Future: L2 category assignment
    USE_SAGEMAKER_LLM=true AWS_PROFILE=jeongbeomkim \
        uv run python scripts/run_llm_enrichment.py l2-category a-ari drp

    # Future: Term definition enrichment
    USE_SAGEMAKER_LLM=true AWS_PROFILE=jeongbeomkim \
        uv run python scripts/run_llm_enrichment.py term-enrich a-ari
"""
import os
import sys
import logging
import subprocess

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run_graphrag(kb_ids: list[str]):
    """Run parallel GraphRAG extraction.

    NOTE: OWNS/CATEGORIZED_AS edges are handled by run_metadata_backfill.py (Batch 1).
    This only runs LLM-based entity/relationship extraction (Store, Person, Process, etc.)
    """
    workers = os.getenv("GRAPHRAG_WORKERS", "8")
    logger.info(f"GraphRAG: {kb_ids} with {workers} workers")
    logger.info("NOTE: OWNS/CATEGORIZED_AS edges are NOT created here (use run_metadata_backfill.py)")
    subprocess.run(
        ["uv", "run", "python", "scripts/run_graphrag_parallel.py", *kb_ids],
        env={
            **os.environ,
            "GRAPHRAG_USE_SAGEMAKER": os.getenv("GRAPHRAG_USE_SAGEMAKER", "true"),
            "AWS_PROFILE": os.getenv("AWS_PROFILE", "jeongbeomkim"),
            "GRAPHRAG_WORKERS": workers,
        },
    )


def run_l2_category(kb_ids: list[str]):
    """Assign L2 categories using LLM classification. (Placeholder)"""
    logger.warning("L2 category assignment not yet implemented. Coming soon.")
    # Future: For each chunk, send title+content to LLM with L1 category context,
    # ask for L2 subcategory, check similarity with existing L2s before creating new.


def run_term_enrich(kb_ids: list[str]):
    """Enrich term definitions using LLM. (Placeholder)"""
    logger.warning("Term definition enrichment not yet implemented. Coming soon.")
    # Future: For each pending term with empty definition,
    # gather context chunks and ask LLM to generate definition.


COMMANDS = {
    "graphrag": run_graphrag,
    "l2-category": run_l2_category,
    "term-enrich": run_term_enrich,
}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <command> [kb_ids...]")
        print(f"Commands: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    command = sys.argv[1]
    kb_ids = sys.argv[2:] if len(sys.argv) > 2 else []

    if command not in COMMANDS:
        print(f"Unknown command: {command}")
        print(f"Available: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    if not kb_ids:
        print("No KB IDs specified.")
        sys.exit(1)

    logger.info(f"Command: {command}, KBs: {kb_ids}")
    COMMANDS[command](kb_ids)
