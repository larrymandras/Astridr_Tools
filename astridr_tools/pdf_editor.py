"""PDF editor tool — merge, split, extract text, add watermarks, rotate pages.

Provides PDF manipulation capabilities using PyPDF2 and reportlab. All file
writes go through the engine's atomic_io module for crash safety.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()


class PDFEditorTool(BaseTool):
    """Edit PDFs: merge, split, extract text, add watermarks."""

    name = "edit_pdf"
    description = "Edit PDF files: merge, split, extract text, add watermarks, rotate pages"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "extract_text",
                    "merge",
                    "split",
                    "add_watermark",
                    "rotate",
                    "delete_pages",
                    "get_info",
                ],
                "description": "The PDF operation to perform.",
            },
            "pdf_path": {
                "type": "string",
                "description": "Path to input PDF.",
            },
            "output_path": {
                "type": "string",
                "description": "Path for output PDF.",
            },
            "pages": {
                "type": "string",
                "description": "Page range (e.g., '1-5', '1,3,5').",
            },
            "watermark_text": {
                "type": "string",
                "description": "Watermark text to add.",
            },
            "merge_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "PDF paths to merge.",
            },
            "rotation": {
                "type": "integer",
                "description": "Rotation angle (90, 180, 270).",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    SUPPORTED_ROTATIONS = {90, 180, 270}

    # Maximum PDF file size (100MB)
    MAX_FILE_SIZE = 100 * 1024 * 1024

    # ------------------------------------------------------------------
    # BaseTool.execute
    # ------------------------------------------------------------------

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Dispatch to the requested PDF action."""
        action: str = kwargs.get("action", "")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")

        dispatch = {
            "extract_text": self._extract_text,
            "merge": self._merge,
            "split": self._split,
            "add_watermark": self._add_watermark,
            "rotate": self._rotate,
            "delete_pages": self._delete_pages,
            "get_info": self._get_info,
        }

        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except Exception as exc:
            log.error("pdf_editor.error", action=action, error=str(exc))
            return ToolResult(success=False, error=f"PDF operation failed: {exc}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _extract_text(self, **kwargs: Any) -> ToolResult:
        """Extract text from PDF pages."""
        pdf_path = kwargs.get("pdf_path", "")
        if not pdf_path:
            return ToolResult(success=False, error="Missing required parameter: pdf_path")

        try:
            validated = self._validate_pdf(pdf_path)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        from PyPDF2 import PdfReader

        reader = PdfReader(str(validated))
        total_pages = len(reader.pages)

        pages_str = kwargs.get("pages")
        if pages_str:
            try:
                page_indices = self._parse_page_range(pages_str, total_pages)
            except ValueError as exc:
                return ToolResult(success=False, error=str(exc))
        else:
            page_indices = list(range(total_pages))

        text_parts: list[str] = []
        for idx in page_indices:
            page = reader.pages[idx]
            page_text = page.extract_text() or ""
            text_parts.append(f"--- Page {idx + 1} ---\n{page_text}")

        output = "\n\n".join(text_parts)
        log.info(
            "pdf_editor.extract_text",
            pdf_path=pdf_path,
            pages_extracted=len(page_indices),
        )
        return ToolResult(
            success=True,
            output=output,
            data={
                "pages_extracted": len(page_indices),
                "total_pages": total_pages,
            },
        )

    async def _merge(self, **kwargs: Any) -> ToolResult:
        """Merge multiple PDFs into one."""
        merge_paths: list[str] = kwargs.get("merge_paths", [])
        output_path: str = kwargs.get("output_path", "")

        if not merge_paths or len(merge_paths) < 2:
            return ToolResult(
                success=False,
                error="merge_paths must contain at least 2 PDF paths",
            )
        if not output_path:
            return ToolResult(success=False, error="Missing required parameter: output_path")

        # Validate all input PDFs
        for path_str in merge_paths:
            try:
                self._validate_pdf(path_str)
            except ValueError as exc:
                return ToolResult(success=False, error=str(exc))

        from PyPDF2 import PdfMerger

        merger = PdfMerger()
        try:
            for path_str in merge_paths:
                merger.append(path_str)

            # Ensure output directory exists
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            merger.write(str(out))
        finally:
            merger.close()

        log.info(
            "pdf_editor.merge",
            input_count=len(merge_paths),
            output_path=output_path,
        )
        return ToolResult(
            success=True,
            output=f"Merged {len(merge_paths)} PDFs into {output_path}",
            data={
                "input_count": len(merge_paths),
                "output_path": output_path,
            },
        )

    async def _split(self, **kwargs: Any) -> ToolResult:
        """Extract specific pages into a new PDF."""
        pdf_path: str = kwargs.get("pdf_path", "")
        pages_str: str = kwargs.get("pages", "")
        output_path: str = kwargs.get("output_path", "")

        if not pdf_path:
            return ToolResult(success=False, error="Missing required parameter: pdf_path")
        if not pages_str:
            return ToolResult(success=False, error="Missing required parameter: pages")
        if not output_path:
            return ToolResult(success=False, error="Missing required parameter: output_path")

        try:
            validated = self._validate_pdf(pdf_path)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        from PyPDF2 import PdfReader, PdfWriter

        reader = PdfReader(str(validated))
        total_pages = len(reader.pages)

        try:
            page_indices = self._parse_page_range(pages_str, total_pages)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        writer = PdfWriter()
        for idx in page_indices:
            writer.add_page(reader.pages[idx])

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(str(out), "wb") as f:
            writer.write(f)

        log.info(
            "pdf_editor.split",
            pdf_path=pdf_path,
            pages=pages_str,
            output_path=output_path,
        )
        return ToolResult(
            success=True,
            output=f"Split pages {pages_str} from {pdf_path} into {output_path}",
            data={
                "pages_extracted": len(page_indices),
                "output_path": output_path,
            },
        )

    async def _add_watermark(self, **kwargs: Any) -> ToolResult:
        """Add text watermark to all pages."""
        pdf_path: str = kwargs.get("pdf_path", "")
        watermark_text: str = kwargs.get("watermark_text", "")
        output_path: str = kwargs.get("output_path", "")

        if not pdf_path:
            return ToolResult(success=False, error="Missing required parameter: pdf_path")
        if not watermark_text:
            return ToolResult(
                success=False,
                error="Missing required parameter: watermark_text",
            )
        if not output_path:
            return ToolResult(success=False, error="Missing required parameter: output_path")

        try:
            validated = self._validate_pdf(pdf_path)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        # Create watermark PDF page using reportlab
        watermark_pdf = self._create_watermark_page(watermark_text)

        from PyPDF2 import PdfReader, PdfWriter

        reader = PdfReader(str(validated))
        watermark_reader = PdfReader(watermark_pdf)
        watermark_page = watermark_reader.pages[0]

        writer = PdfWriter()
        for page in reader.pages:
            page.merge_page(watermark_page)
            writer.add_page(page)

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(str(out), "wb") as f:
            writer.write(f)

        log.info(
            "pdf_editor.add_watermark",
            pdf_path=pdf_path,
            output_path=output_path,
        )
        return ToolResult(
            success=True,
            output=f"Added watermark '{watermark_text}' to {pdf_path} -> {output_path}",
            data={
                "pages_watermarked": len(reader.pages),
                "output_path": output_path,
            },
        )

    async def _rotate(self, **kwargs: Any) -> ToolResult:
        """Rotate specified pages."""
        pdf_path: str = kwargs.get("pdf_path", "")
        rotation: int | None = kwargs.get("rotation")
        output_path: str = kwargs.get("output_path", "")

        if not pdf_path:
            return ToolResult(success=False, error="Missing required parameter: pdf_path")
        if rotation is None:
            return ToolResult(success=False, error="Missing required parameter: rotation")
        if not output_path:
            return ToolResult(success=False, error="Missing required parameter: output_path")

        if rotation not in self.SUPPORTED_ROTATIONS:
            return ToolResult(
                success=False,
                error=f"Invalid rotation angle: {rotation}. Must be one of {sorted(self.SUPPORTED_ROTATIONS)}",
            )

        try:
            validated = self._validate_pdf(pdf_path)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        from PyPDF2 import PdfReader, PdfWriter

        reader = PdfReader(str(validated))
        total_pages = len(reader.pages)

        pages_str = kwargs.get("pages")
        if pages_str:
            try:
                page_indices = self._parse_page_range(pages_str, total_pages)
            except ValueError as exc:
                return ToolResult(success=False, error=str(exc))
        else:
            page_indices = list(range(total_pages))

        writer = PdfWriter()
        for idx in range(total_pages):
            page = reader.pages[idx]
            if idx in page_indices:
                page.rotate(rotation)
            writer.add_page(page)

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(str(out), "wb") as f:
            writer.write(f)

        log.info(
            "pdf_editor.rotate",
            pdf_path=pdf_path,
            rotation=rotation,
            pages_rotated=len(page_indices),
            output_path=output_path,
        )
        return ToolResult(
            success=True,
            output=f"Rotated {len(page_indices)} pages by {rotation}\u00b0 in {output_path}",
            data={
                "pages_rotated": len(page_indices),
                "rotation": rotation,
                "output_path": output_path,
            },
        )

    async def _delete_pages(self, **kwargs: Any) -> ToolResult:
        """Remove specified pages from PDF."""
        pdf_path: str = kwargs.get("pdf_path", "")
        pages_str: str = kwargs.get("pages", "")
        output_path: str = kwargs.get("output_path", "")

        if not pdf_path:
            return ToolResult(success=False, error="Missing required parameter: pdf_path")
        if not pages_str:
            return ToolResult(success=False, error="Missing required parameter: pages")
        if not output_path:
            return ToolResult(success=False, error="Missing required parameter: output_path")

        try:
            validated = self._validate_pdf(pdf_path)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        from PyPDF2 import PdfReader, PdfWriter

        reader = PdfReader(str(validated))
        total_pages = len(reader.pages)

        try:
            pages_to_delete = set(self._parse_page_range(pages_str, total_pages))
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        writer = PdfWriter()
        kept = 0
        for idx in range(total_pages):
            if idx not in pages_to_delete:
                writer.add_page(reader.pages[idx])
                kept += 1

        if kept == 0:
            return ToolResult(
                success=False,
                error="Cannot delete all pages from PDF",
            )

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(str(out), "wb") as f:
            writer.write(f)

        log.info(
            "pdf_editor.delete_pages",
            pdf_path=pdf_path,
            pages_deleted=len(pages_to_delete),
            pages_remaining=kept,
            output_path=output_path,
        )
        return ToolResult(
            success=True,
            output=f"Deleted {len(pages_to_delete)} pages, {kept} remaining in {output_path}",
            data={
                "pages_deleted": len(pages_to_delete),
                "pages_remaining": kept,
                "output_path": output_path,
            },
        )

    async def _get_info(self, **kwargs: Any) -> ToolResult:
        """Return PDF metadata: pages, size, author, title."""
        pdf_path: str = kwargs.get("pdf_path", "")
        if not pdf_path:
            return ToolResult(success=False, error="Missing required parameter: pdf_path")

        try:
            validated = self._validate_pdf(pdf_path)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        from PyPDF2 import PdfReader

        reader = PdfReader(str(validated))
        metadata = reader.metadata

        info: dict[str, Any] = {
            "pages": len(reader.pages),
            "size_bytes": validated.stat().st_size,
        }

        if metadata:
            info["title"] = metadata.get("/Title", "") or ""
            info["author"] = metadata.get("/Author", "") or ""
            info["subject"] = metadata.get("/Subject", "") or ""
            info["creator"] = metadata.get("/Creator", "") or ""

        output_lines = [
            f"**Pages:** {info['pages']}",
            f"**Size:** {info['size_bytes']} bytes",
        ]
        if info.get("title"):
            output_lines.append(f"**Title:** {info['title']}")
        if info.get("author"):
            output_lines.append(f"**Author:** {info['author']}")
        if info.get("subject"):
            output_lines.append(f"**Subject:** {info['subject']}")

        log.info("pdf_editor.get_info", pdf_path=pdf_path, pages=info["pages"])
        return ToolResult(
            success=True,
            output="\n".join(output_lines),
            data=info,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_page_range(pages_str: str, total_pages: int) -> list[int]:
        """Parse page range string into list of 0-indexed page numbers.

        Supports formats like "1-5", "1,3,5", "1-3,7,9-10".
        Page numbers in the string are 1-indexed (human-friendly).

        Args:
            pages_str: Page range string.
            total_pages: Total number of pages in the PDF.

        Returns:
            Sorted list of unique 0-indexed page numbers.

        Raises:
            ValueError: If the page range is invalid.
        """
        if not pages_str or not pages_str.strip():
            raise ValueError("Empty page range")

        indices: set[int] = set()

        for part in pages_str.split(","):
            part = part.strip()
            if not part:
                continue

            # Check for range pattern: digits-digits (but not negative numbers)
            range_match = re.match(r"^(\d+)\s*-\s*(\d+)$", part)
            if range_match:
                start = int(range_match.group(1))
                end = int(range_match.group(2))

                if start < 1 or end < 1:
                    raise ValueError(f"Page numbers must be positive: {part}")
                if start > total_pages or end > total_pages:
                    raise ValueError(
                        f"Page number out of range (max {total_pages}): {part}"
                    )
                if start > end:
                    raise ValueError(f"Invalid range (start > end): {part}")

                for i in range(start, end + 1):
                    indices.add(i - 1)  # Convert to 0-indexed
            else:
                try:
                    page_num = int(part)
                except ValueError:
                    raise ValueError(f"Invalid page number: {part}")

                if page_num < 1:
                    raise ValueError(f"Page numbers must be positive: {part}")
                if page_num > total_pages:
                    raise ValueError(
                        f"Page number out of range (max {total_pages}): {part}"
                    )
                indices.add(page_num - 1)  # Convert to 0-indexed

        return sorted(indices)

    def _validate_pdf(self, path: str) -> Path:
        """Validate that a PDF file exists and is reasonable.

        Args:
            path: Path to the PDF file.

        Returns:
            Resolved Path object.

        Raises:
            ValueError: If the file doesn't exist, isn't a PDF, or is too large.
        """
        p = Path(path)

        if not p.exists():
            raise ValueError(f"File not found: {path}")

        if not p.is_file():
            raise ValueError(f"Not a file: {path}")

        if p.suffix.lower() != ".pdf":
            raise ValueError(f"Not a PDF file: {path}")

        size = p.stat().st_size
        if size > self.MAX_FILE_SIZE:
            raise ValueError(
                f"File too large ({size} bytes, max {self.MAX_FILE_SIZE}): {path}"
            )

        return p

    @staticmethod
    def _create_watermark_page(text: str) -> io.BytesIO:
        """Create a single-page PDF with watermark text using reportlab.

        Args:
            text: Watermark text to render.

        Returns:
            BytesIO buffer containing the watermark PDF.
        """
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter

        # Semi-transparent diagonal watermark
        c.saveState()
        c.setFillAlpha(0.3)
        c.setFont("Helvetica", 50)
        c.translate(width / 2, height / 2)
        c.rotate(45)
        c.drawCentredString(0, 0, text)
        c.restoreState()
        c.save()

        buffer.seek(0)
        return buffer
