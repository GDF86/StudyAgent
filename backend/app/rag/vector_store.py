from pathlib import Path
from uuid import UUID, uuid4

import faiss
import numpy as np


class VectorStore:
    """Persist one FAISS index per document; SQLite owns vector-to-chunk mapping."""

    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def path(self, document_id: str) -> Path:
        return self.directory / f"{UUID(document_id)}.faiss"

    def write(self, document_id: str, vectors: list[list[float]]) -> None:
        """Normalize vectors and atomically replace the complete index."""
        matrix = np.asarray(vectors, dtype="float32")
        if matrix.ndim != 2 or not matrix.shape[0] or not matrix.shape[1] or not np.isfinite(matrix).all():
            raise ValueError("invalid vector matrix")
        if (np.linalg.norm(matrix, axis=1) == 0).any():
            raise ValueError("zero vector")
        faiss.normalize_L2(matrix)
        index = faiss.IndexFlatIP(matrix.shape[1])
        index.add(matrix)
        # Python file I/O handles Windows Unicode paths consistently.
        temporary = self.directory / f"{uuid4()}.tmp"
        try:
            temporary.write_bytes(faiss.serialize_index(index).tobytes())
            temporary.replace(self.path(document_id))
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, document_id: str):
        return faiss.deserialize_index(np.frombuffer(self.path(document_id).read_bytes(), dtype="uint8"))

    def delete(self, document_id: str) -> None:
        self.path(document_id).unlink(missing_ok=True)
