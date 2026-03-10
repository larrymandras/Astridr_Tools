"""Document query tool — ask questions about document contents with citations.

Supports .txt, .md, .pdf, and .docx files. Uses keyword-based relevance
scoring to find the most relevant chunks, then returns them with source
citations for the parent agent to formulate an answer.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()


class DocumentQueryTool(BaseTool):
    """Query documents for answers with source citations."""

    name = "query_document"
    description = "Ask questions about document contents with source citations"
    approval_tier = "read_only"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "document_path": {
                "type": "string",
                "description": "Path to document file.",
            },
            "query": {
                "type": "string",
                "description": "Question to ask about the document.",
            },
            "max_chunks": {
                "type": "integer",
                "description": "Max context chunks to use.",
                "default": 5,
            },
        },
        "required": ["document_path", "query"],
        "additionalProperties": False,
    }

    SUPPORTED_FORMATS = {".txt", ".md", ".pdf", ".docx"}

    # Maximum document size (50MB)
    MAX_FILE_SIZE = 50 * 1024 * 1024

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 100,
    ) -> None:
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    # ------------------------------------------------------------------
    # BaseTool.execute
    # ------------------------------------------------------------------

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Load document, chunk text, find relevant chunks, return context."""
        document_path: str = kwargs.get("document_path", "")
        query: str = kwargs.get("query", "")
        max_chunks: int = kwargs.get("max_chunks", 5)

        if not document_path:
            return ToolResult(
                success=False,
                error="Missing required parameter: document_path",
            )
        if not query:
            return ToolResult(
                success=False,
                error="Missing required parameter: query",
            )

        # Validate document
        try:
            validated = self._validate_document(document_path)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        # Load document text
        try:
            text = await self._load_document(validated)
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Failed to load document: {exc}",
            )

        if not text.strip():
            return ToolResult(
                success=False,
                error="Document is empty or contains no extractable text",
            )

        # Chunk into overlapping segments
        chunks = self._chunk_text(text)

        # Find most relevant chunks
        relevant = self._find_relevant_chunks(chunks, query, max_chunks)

        # Format output
        output_parts = [
            f"## Query: {query}",
            f"**Document:** {document_path}",
            f"**Relevant chunks:** {len(relevant)} of {len(chunks)}",
            "",
        ]

        for i, chunk in enumerate(relevant, 1):
            output_parts.append(f"### Chunk {i} (score: {chunk['score']:.2f})")
            output_parts.append(f"*Characters {chunk['start']}-{chunk['end']}*")
            output_parts.append("")
            output_parts.append(chunk["content"])
            output_parts.append("")

        output = "\n".join(output_parts)

        log.info(
            "document_query.complete",
            document_path=document_path,
            total_chunks=len(chunks),
            relevant_chunks=len(relevant),
        )

        return ToolResult(
            success=True,
            output=output,
            data={
                "query": query,
                "document_path": document_path,
                "total_chunks": len(chunks),
                "relevant_chunks": [
                    {
                        "content": c["content"],
                        "start": c["start"],
                        "end": c["end"],
                        "chunk_index": c["chunk_index"],
                        "score": c["score"],
                    }
                    for c in relevant
                ],
            },
        )

    # ------------------------------------------------------------------
    # Document loading
    # ------------------------------------------------------------------

    async def _load_document(self, path: Path) -> str:
        """Route to format-specific loader.

        Args:
            path: Validated path to the document.

        Returns:
            Extracted text content.
        """
        suffix = path.suffix.lower()
        loaders = {
            ".txt": self._load_txt,
            ".md": self._load_md,
            ".pdf": self._load_pdf,
            ".docx": self._load_docx,
        }
        loader = loaders.get(suffix)
        if loader is None:
            raise ValueError(f"Unsupported format: {suffix}")
        return await loader(path)

    async def _load_txt(self, path: Path) -> str:
        """Load plain text file."""
        return path.read_text(encoding="utf-8")

    async def _load_md(self, path: Path) -> str:
        """Load markdown file (treated as plain text)."""
        return path.read_text(encoding="utf-8")

    async def _load_pdf(self, path: Path) -> str:
        """Extract text from PDF via PyPDF2."""
        from PyPDF2 import PdfReader

        reader = PdfReader(str(path))
        parts: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            parts.append(text)
        return "\n\n".join(parts)

    async def _load_docx(self, path: Path) -> str:
        """Extract text from DOCX via python-docx."""
        import docx

        doc = docx.Document(str(path))
        parts: list[str] = []
        for paragraph in doc.paragraphs:
            if paragraph.text:
                parts.append(paragraph.text)
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    def _chunk_text(self, text: str) -> list[dict[str, Any]]:
        """Split text into overlapping chunks with metadata.

        Returns:
            List of dicts with content, start, end, and chunk_index.
        """
        chunks: list[dict[str, Any]] = []
        text_len = len(text)

        if text_len == 0:
            return chunks

        step = max(1, self._chunk_size - self._chunk_overlap)
        chunk_index = 0
        pos = 0

        while pos < text_len:
            end = min(pos + self._chunk_size, text_len)
            chunk_content = text[pos:end]

            chunks.append({
                "content": chunk_content,
                "start": pos,
                "end": end,
                "chunk_index": chunk_index,
            })

            chunk_index += 1
            pos += step

            # If we've reached the end, stop
            if end >= text_len:
                break

        return chunks

    # ------------------------------------------------------------------
    # Relevance scoring
    # ------------------------------------------------------------------

    def _find_relevant_chunks(
        self,
        chunks: list[dict[str, Any]],
        query: str,
        max_chunks: int = 5,
    ) -> list[dict[str, Any]]:
        """Find the most relevant chunks using keyword-based scoring.

        Score = number of unique query words found in the chunk (case-insensitive).

        Args:
            chunks: List of text chunks with metadata.
            query: The search query.
            max_chunks: Maximum number of chunks to return.

        Returns:
            Top-scoring chunks sorted by score (descending), each with
            an added 'score' field.
        """
        # Tokenize query into unique lowercase words
        query_words = set(re.findall(r"\w+", query.lower()))

        if not query_words:
            # No meaningful query words — return first max_chunks
            for chunk in chunks[:max_chunks]:
                chunk["score"] = 0.0
            return chunks[:max_chunks]

        scored: list[dict[str, Any]] = []
        for chunk in chunks:
            chunk_lower = chunk["content"].lower()
            # Count how many unique query words appear in the chunk
            matches = sum(1 for word in query_words if word in chunk_lower)
            score = matches / len(query_words) if query_words else 0.0
            scored.append({**chunk, "score": score})

        # Sort by score descending, then by chunk_index for stability
        scored.sort(key=lambda c: (-c["score"], c["chunk_index"]))

        return scored[:max_chunks]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_document(self, path: str) -> Path:
        """Validate that a document file exists and is supported.

        Args:
            path: Path to the document file.

        Returns:
            Resolved Path object.

        Raises:
            ValueError: If the file doesn't exist, format is unsupported,
                or file is too large.
        """
        p = Path(path)

        if not p.exists():
            raise ValueError(f"File not found: {path}")

        if not p.is_file():
            raise ValueError(f"Not a file: {path}")

        suffix = p.suffix.lower()
        if suffix not in self.SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported format: {suffix}. "
                f"Supported: {', '.join(sorted(self.SUPPORTED_FORMATS))}"
            )

        size = p.stat().st_size
        if size > self.MAX_FILE_SIZE:
            raise ValueError(
                f"File too large ({size} bytes, max {self.MAX_FILE_SIZE}): {path}"
            )

        return p
