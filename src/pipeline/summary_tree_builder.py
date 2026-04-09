"""Summary Tree Builder — RAPTOR식 bottom-up 클러스터링 → 요약 트리.

청크들을 임베딩 유사도로 클러스터링하고 LLM으로 요약하여
상위 계층 요약 노드를 Qdrant에 저장한다.

알고리즘 출처: RAPTOR (UC Berkeley, 2024)
구현: 자체 (BGE-M3 + EXAONE, scikit-learn GMM, 선택적 UMAP)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

import numpy as np

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """다음 텍스트들의 핵심 내용을 한국어로 요약하세요.
가능한 많은 핵심 세부사항을 포함하되, 간결하게 작성하세요.

{context}

요약:"""


class Embedder(Protocol):
    async def embed(self, text: str) -> list[float]: ...
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class LLM(Protocol):
    async def generate(self, prompt: str, *, max_tokens: int | None = None) -> str: ...


def _cluster_embeddings(
    embeddings: np.ndarray,
    *,
    use_umap: bool = True,
    umap_dim: int = 10,
    max_clusters: int = 20,
) -> list[list[int]]:
    """임베딩 벡터를 UMAP 축소 후 GMM으로 클러스터링.

    Returns:
        클러스터별 인덱스 리스트. 예: [[0,1,3], [2,4,5]]
    """
    from sklearn.mixture import GaussianMixture

    n_samples = len(embeddings)
    if n_samples < 2:
        return [list(range(n_samples))]

    reduced = embeddings
    if use_umap and n_samples > umap_dim + 2:
        try:
            import umap
            n_neighbors = min(15, max(2, int(n_samples ** 0.5)))
            reducer = umap.UMAP(
                n_components=min(umap_dim, n_samples - 2),
                n_neighbors=n_neighbors,
                metric="cosine",
                random_state=42,
            )
            reduced = reducer.fit_transform(embeddings)
        except Exception as e:
            logger.debug("UMAP reduction failed, using raw embeddings: %s", e)
            reduced = embeddings

    # BIC 기반 최적 클러스터 수 결정
    max_k = min(max_clusters, n_samples // 2, n_samples - 1)
    max_k = max(max_k, 1)

    best_k, best_bic = 1, float("inf")
    for k in range(1, max_k + 1):
        try:
            gmm = GaussianMixture(n_components=k, random_state=42, covariance_type="full")
            gmm.fit(reduced)
            bic = gmm.bic(reduced)
            if bic < best_bic:
                best_bic = bic
                best_k = k
        except Exception:
            break

    gmm = GaussianMixture(n_components=best_k, random_state=42, covariance_type="full")
    labels = gmm.fit_predict(reduced)

    clusters: dict[int, list[int]] = {}
    for idx, label in enumerate(labels):
        clusters.setdefault(int(label), []).append(idx)

    return list(clusters.values())


async def _summarize_cluster(
    texts: list[str],
    llm: LLM,
    *,
    max_context_chars: int = 6000,
) -> str:
    """클러스터 텍스트들을 LLM으로 요약."""
    combined = "\n\n---\n\n".join(texts)
    if len(combined) > max_context_chars:
        combined = combined[:max_context_chars] + "\n...(생략)"

    prompt = SUMMARY_PROMPT.format(context=combined)
    try:
        return await llm.generate(prompt, max_tokens=300)
    except Exception as e:
        logger.warning("Summary generation failed: %s", e)
        return ""


async def build_summary_layer(
    texts: list[str],
    embeddings: np.ndarray,
    embedder: Embedder,
    llm: LLM,
    *,
    use_umap: bool = True,
    umap_dim: int = 10,
    min_chunks: int = 5,
) -> list[dict[str, Any]]:
    """단일 계층 요약 생성: 클러스터링 → 요약 → 임베딩.

    Args:
        texts: 현재 계층의 텍스트들
        embeddings: 현재 계층의 임베딩 벡터들
        embedder: 임베딩 프로바이더
        llm: LLM 클라이언트
        use_umap: UMAP 차원 축소 사용 여부
        umap_dim: UMAP 축소 차원
        min_chunks: 클러스터링 최소 청크 수 (미만이면 스킵)

    Returns:
        [{"text": 요약문, "embedding": 벡터, "source_indices": [원본 인덱스]}]
    """
    if len(texts) < min_chunks:
        return []

    clusters = await asyncio.to_thread(
        _cluster_embeddings, embeddings,
        use_umap=use_umap, umap_dim=umap_dim,
    )

    # 클러스터별 요약 생성 (병렬)
    summary_coros = []
    for cluster_indices in clusters:
        cluster_texts = [texts[i] for i in cluster_indices]
        summary_coros.append(_summarize_cluster(cluster_texts, llm))

    summaries = await asyncio.gather(*summary_coros)

    # 빈 요약 필터링 + 임베딩
    valid_summaries = []
    for summary_text, cluster_indices in zip(summaries, clusters):
        if summary_text.strip():
            valid_summaries.append((summary_text, cluster_indices))

    if not valid_summaries:
        return []

    summary_texts = [s[0] for s in valid_summaries]
    summary_embeddings = await embedder.embed_documents(summary_texts)

    results = []
    for (text, source_indices), embedding in zip(valid_summaries, summary_embeddings):
        results.append({
            "text": text,
            "embedding": embedding,
            "source_indices": source_indices,
        })

    return results


async def build_summary_tree(
    chunks: list[dict[str, Any]],
    embedder: Embedder,
    llm: LLM,
    *,
    max_layers: int = 3,
    min_chunks: int = 5,
    use_umap: bool = True,
    umap_dim: int = 10,
) -> list[dict[str, Any]]:
    """재귀적 요약 트리 생성 (bottom-up).

    Args:
        chunks: [{"text": str, "embedding": list[float], "chunk_id": str, ...}]
        embedder: 임베딩 프로바이더
        llm: LLM 클라이언트
        max_layers: 최대 요약 계층 수
        min_chunks: 클러스터링 최소 청크 수
        use_umap: UMAP 차원 축소
        umap_dim: UMAP 축소 차원

    Returns:
        [{"text", "embedding", "layer", "source_chunk_ids": [str]}]
    """
    if not chunks:
        return []

    current_texts = [c["text"] for c in chunks]
    current_embeddings = np.array([c["embedding"] for c in chunks])
    chunk_ids = [c.get("chunk_id", "") for c in chunks]

    all_summaries: list[dict[str, Any]] = []

    for layer in range(1, max_layers + 1):
        layer_results = await build_summary_layer(
            current_texts, current_embeddings, embedder, llm,
            use_umap=use_umap, umap_dim=umap_dim, min_chunks=min_chunks,
        )

        if not layer_results:
            break

        # 원본 chunk_id 추적
        for result in layer_results:
            source_cids = []
            for idx in result["source_indices"]:
                if idx < len(chunk_ids):
                    source_cids.append(chunk_ids[idx])
            all_summaries.append({
                "text": result["text"],
                "embedding": result["embedding"],
                "layer": layer,
                "source_chunk_ids": source_cids,
            })

        # 다음 계층 입력 준비
        current_texts = [r["text"] for r in layer_results]
        current_embeddings = np.array([r["embedding"] for r in layer_results])
        chunk_ids = [f"summary_layer{layer}_{i}" for i in range(len(layer_results))]

        if len(current_texts) < min_chunks:
            break

    logger.info("Summary tree built: %d summaries across %d layers",
                len(all_summaries),
                max((s["layer"] for s in all_summaries), default=0))
    return all_summaries
