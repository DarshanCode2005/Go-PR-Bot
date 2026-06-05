"""Optional semantic retrieval over Go source chunks via ChromaDB."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from go_agent.code_graph import _collect_go_files
from go_agent.config import Settings
from go_agent.github_issues import IssueContext
from go_agent.git_util import run_git
from go_agent.repo_search import SearchHit
from go_agent.run_context import RunContext
from go_agent.workspace import repo_slug

RAG_QUERY_PREFIX = "rag:"
_INDEX_META = "index_meta.json"
_COLLECTION_NAME = "go_chunks"


class RagDepsNotFoundError(ImportError):
    """Raised when optional RAG dependencies are not installed."""


class RagChunk(BaseModel):
    path: str
    start_line: int
    end_line: int
    text: str


class RagHit(BaseModel):
    path: str
    line_number: int
    line_text: str
    query: str
    score: float
    chunk_start: int
    chunk_end: int


class RagArtifact(BaseModel):
    issue_number: int
    repo: str
    query: str
    hits: list[RagHit] = Field(default_factory=list)


def build_rag_query(issue: IssueContext, *, max_body_chars: int = 1500) -> str:
    """Build a retrieval query from issue title and body excerpt."""
    body = issue.body[:max_body_chars].strip()
    if body:
        return f"{issue.title}\n\n{body}"
    return issue.title


def chunk_go_files(repo_path: Path, settings: Settings) -> list[RagChunk]:
    """Split Go files into overlapping line windows."""
    chunk_size = max(settings.rag_chunk_lines, 1)
    overlap = max(min(settings.rag_chunk_overlap, chunk_size - 1), 0)
    step = max(chunk_size - overlap, 1)
    chunks: list[RagChunk] = []

    for path in _collect_go_files(repo_path, skip_vendor=settings.repo_map_skip_vendor):
        try:
            lines = (repo_path / path).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        if not lines:
            continue
        start = 0
        while start < len(lines):
            end = min(start + chunk_size, len(lines))
            text = "\n".join(lines[start:end])
            if text.strip():
                chunks.append(
                    RagChunk(
                        path=path,
                        start_line=start + 1,
                        end_line=end,
                        text=text,
                    )
                )
            if end >= len(lines):
                break
            start += step

    return chunks


def _resolve_repo_head(repo_path: Path) -> str:
    return run_git(["rev-parse", "HEAD"], cwd=repo_path)


def _index_dir(settings: Settings, repo: str, repo_head: str) -> Path:
    return settings.work_dir / "_cache" / repo_slug(repo) / "rag_index" / repo_head[:12]


def _chunk_id(chunk: RagChunk) -> str:
    return f"{chunk.path}:{chunk.start_line}:{chunk.end_line}"


def _embed_texts(texts: list[str], settings: Settings) -> list[list[float]]:
    if settings.rag_embed_provider == "openai":
        if not settings.openai_api_key:
            msg = "OPENAI_API_KEY required for rag_embed_provider=openai"
            raise ValueError(msg)
        try:
            import litellm
        except ImportError as exc:
            raise RagDepsNotFoundError("litellm is required for OpenAI embeddings") from exc
        model = settings.rag_embed_model
        if not model.startswith("text-embedding"):
            model = "text-embedding-3-small"
        response = litellm.embedding(model=model, input=texts)
        return [item["embedding"] for item in response.data]

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RagDepsNotFoundError(
            "sentence-transformers is required for local RAG embeddings; "
            'install with: pip install -e ".[rag]"'
        ) from exc

    model = SentenceTransformer(settings.rag_embed_model)
    vectors = model.encode(texts, normalize_embeddings=True)
    return [vector.tolist() for vector in vectors]


def _get_chroma_collection(index_dir: Path, settings: Settings) -> Any:
    try:
        import chromadb
    except ImportError as exc:
        raise RagDepsNotFoundError(
            'chromadb is required for RAG; install with: pip install -e ".[rag]"'
        ) from exc

    index_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(index_dir))
    return client.get_or_create_collection(
        name=_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def _index_is_ready(index_dir: Path, settings: Settings) -> bool:
    meta_path = index_dir / _INDEX_META
    if not meta_path.is_file():
        return False
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if not payload.get("chunk_count", 0):
        return False
    return (
        payload.get("embed_provider") == settings.rag_embed_provider
        and payload.get("embed_model") == settings.rag_embed_model
    )


def get_or_build_index(
    repo: str,
    repo_path: Path,
    repo_head: str,
    settings: Settings,
    logger: logging.Logger | None = None,
) -> Any:
    """Return a Chroma collection for the repo at repo_head, building it when missing."""
    log = logger or logging.getLogger("go_agent")
    index_dir = _index_dir(settings, repo, repo_head)
    collection = _get_chroma_collection(index_dir, settings)

    if _index_is_ready(index_dir, settings) and collection.count() > 0:
        log.info("RAG index cache hit for %s at %s", repo, index_dir)
        return collection

    chunks = chunk_go_files(repo_path, settings)
    if not chunks:
        log.warning("RAG indexing skipped: no Go chunks found in %s", repo_path)
        return collection

    ids = [_chunk_id(chunk) for chunk in chunks]
    documents = [chunk.text for chunk in chunks]
    metadatas = [
        {
            "path": chunk.path,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
        }
        for chunk in chunks
    ]

    batch_size = 32
    for start in range(0, len(chunks), batch_size):
        end = start + batch_size
        embeddings = _embed_texts(documents[start:end], settings)
        collection.upsert(
            ids=ids[start:end],
            documents=documents[start:end],
            embeddings=embeddings,
            metadatas=metadatas[start:end],
        )

    (index_dir / _INDEX_META).write_text(
        json.dumps(
            {
                "repo": repo,
                "repo_head": repo_head,
                "chunk_count": len(chunks),
                "embed_provider": settings.rag_embed_provider,
                "embed_model": settings.rag_embed_model,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    log.info("RAG index built for %s: %d chunks at %s", repo, len(chunks), index_dir)
    return collection


def retrieve_chunks(
    collection: Any,
    query: str,
    settings: Settings,
) -> list[RagHit]:
    """Retrieve top-k chunks for a query."""
    if collection.count() == 0:
        return []

    query_embedding = _embed_texts([query], settings)[0]
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(settings.rag_top_k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    hits: list[RagHit] = []
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    for document, metadata, distance in zip(documents, metadatas, distances, strict=True):
        score = max(0.0, 1.0 - float(distance))
        if score < settings.rag_min_score:
            continue
        start_line = int(metadata["start_line"])
        end_line = int(metadata["end_line"])
        path = str(metadata["path"])
        line_text = document.splitlines()[0] if document else ""
        hits.append(
            RagHit(
                path=path,
                line_number=start_line,
                line_text=line_text,
                query=query,
                score=score,
                chunk_start=start_line,
                chunk_end=end_line,
            )
        )

    return hits


def rag_hits_to_search_hits(hits: list[RagHit]) -> list[SearchHit]:
    """Adapt RAG hits to SearchHit for the existing context pipeline."""
    return [
        SearchHit(
            path=hit.path,
            line_number=hit.line_number,
            line_text=hit.line_text,
            query=f"{RAG_QUERY_PREFIX}{hit.query[:120]}",
        )
        for hit in hits
    ]


def merge_search_hits(primary: list[SearchHit], secondary: list[SearchHit]) -> list[SearchHit]:
    """Merge search hits, preferring ripgrep results on duplicate path/line."""
    merged: dict[tuple[str, int], SearchHit] = {}
    for hit in primary:
        merged[(hit.path, hit.line_number)] = hit
    for hit in secondary:
        key = (hit.path, hit.line_number)
        if key not in merged:
            merged[key] = hit
    return list(merged.values())


def retrieve_rag_hits(
    repo_path: Path,
    issue: IssueContext,
    repo: str,
    settings: Settings,
    logger: logging.Logger | None = None,
) -> list[RagHit]:
    """Retrieve semantic hits when RAG is enabled; otherwise return an empty list."""
    log = logger or logging.getLogger("go_agent")
    if not settings.enable_rag:
        return []

    query = build_rag_query(issue)
    try:
        repo_head = _resolve_repo_head(repo_path)
        collection = get_or_build_index(repo, repo_path, repo_head, settings, logger=log)
        hits = retrieve_chunks(collection, query, settings)
    except RagDepsNotFoundError as exc:
        log.warning("%s", exc)
        return []
    except Exception as exc:
        log.warning("RAG retrieval failed: %s", exc)
        return []

    log.info("RAG retrieval: %d hits for %s", len(hits), repo)
    return hits


def write_rag_hits(
    ctx: RunContext,
    issue: IssueContext,
    query: str,
    hits: list[RagHit],
) -> Path:
    artifact = RagArtifact(
        issue_number=issue.number,
        repo=issue.repo,
        query=query,
        hits=hits,
    )
    path = ctx.artifact_dir / "rag_hits.json"
    path.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
