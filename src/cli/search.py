"""CLI: Search knowledge base.

Usage:
    python -m cli.search "K8s Pod restart 방법"
    python -m cli.search "담당자 누구" --kb-id infra-kb
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def search(query: str, kb_id: str, top_k: int, with_answer: bool) -> None:
    from src.config import get_settings
    from src.stores.qdrant.client import QdrantConfig, QdrantClientProvider
    from src.stores.qdrant.collections import QdrantCollectionManager
    from src.stores.qdrant.search import QdrantSearchEngine

    settings = get_settings()

    config = QdrantConfig.from_env()
    provider = QdrantClientProvider(config)
    await provider.ensure_client()
    cm = QdrantCollectionManager(provider)
    engine = QdrantSearchEngine(provider, cm)

    # Embed query
    from src.nlp.embedding.onnx_provider import OnnxBgeEmbeddingProvider

    model_path = settings.embedding.onnx_model_path or os.getenv("KNOWLEDGE_BGE_ONNX_MODEL_PATH", "")
    embedder = OnnxBgeEmbeddingProvider(model_path=model_path)

    if not embedder.is_ready():
        print("ERROR: BGE-M3 model not ready")
        return

    output = embedder.encode([query], return_dense=True, return_sparse=True)
    dense = output["dense_vecs"][0] if output["dense_vecs"] else []
    sparse = output["lexical_weights"][0] if output["lexical_weights"] else {}

    results = await engine.search(
        kb_id=kb_id,
        dense_vector=dense,
        sparse_vector=sparse,
        top_k=top_k,
    )

    print(f"\n{'='*60}")
    print(f"Query: {query}")
    print(f"KB: {kb_id} | Results: {len(results)}")
    print(f"{'='*60}\n")

    for i, r in enumerate(results):
        print(f"[{i+1}] Score: {r.score:.4f}")
        print(f"    Document: {r.metadata.get('document_name', 'N/A')}")
        print(f"    Content: {r.content[:200]}...")
        print()

    if with_answer and results:
        from src.nlp.llm.ollama_client import OllamaClient, OllamaConfig

        llm = OllamaClient(OllamaConfig(
            base_url=settings.ollama.base_url,
            model=settings.ollama.model,
        ))
        context = "\n\n".join(
            f"[{i+1}] {r.content}"
            for i, r in enumerate(results)
        )
        answer = await llm.generate_with_context(query=query, context=context)
        print(f"{'='*60}")
        print(f"Answer:\n{answer}")
        print(f"{'='*60}")

    await provider.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Knowledge Search CLI")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--kb-id", default="knowledge", help="Knowledge base ID")
    parser.add_argument("--top-k", type=int, default=5, help="Number of results")
    parser.add_argument("--no-answer", action="store_true", help="Skip LLM answer")

    args = parser.parse_args()
    asyncio.run(search(args.query, args.kb_id, args.top_k, not args.no_answer))


if __name__ == "__main__":
    main()
