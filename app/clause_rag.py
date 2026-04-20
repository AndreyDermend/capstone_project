"""
RAG-based clause retrieval for the LexiAgent contract drafting system.

Uses nomic-embed-text (via Ollama) to embed clause variants and rank them
by similarity to user context at assembly time.

Architecture:
- Embeds all clauses from JSONL clause libraries on first use (cached to disk)
- At assembly time, filters candidates by contract_type + subtype + clause_name
- If only 1 candidate → deterministic (no vector search needed)
- If multiple candidates → rank by cosine similarity to user context string
- Returns the best-matching variant

The embedding cache is stored alongside the clause library JSONL files
as .embeddings.npz files. Delete these to force re-embedding.
"""

import json
import os
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ollama import embed

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "clause_library"
CONFIG_DIR = ROOT / "config"

EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------
def get_embedding(text: str) -> np.ndarray:
    """Get embedding vector for a single text string."""
    result = embed(model=EMBED_MODEL, input=text)
    return np.array(result.embeddings[0], dtype=np.float32)


def get_embeddings_batch(texts: List[str]) -> np.ndarray:
    """Get embeddings for a batch of texts. Returns (N, dim) array."""
    result = embed(model=EMBED_MODEL, input=texts)
    return np.array(result.embeddings, dtype=np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def cosine_similarities(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Cosine similarities between a query vector and a matrix of vectors."""
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0] = 1.0  # avoid division by zero
    query_norm = np.linalg.norm(query)
    if query_norm == 0:
        return np.zeros(len(matrix))
    return np.dot(matrix, query) / (norms * query_norm)


# ---------------------------------------------------------------------------
# Clause library with embeddings
# ---------------------------------------------------------------------------
class ClauseStore:
    """In-memory clause store with embedding-based retrieval."""

    def __init__(self):
        self.clauses: List[dict] = []
        self.embeddings: Optional[np.ndarray] = None
        self._loaded_files: set = set()

    def load_library(self, jsonl_path: Path) -> None:
        """Load a clause library JSONL and compute/cache embeddings."""
        jsonl_path = jsonl_path.resolve()
        if str(jsonl_path) in self._loaded_files:
            return

        # Load clauses
        new_clauses = []
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    new_clauses.append(json.loads(line))

        if not new_clauses:
            return

        # Check for cached embeddings
        cache_path = jsonl_path.with_suffix(".embeddings.npz")
        cached_embeddings = None

        if cache_path.exists():
            try:
                data = np.load(cache_path)
                if len(data["embeddings"]) == len(new_clauses):
                    cached_embeddings = data["embeddings"]
            except Exception:
                pass

        if cached_embeddings is None:
            # Compute embeddings
            texts = [clause["text"] for clause in new_clauses]
            print(f"  Embedding {len(texts)} clauses from {jsonl_path.name}...")
            cached_embeddings = get_embeddings_batch(texts)
            # Cache to disk
            np.savez_compressed(cache_path, embeddings=cached_embeddings)
            print(f"  Cached embeddings to {cache_path.name}")

        # Append to store
        start_idx = len(self.clauses)
        self.clauses.extend(new_clauses)

        if self.embeddings is None:
            self.embeddings = cached_embeddings
        else:
            self.embeddings = np.vstack([self.embeddings, cached_embeddings])

        self._loaded_files.add(str(jsonl_path))

    def find_variants(
        self,
        clause_name: str,
        subtype: str,
        subtype_field: str,
    ) -> List[Tuple[int, dict]]:
        """Find all clause variants matching the given filters.
        Returns list of (index, clause_dict) tuples."""
        results = []
        for i, clause in enumerate(self.clauses):
            if (
                clause.get("clause_name") == clause_name
                and clause.get(subtype_field) == subtype
            ):
                results.append((i, clause))
        return results

    def rank_variants(
        self,
        candidates: List[Tuple[int, dict]],
        context: str,
    ) -> List[Tuple[dict, float]]:
        """Rank clause variants by similarity to context string.
        Returns list of (clause_dict, score) tuples, sorted by score desc."""
        if not candidates or self.embeddings is None:
            return [(c[1], 0.0) for c in candidates]

        if len(candidates) == 1:
            return [(candidates[0][1], 1.0)]

        # Get context embedding
        context_emb = get_embedding(context)

        # Get candidate embeddings
        indices = [c[0] for c in candidates]
        candidate_embs = self.embeddings[indices]

        # Compute similarities
        sims = cosine_similarities(context_emb, candidate_embs)

        # Pair with clause dicts and sort
        ranked = [
            (candidates[i][1], float(sims[i]))
            for i in range(len(candidates))
        ]
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked

    def select_best(
        self,
        clause_name: str,
        subtype: str,
        subtype_field: str,
        context: str = "",
    ) -> Optional[Tuple[dict, float, int]]:
        """Select the best clause variant for a given clause name.

        Returns (clause_dict, similarity_score, num_candidates) or None.
        If only 1 candidate, returns it with score=1.0 (deterministic).
        If multiple candidates and context given, ranks by similarity.
        """
        candidates = self.find_variants(clause_name, subtype, subtype_field)
        if not candidates:
            return None

        num = len(candidates)

        if num == 1 or not context:
            # Deterministic: pick first by sort_order
            candidates.sort(key=lambda c: c[1].get("sort_order", 999))
            return candidates[0][1], 1.0, num

        # RAG: rank by similarity
        ranked = self.rank_variants(candidates, context)
        return ranked[0][0], ranked[0][1], num


# ---------------------------------------------------------------------------
# Module-level store (singleton)
# ---------------------------------------------------------------------------
_store: Optional[ClauseStore] = None


def get_store() -> ClauseStore:
    """Get or create the global clause store."""
    global _store
    if _store is None:
        _store = ClauseStore()
    return _store


def load_contract_clauses(contract_type: str) -> ClauseStore:
    """Load clause library for a contract type into the global store."""
    store = get_store()

    # Load registry to find the clause library path
    registry = json.loads(
        (CONFIG_DIR / "contract_registry.json").read_text(encoding="utf-8")
    )
    config = registry["contract_types"][contract_type]
    library_path = DATA_DIR / config["clause_library"]

    store.load_library(library_path)
    return store


def build_context_string(answers: Dict[str, str]) -> str:
    """Build a context string from user answers for similarity matching.

    The context string captures the user's intent and key details,
    which helps select clause variants that match their specific needs.
    """
    parts = []
    for key, value in answers.items():
        if value and str(value).strip():
            # Use field names as labels for better semantic matching
            label = key.replace("_", " ")
            parts.append(f"{label}: {value}")
    return ". ".join(parts)


# ---------------------------------------------------------------------------
# RAG-enhanced clause selection
# ---------------------------------------------------------------------------
def select_clauses_rag(
    contract_type: str,
    subtype: str,
    subtype_field: str,
    ordered_clause_names: List[str],
    answers: Dict[str, str],
) -> Tuple[List[dict], Dict[str, dict]]:
    """Select clauses using RAG when multiple variants exist.

    Returns:
        (selected_clauses, rag_metadata)
        rag_metadata maps clause_name -> {variant_id, score, num_candidates, method}
    """
    store = load_contract_clauses(contract_type)
    context = build_context_string(answers)

    selected = []
    metadata = {}

    for clause_name in ordered_clause_names:
        result = store.select_best(
            clause_name=clause_name,
            subtype=subtype,
            subtype_field=subtype_field,
            context=context,
        )

        if result is None:
            continue

        clause, score, num_candidates = result
        selected.append(clause)

        method = "deterministic" if num_candidates == 1 else "rag"
        metadata[clause_name] = {
            "variant_id": clause.get("variant_id", "unknown"),
            "source": clause.get("source", "unknown"),
            "score": round(score, 4),
            "num_candidates": num_candidates,
            "method": method,
        }

    return selected, metadata
