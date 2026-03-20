"""Load and decompress Draw.io (.drawio) XML files.

This module handles the full lifecycle of reading a .drawio file:
  1. Parse the outer XML (mxfile/diagram structure).
  2. Detect whether diagram content is compressed or inline XML.
  3. Decompress if needed (base64 -> inflate -> URL-decode).
  4. Return the ``<mxGraphModel>`` element ready for downstream processing.

Security: All XML parsing uses ``defusedxml`` to prevent XXE attacks.
"""

from __future__ import annotations

import base64
import urllib.parse
import zlib
from pathlib import Path
from typing import Union

import defusedxml.ElementTree as DET


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class DrawioLoadError(Exception):
    """Raised when a .drawio file cannot be loaded or parsed."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_drawio(
    file_path: Union[str, Path],
    page: Union[str, int, None] = None,
) -> DET.Element:
    """Load a .drawio file and return the ``<mxGraphModel>`` element.

    Parameters
    ----------
    file_path:
        Path to the ``.drawio`` file on disk.
    page:
        Optional page selector.  Pass a ``str`` to select by page *name*,
        an ``int`` to select by zero-based *index*, or ``None`` (default) to
        use the first page.

    Returns
    -------
    xml.etree.ElementTree.Element
        The ``<mxGraphModel>`` element containing the full graph.

    Raises
    ------
    DrawioLoadError
        If the file cannot be found, parsed, or the requested page is
        missing.
    """
    file_path = Path(file_path)
    tree = _parse_file(file_path)
    root = tree.getroot()

    diagram = _select_diagram(root, page)
    return _extract_graph_model(diagram)


def decompress_diagram(content: str) -> str:
    """Decompress Draw.io compressed diagram content.

    Draw.io stores compressed diagrams as::

        base64( deflate( url_encode(xml) ) )

    This function reverses that pipeline.

    Parameters
    ----------
    content:
        The base64-encoded compressed string from a ``<diagram>`` element.

    Returns
    -------
    str
        The raw XML string (typically an ``<mxGraphModel>`` document).

    Raises
    ------
    DrawioLoadError
        If decompression fails at any stage.
    """
    try:
        decoded = base64.b64decode(content)
    except Exception as exc:
        raise DrawioLoadError(
            f"Failed to decompress diagram: base64 decode error: {exc}"
        ) from exc

    try:
        inflated = zlib.decompress(decoded, -zlib.MAX_WBITS)
    except Exception as exc:
        raise DrawioLoadError(
            f"Failed to decompress diagram: inflate error: {exc}"
        ) from exc

    try:
        xml_str = urllib.parse.unquote(inflated.decode("utf-8"))
    except Exception as exc:
        raise DrawioLoadError(
            f"Failed to decompress diagram: URL decode error: {exc}"
        ) from exc

    return xml_str


def is_compressed(diagram_element: DET.Element) -> bool:
    """Return ``True`` if the diagram content is compressed.

    Detection heuristic:
      - If the text content of the ``<diagram>`` element, after stripping
        whitespace, does not start with ``<``, it is compressed.
      - An empty or whitespace-only text is treated as *not* compressed
        (the diagram may have child elements instead).

    Parameters
    ----------
    diagram_element:
        An ``<diagram>`` XML element.

    Returns
    -------
    bool
    """
    text = (diagram_element.text or "").strip()
    if not text:
        return False
    return not text.startswith("<")


def get_diagram_pages(file_path: Union[str, Path]) -> list[dict]:
    """Return metadata for every page in a .drawio file.

    Parameters
    ----------
    file_path:
        Path to the ``.drawio`` file.

    Returns
    -------
    list[dict]
        Each dict contains ``index`` (int), ``id`` (str), and ``name``
        (str) for one ``<diagram>`` element in document order.

    Raises
    ------
    DrawioLoadError
        If the file cannot be found or parsed.
    """
    file_path = Path(file_path)
    tree = _parse_file(file_path)
    root = tree.getroot()

    diagrams = root.findall("diagram")
    pages: list[dict] = []
    for idx, diag in enumerate(diagrams):
        pages.append({
            "index": idx,
            "id": diag.get("id", ""),
            "name": diag.get("name", ""),
        })
    return pages


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_file(file_path: Path) -> DET.ElementTree:
    """Parse a .drawio file from disk, returning an ElementTree.

    Raises DrawioLoadError on file-not-found or invalid XML.
    """
    if file_path.is_dir():
        raise DrawioLoadError(
            f"File not found (path is a directory): {file_path}"
        )
    if not file_path.exists():
        raise DrawioLoadError(f"File not found: {file_path}")

    try:
        return DET.parse(str(file_path))
    except Exception as exc:
        raise DrawioLoadError(
            f"Invalid XML in {file_path}: {exc}"
        ) from exc


def _select_diagram(
    root: DET.Element,
    page: Union[str, int, None],
) -> DET.Element:
    """Select a ``<diagram>`` element based on *page*.

    Returns the element or raises DrawioLoadError.
    """
    diagrams = root.findall("diagram")
    if not diagrams:
        raise DrawioLoadError("No <diagram> element found in file")

    if page is None:
        return diagrams[0]

    if isinstance(page, int):
        if page < 0 or page >= len(diagrams):
            raise DrawioLoadError(
                f"Page index {page} out of range "
                f"(file has {len(diagrams)} page(s), valid: 0..{len(diagrams) - 1})"
            )
        return diagrams[page]

    # page is a string -- match by name attribute
    for diag in diagrams:
        if diag.get("name") == page:
            return diag

    available = [d.get("name", "(unnamed)") for d in diagrams]
    raise DrawioLoadError(
        f"Page '{page}' not found. Available pages: {available}"
    )


def _extract_graph_model(diagram: DET.Element) -> DET.Element:
    """Extract the ``<mxGraphModel>`` from a ``<diagram>`` element.

    Handles both compressed text content and inline child elements.
    """
    if is_compressed(diagram):
        content = (diagram.text or "").strip()
        xml_str = decompress_diagram(content)
        try:
            return DET.fromstring(xml_str)
        except Exception as exc:
            raise DrawioLoadError(
                f"Decompressed content is not valid XML: {exc}"
            ) from exc

    # Uncompressed: the mxGraphModel may be a direct child element or
    # the text content itself may be raw XML.
    child = diagram.find("mxGraphModel")
    if child is not None:
        return child

    # Try parsing the text content as XML (some exporters inline it).
    text = (diagram.text or "").strip()
    if text:
        try:
            return DET.fromstring(text)
        except Exception as exc:
            raise DrawioLoadError(
                f"Diagram text is not valid XML and not compressed: {exc}"
            ) from exc

    raise DrawioLoadError(
        "Diagram element contains neither compressed data, "
        "child <mxGraphModel>, nor parseable XML text"
    )
