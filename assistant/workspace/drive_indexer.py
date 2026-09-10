"""Bi-directional Google Drive Semantic Indexer.

Synchronizes Google Drive documents with local ChromaDB vector memory,
supporting:
- Incremental Pull/Ingest: Scans Drive, detects new/updated files, chunks text,
  and indexes into ChromaDB with SQLite state tracking.
- Stale Chunk Pruning: Automatically deletes embeddings for removed Drive files.
- Natural Language Semantic Search: Vector similarity queries against indexed Drive content.
- Bi-directional Push/Export: Uploads documents to Google Drive and indexes them immediately.
- Graceful Demo/Offline Mode: Operates seamlessly using mock data when Google OAuth is inactive.
"""

import logging
import time
import re
import threading
from typing import List, Dict, Any, Optional

from assistant.workspace.drive import list_drive_files, read_drive_file, upload_drive_file, _mock_drive_files
from assistant.control.store import ControlStore

logger = logging.getLogger(__name__)

# Drive collection name in ChromaDB
COLLECTION_NAME = "google_drive_index"

_drive_collection = None
_indexer_initialized = False
_drive_collection_lock = threading.Lock()


def _get_drive_collection():
    """Initializes or retrieves the persistent ChromaDB collection for Drive documents."""
    global _drive_collection, _indexer_initialized
    if _drive_collection is not None:
        return _drive_collection

    with _drive_collection_lock:
        if _drive_collection is not None:
            return _drive_collection
        try:
            from assistant.memory import chroma_client, _memory_enabled
            if _memory_enabled and chroma_client is not None:
                _drive_collection = chroma_client.get_or_create_collection(
                    name=COLLECTION_NAME,
                    metadata={"hnsw:space": "cosine"}
                )
                _indexer_initialized = True
                return _drive_collection
        except Exception as e:
            logger.debug("ChromaDB not available for drive indexer: %s", e)

    return None


def chunk_text(text: str, chunk_size: int = 600, overlap: int = 100) -> List[str]:
    """Splits text into overlapping chunks respecting sentence/line boundaries."""
    if not text or not text.strip():
        return []

    text = re.sub(r"\r\n", "\n", text).strip()
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:].strip())
            break

        # Attempt to break on newline or sentence boundary
        split_idx = text.rfind("\n", start + chunk_size // 2, end)
        if split_idx == -1:
            split_idx = text.rfind(". ", start + chunk_size // 2, end)
            if split_idx != -1:
                split_idx += 1  # include period

        if split_idx == -1 or split_idx <= start:
            split_idx = end

        chunk = text[start:split_idx].strip()
        if chunk:
            chunks.append(chunk)

        start = max(split_idx, start + chunk_size - overlap)

    return [c for c in chunks if c]


class DriveSemanticIndexer:
    """Orchestrates bi-directional synchronization and semantic querying for Google Drive."""

    def __init__(self, store: Optional[ControlStore] = None):
        self._store = store

    @property
    def store(self) -> ControlStore:
        if self._store is None:
            self._store = ControlStore()
        return self._store

    @store.setter
    def store(self, value: Optional[ControlStore]):
        self._store = value

    def sync_drive_index(self, full_reindex: bool = False, limit: int = 50) -> Dict[str, Any]:
        """Scans Google Drive, ingests updated files, and purges deleted files.

        Returns a detailed sync summary dictionary.
        """
        drive_files = list_drive_files(limit=limit)
        current_sync_files = {f["file_id"]: f for f in self.store.list_drive_sync_files()}

        collection = _get_drive_collection()
        indexed_count = 0
        updated_count = 0
        deleted_count = 0
        total_chunks_added = 0
        seen_file_ids = set()

        for file_meta in drive_files:
            file_id = file_meta.get("id")
            if not file_id:
                continue

            seen_file_ids.add(file_id)
            name = file_meta.get("name", "Untitled")
            mime_type = file_meta.get("mimeType", "text/plain")
            modified_time = file_meta.get("modifiedTime", "")
            web_view_link = file_meta.get("webViewLink", "")

            # Check if file has been modified since last sync
            existing_record = current_sync_files.get(file_id)
            is_new = existing_record is None
            has_changed = (
                is_new
                or full_reindex
                or (modified_time and modified_time != existing_record.get("modified_time"))
            )

            if not has_changed:
                continue

            # Fetch file content
            content = file_meta.get("content", "")
            if not content:
                file_data = read_drive_file(file_id)
                content = file_data.get("content", "")

            chunks = chunk_text(content)
            now = time.time()

            # Remove old chunks for this file if updating
            if collection is not None and not is_new:
                try:
                    collection.delete(where={"file_id": {"$eq": file_id}})
                except Exception:
                    try:
                        old_chunk_count = existing_record.get("chunk_count", 0)
                        old_ids = [f"drive_{file_id}_chunk_{i}" for i in range(old_chunk_count)]
                        if old_ids:
                            collection.delete(ids=old_ids)
                    except Exception as e:
                        logger.debug("Failed removing old chunks for %s: %s", file_id, e)

            if not chunks:
                logger.warning("No indexable text chunks extracted for Drive file %s (%s)", file_id, name)

            # Ingest new chunks into ChromaDB
            if collection is not None and chunks:
                chunk_ids = [f"drive_{file_id}_chunk_{i}" for i in range(len(chunks))]
                metadatas = [
                    {
                        "file_id": file_id,
                        "name": name,
                        "mime_type": mime_type,
                        "web_view_link": web_view_link,
                        "modified_time": modified_time,
                        "chunk_index": i,
                    }
                    for i in range(len(chunks))
                ]
                try:
                    collection.upsert(
                        documents=chunks,
                        ids=chunk_ids,
                        metadatas=metadatas,
                    )
                except Exception as e:
                    logger.error("Failed indexing chunks into ChromaDB for %s: %s", file_id, e)

            # Record in SQLite store
            self.store.save_drive_sync_file(
                file_id=file_id,
                name=name,
                mime_type=mime_type,
                modified_time=modified_time,
                last_indexed_at=now,
                chunk_count=len(chunks),
                web_view_link=web_view_link,
            )

            if is_new:
                indexed_count += 1
            else:
                updated_count += 1
            total_chunks_added += len(chunks)

        # Detect and delete removed Drive files
        for stale_file_id, stale_record in current_sync_files.items():
            if stale_file_id not in seen_file_ids:
                if collection is not None:
                    try:
                        collection.delete(where={"file_id": {"$eq": stale_file_id}})
                    except Exception:
                        try:
                            old_count = stale_record.get("chunk_count", 0)
                            old_ids = [f"drive_{stale_file_id}_chunk_{i}" for i in range(old_count)]
                            if old_ids:
                                collection.delete(ids=old_ids)
                        except Exception as e:
                            logger.debug("Failed deleting stale chunks for %s: %s", stale_file_id, e)
                self.store.delete_drive_sync_file(stale_file_id)
                deleted_count += 1

        summary = self.store.get_drive_sync_summary()
        return {
            "status": "success",
            "indexed": indexed_count,
            "updated": updated_count,
            "deleted": deleted_count,
            "chunks_added": total_chunks_added,
            "total_files": summary.get("total_files", 0),
            "total_chunks": summary.get("total_chunks", 0),
            "last_sync_at": summary.get("last_sync_at", 0.0),
        }

    def semantic_search(self, query: str, limit: int = 5, mime_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Queries the vector index for semantically similar Drive content."""
        if not query or not query.strip():
            return []

        collection = _get_drive_collection()
        if collection is not None and collection.count() > 0:
            try:
                where_filter = {"mime_type": {"$eq": mime_type}} if mime_type else None
                results = collection.query(
                    query_texts=[query],
                    n_results=min(limit, collection.count()),
                    where=where_filter,
                )
                docs = results.get("documents", [[]])[0]
                metas = results.get("metadatas", [[]])[0]
                distances = results.get("distances", [[]])[0] if "distances" in results else []

                matches = []
                for idx, doc in enumerate(docs):
                    meta = metas[idx] if idx < len(metas) else {}
                    distance = distances[idx] if idx < len(distances) else 0.0
                    score = round(max(0.0, 1.0 - float(distance)), 3)
                    matches.append({
                        "file_id": meta.get("file_id", ""),
                        "name": meta.get("name", "Untitled"),
                        "mime_type": meta.get("mime_type", ""),
                        "snippet": doc,
                        "relevance": score,
                        "web_view_link": meta.get("web_view_link", ""),
                        "chunk_index": meta.get("chunk_index", 0),
                    })
                return matches
            except Exception as e:
                logger.error("ChromaDB semantic search failed: %s", e)

        # Fallback keyword match across mock or indexed files if ChromaDB is not populated
        q_lower = query.lower()
        fallback_matches = []
        indexed_files = self.store.list_drive_sync_files()
        candidate_files = indexed_files if indexed_files else _mock_drive_files

        for f in candidate_files:
            name = f.get("name", "")
            content = f.get("content", "")
            if not content and "file_id" in f:
                d_info = read_drive_file(f["file_id"])
                content = d_info.get("content", "")

            if q_lower in name.lower() or q_lower in content.lower():
                fallback_matches.append({
                    "file_id": f.get("file_id") or f.get("id", ""),
                    "name": name,
                    "mime_type": f.get("mime_type") or f.get("mimeType", "text/plain"),
                    "snippet": content[:300] if content else f"Matched keyword '{query}' in file {name}.",
                    "relevance": 0.75,
                    "web_view_link": f.get("web_view_link") or f.get("webViewLink", ""),
                    "chunk_index": 0,
                })

        return fallback_matches[:limit]

    def export_to_drive(self, name: str, content: str, mime_type: str = "text/plain") -> Dict[str, Any]:
        """Uploads a file to Google Drive and immediately indexes it in the vector store."""
        uploaded = upload_drive_file(name=name, content=content, mime_type=mime_type)
        file_id = uploaded.get("id")

        if not file_id:
            return {"error": uploaded.get("error", "Upload failed"), "indexed": False}

        now = time.time()
        modified_time = uploaded.get("modifiedTime") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        web_view_link = uploaded.get("webViewLink", "")
        chunks = chunk_text(content)

        collection = _get_drive_collection()
        if collection is not None and chunks:
            chunk_ids = [f"drive_{file_id}_chunk_{i}" for i in range(len(chunks))]
            metadatas = [
                {
                    "file_id": file_id,
                    "name": name,
                    "mime_type": mime_type,
                    "web_view_link": web_view_link,
                    "modified_time": modified_time,
                    "chunk_index": i,
                }
                for i in range(len(chunks))
            ]
            try:
                collection.upsert(documents=chunks, ids=chunk_ids, metadatas=metadatas)
            except Exception as e:
                logger.error("Failed indexing exported file into ChromaDB: %s", e)

        self.store.save_drive_sync_file(
            file_id=file_id,
            name=name,
            mime_type=mime_type,
            modified_time=modified_time,
            last_indexed_at=now,
            chunk_count=len(chunks),
            web_view_link=web_view_link,
        )

        return {
            "status": "success",
            "file_id": file_id,
            "name": name,
            "mime_type": mime_type,
            "chunks_indexed": len(chunks),
            "web_view_link": web_view_link,
            "indexed": True,
        }


# Global instance
indexer = DriveSemanticIndexer()
