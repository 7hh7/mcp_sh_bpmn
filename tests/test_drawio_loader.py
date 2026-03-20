"""Tests for the Draw.io XML loader module."""

import base64
import urllib.parse
import zlib
from pathlib import Path

import pytest

from mcp_sh_bpmn.drawio_loader import (
    DrawioLoadError,
    decompress_diagram,
    get_diagram_pages,
    is_compressed,
    load_drawio,
)


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

# Minimal valid mxGraphModel XML that Draw.io would produce.
MINIMAL_GRAPH_XML = (
    '<mxGraphModel>'
    '<root>'
    '<mxCell id="0"/>'
    '<mxCell id="1" parent="0"/>'
    '<mxCell id="2" value="Start" style="ellipse" vertex="1" parent="1">'
    '<mxGeometry x="100" y="100" width="80" height="80" as="geometry"/>'
    '</mxCell>'
    '</root>'
    '</mxGraphModel>'
)


def _compress_content(xml_text: str) -> str:
    """Compress XML using the Draw.io algorithm (URL-encode -> deflate -> b64).

    Mirrors the real Draw.io compression pipeline so we can build
    realistic test fixtures.
    """
    url_encoded = urllib.parse.quote(xml_text, safe="")
    deflated = zlib.compress(url_encoded.encode("utf-8"), 9)[2:-4]  # raw deflate
    return base64.b64encode(deflated).decode("utf-8")


def _build_drawio_xml(
    pages: list[dict],
    *,
    mxfile_attrs: str = "",
) -> str:
    """Build a complete .drawio XML string from a list of page dicts.

    Each dict may contain keys: name, id, content, compressed (bool).
    If 'compressed' is True the content is compressed automatically.
    """
    parts = [f'<mxfile{" " + mxfile_attrs if mxfile_attrs else ""}>']
    for page in pages:
        pid = page.get("id", "page-id")
        pname = page.get("name", "Page-1")
        content = page.get("content", MINIMAL_GRAPH_XML)
        if page.get("compressed", False):
            content = _compress_content(content)
            parts.append(f'<diagram id="{pid}" name="{pname}">{content}</diagram>')
        else:
            parts.append(f'<diagram id="{pid}" name="{pname}">{content}</diagram>')
    parts.append("</mxfile>")
    return "\n".join(parts)


def _write_drawio(tmp_path: Path, xml_text: str, filename: str = "test.drawio") -> Path:
    """Write XML text to a .drawio file and return the path."""
    fp = tmp_path / filename
    fp.write_text(xml_text, encoding="utf-8")
    return fp


# ---------------------------------------------------------------------------
# Tests for load_drawio -- uncompressed
# ---------------------------------------------------------------------------


class TestLoadDrawioUncompressed:
    """Test loading uncompressed .drawio files."""

    def test_loads_single_page_uncompressed(self, tmp_path: Path) -> None:
        xml = _build_drawio_xml([{"name": "Main", "id": "d1", "compressed": False}])
        fp = _write_drawio(tmp_path, xml)

        root = load_drawio(fp)

        # Must return an mxGraphModel element
        assert root.tag == "mxGraphModel"
        # The mxGraphModel should contain a <root> child
        root_child = root.find("root")
        assert root_child is not None
        # Should contain our cells
        cells = root_child.findall("mxCell")
        assert len(cells) == 3  # ids 0, 1, 2

    def test_loads_with_string_path(self, tmp_path: Path) -> None:
        xml = _build_drawio_xml([{"name": "P1", "id": "abc", "compressed": False}])
        fp = _write_drawio(tmp_path, xml)

        root = load_drawio(str(fp))

        assert root.tag == "mxGraphModel"

    def test_returns_first_page_by_default(self, tmp_path: Path) -> None:
        pages = [
            {"name": "First", "id": "p1", "compressed": False},
            {"name": "Second", "id": "p2", "compressed": False},
        ]
        xml = _build_drawio_xml(pages)
        fp = _write_drawio(tmp_path, xml)

        root = load_drawio(fp)

        # Default: returns the first page
        assert root.tag == "mxGraphModel"


# ---------------------------------------------------------------------------
# Tests for load_drawio -- compressed
# ---------------------------------------------------------------------------


class TestLoadDrawioCompressed:
    """Test loading compressed .drawio files."""

    def test_loads_compressed_diagram(self, tmp_path: Path) -> None:
        xml = _build_drawio_xml([{"name": "Main", "id": "c1", "compressed": True}])
        fp = _write_drawio(tmp_path, xml)

        root = load_drawio(fp)

        assert root.tag == "mxGraphModel"
        root_child = root.find("root")
        assert root_child is not None
        cells = root_child.findall("mxCell")
        assert len(cells) == 3

    def test_compressed_content_matches_original(self, tmp_path: Path) -> None:
        """Verify that compressed round-trips produce the same graph."""
        xml = _build_drawio_xml([{"name": "A", "id": "rt", "compressed": True}])
        fp = _write_drawio(tmp_path, xml)

        root = load_drawio(fp)

        cell = root.find(".//mxCell[@id='2']")
        assert cell is not None
        assert cell.get("value") == "Start"
        assert cell.get("style") == "ellipse"


# ---------------------------------------------------------------------------
# Tests for is_compressed
# ---------------------------------------------------------------------------


class TestIsCompressed:
    """Test the is_compressed helper."""

    def test_uncompressed_returns_false(self) -> None:
        import defusedxml.ElementTree as DET

        diagram_xml = f'<diagram id="d1" name="P">{MINIMAL_GRAPH_XML}</diagram>'
        elem = DET.fromstring(diagram_xml)
        assert is_compressed(elem) is False

    def test_compressed_returns_true(self) -> None:
        import defusedxml.ElementTree as DET

        compressed = _compress_content(MINIMAL_GRAPH_XML)
        diagram_xml = f'<diagram id="d1" name="P">{compressed}</diagram>'
        elem = DET.fromstring(diagram_xml)
        assert is_compressed(elem) is True

    def test_empty_text_returns_false(self) -> None:
        import defusedxml.ElementTree as DET

        diagram_xml = '<diagram id="d1" name="P"></diagram>'
        elem = DET.fromstring(diagram_xml)
        assert is_compressed(elem) is False

    def test_whitespace_only_text_returns_false(self) -> None:
        import defusedxml.ElementTree as DET

        diagram_xml = '<diagram id="d1" name="P">   \n  </diagram>'
        elem = DET.fromstring(diagram_xml)
        assert is_compressed(elem) is False


# ---------------------------------------------------------------------------
# Tests for decompress_diagram
# ---------------------------------------------------------------------------


class TestDecompressDiagram:
    """Test direct decompression of Draw.io compressed content."""

    def test_decompresses_to_valid_xml(self) -> None:
        compressed = _compress_content(MINIMAL_GRAPH_XML)
        result = decompress_diagram(compressed)

        assert "<mxGraphModel>" in result
        assert "<mxCell" in result

    def test_round_trip_preserves_content(self) -> None:
        compressed = _compress_content(MINIMAL_GRAPH_XML)
        result = decompress_diagram(compressed)
        assert result == MINIMAL_GRAPH_XML

    def test_handles_special_characters(self) -> None:
        xml_with_special = (
            '<mxGraphModel>'
            '<root>'
            '<mxCell id="0"/>'
            '<mxCell id="1" parent="0"/>'
            '<mxCell id="2" value="Has &amp; special &lt;chars&gt;" '
            'vertex="1" parent="1"/>'
            '</root>'
            '</mxGraphModel>'
        )
        compressed = _compress_content(xml_with_special)
        result = decompress_diagram(compressed)
        assert result == xml_with_special

    def test_invalid_base64_raises_error(self) -> None:
        with pytest.raises(DrawioLoadError, match="[Dd]ecompress"):
            decompress_diagram("!!!not-base64!!!")

    def test_corrupt_deflate_raises_error(self) -> None:
        # Valid base64 but invalid deflate data
        bad_data = base64.b64encode(b"this is not deflated").decode("utf-8")
        with pytest.raises(DrawioLoadError, match="[Dd]ecompress"):
            decompress_diagram(bad_data)


# ---------------------------------------------------------------------------
# Tests for get_diagram_pages
# ---------------------------------------------------------------------------


class TestGetDiagramPages:
    """Test page enumeration for multi-page diagrams."""

    def test_single_page(self, tmp_path: Path) -> None:
        xml = _build_drawio_xml([{"name": "Only", "id": "pg1"}])
        fp = _write_drawio(tmp_path, xml)

        pages = get_diagram_pages(fp)

        assert len(pages) == 1
        assert pages[0]["index"] == 0
        assert pages[0]["id"] == "pg1"
        assert pages[0]["name"] == "Only"

    def test_multiple_pages(self, tmp_path: Path) -> None:
        page_defs = [
            {"name": "Overview", "id": "p-overview"},
            {"name": "Detail", "id": "p-detail"},
            {"name": "Error Handling", "id": "p-errors"},
        ]
        xml = _build_drawio_xml(page_defs)
        fp = _write_drawio(tmp_path, xml)

        pages = get_diagram_pages(fp)

        assert len(pages) == 3
        for i, pdef in enumerate(page_defs):
            assert pages[i]["index"] == i
            assert pages[i]["id"] == pdef["id"]
            assert pages[i]["name"] == pdef["name"]

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        xml = _build_drawio_xml([{"name": "X", "id": "xid"}])
        fp = _write_drawio(tmp_path, xml)

        pages = get_diagram_pages(str(fp))
        assert len(pages) == 1


# ---------------------------------------------------------------------------
# Tests for page selection
# ---------------------------------------------------------------------------


class TestPageSelection:
    """Test selecting specific pages by name or index."""

    def _make_multipage_file(self, tmp_path: Path) -> Path:
        page_defs = [
            {"name": "Login Flow", "id": "p1", "compressed": False},
            {"name": "Payment Flow", "id": "p2", "compressed": True},
            {"name": "Error Handling", "id": "p3", "compressed": False},
        ]
        xml = _build_drawio_xml(page_defs)
        return _write_drawio(tmp_path, xml)

    def test_select_by_name(self, tmp_path: Path) -> None:
        fp = self._make_multipage_file(tmp_path)

        root = load_drawio(fp, page="Payment Flow")

        assert root.tag == "mxGraphModel"

    def test_select_by_index_zero(self, tmp_path: Path) -> None:
        fp = self._make_multipage_file(tmp_path)

        root = load_drawio(fp, page=0)

        assert root.tag == "mxGraphModel"

    def test_select_by_index_last(self, tmp_path: Path) -> None:
        fp = self._make_multipage_file(tmp_path)

        root = load_drawio(fp, page=2)

        assert root.tag == "mxGraphModel"

    def test_invalid_page_name_raises(self, tmp_path: Path) -> None:
        fp = self._make_multipage_file(tmp_path)

        with pytest.raises(DrawioLoadError, match="[Pp]age.*not found"):
            load_drawio(fp, page="Nonexistent")

    def test_index_out_of_range_raises(self, tmp_path: Path) -> None:
        fp = self._make_multipage_file(tmp_path)

        with pytest.raises(DrawioLoadError, match="[Pp]age index.*out of range"):
            load_drawio(fp, page=99)

    def test_negative_index_raises(self, tmp_path: Path) -> None:
        fp = self._make_multipage_file(tmp_path)

        with pytest.raises(DrawioLoadError, match="[Pp]age index.*out of range"):
            load_drawio(fp, page=-1)


# ---------------------------------------------------------------------------
# Tests for error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Test error conditions raise DrawioLoadError with useful messages."""

    def test_file_not_found(self) -> None:
        with pytest.raises(DrawioLoadError, match="[Ff]ile not found"):
            load_drawio("/nonexistent/path/diagram.drawio")

    def test_invalid_xml(self, tmp_path: Path) -> None:
        fp = tmp_path / "bad.drawio"
        fp.write_text("<<<not xml at all>>>", encoding="utf-8")

        with pytest.raises(DrawioLoadError, match="[Ii]nvalid XML"):
            load_drawio(fp)

    def test_missing_diagram_element(self, tmp_path: Path) -> None:
        # Valid XML but no <diagram> element
        fp = tmp_path / "nodiagram.drawio"
        fp.write_text("<mxfile></mxfile>", encoding="utf-8")

        with pytest.raises(DrawioLoadError, match="[Nn]o.*diagram"):
            load_drawio(fp)

    def test_file_not_found_in_get_diagram_pages(self) -> None:
        with pytest.raises(DrawioLoadError, match="[Ff]ile not found"):
            get_diagram_pages("/nonexistent/pages.drawio")

    def test_invalid_xml_in_get_diagram_pages(self, tmp_path: Path) -> None:
        fp = tmp_path / "bad2.drawio"
        fp.write_text("{json not xml}", encoding="utf-8")

        with pytest.raises(DrawioLoadError, match="[Ii]nvalid XML"):
            get_diagram_pages(fp)

    def test_directory_instead_of_file(self, tmp_path: Path) -> None:
        with pytest.raises(DrawioLoadError):
            load_drawio(tmp_path)


# ---------------------------------------------------------------------------
# Tests for edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and unusual but valid inputs."""

    def test_diagram_with_child_mxgraphmodel(self, tmp_path: Path) -> None:
        """Some uncompressed files embed mxGraphModel as a child element."""
        xml = (
            '<mxfile>'
            '<diagram id="d1" name="P">'
            '<mxGraphModel>'
            '<root>'
            '<mxCell id="0"/>'
            '<mxCell id="1" parent="0"/>'
            '</root>'
            '</mxGraphModel>'
            '</diagram>'
            '</mxfile>'
        )
        fp = _write_drawio(tmp_path, xml)

        root = load_drawio(fp)
        assert root.tag == "mxGraphModel"

    def test_whitespace_around_compressed_content(self, tmp_path: Path) -> None:
        """Compressed content with leading/trailing whitespace."""
        compressed = _compress_content(MINIMAL_GRAPH_XML)
        xml = (
            '<mxfile>'
            f'<diagram id="d1" name="P">  \n  {compressed}  \n  </diagram>'
            '</mxfile>'
        )
        fp = _write_drawio(tmp_path, xml)

        root = load_drawio(fp)
        assert root.tag == "mxGraphModel"

    def test_large_diagram_content(self, tmp_path: Path) -> None:
        """Test with a larger number of cells to verify no truncation."""
        cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
        for i in range(2, 102):
            cells.append(
                f'<mxCell id="{i}" value="Task {i}" '
                f'style="rounded=1" vertex="1" parent="1"/>'
            )
        big_xml = (
            "<mxGraphModel><root>"
            + "".join(cells)
            + "</root></mxGraphModel>"
        )
        xml = _build_drawio_xml([
            {"name": "Big", "id": "big1", "content": big_xml, "compressed": True}
        ])
        fp = _write_drawio(tmp_path, xml)

        root = load_drawio(fp)
        all_cells = root.findall(".//mxCell")
        assert len(all_cells) == 102

    def test_decompressed_content_not_valid_xml(self, tmp_path: Path) -> None:
        """Compressed content that decompresses to non-XML text."""
        bad_text = "this is not xml at all"
        compressed = _compress_content(bad_text)
        xml = (
            '<mxfile>'
            f'<diagram id="d1" name="P">{compressed}</diagram>'
            '</mxfile>'
        )
        fp = _write_drawio(tmp_path, xml)

        with pytest.raises(DrawioLoadError, match="not valid XML"):
            load_drawio(fp)

    def test_empty_diagram_no_content(self, tmp_path: Path) -> None:
        """Diagram element with no text, no children, no compressed data."""
        xml = (
            '<mxfile>'
            '<diagram id="d1" name="P"></diagram>'
            '</mxfile>'
        )
        fp = _write_drawio(tmp_path, xml)

        with pytest.raises(DrawioLoadError, match="neither compressed"):
            load_drawio(fp)

    def test_inline_text_xml_parsed(self, tmp_path: Path) -> None:
        """Diagram text is raw XML without a child mxGraphModel element.

        This covers the fallback branch in _extract_graph_model where
        diagram.text is parseable XML but diagram has no child elements.
        """
        # Build XML where the diagram text IS the mxGraphModel XML string
        # but NOT as a child element (the XML parser sees it as text).
        # We achieve this by escaping the inner XML so the outer parser
        # treats it as text content. However, since our test helper
        # _build_drawio_xml already does this for uncompressed content,
        # and defusedxml parses it as text, this is already covered.
        # Instead, let us test the branch more directly.
        import defusedxml.ElementTree as DET
        from mcp_sh_bpmn.drawio_loader import _extract_graph_model

        diagram = DET.fromstring('<diagram id="d1" name="P"/>')
        diagram.text = MINIMAL_GRAPH_XML

        result = _extract_graph_model(diagram)
        assert result.tag == "mxGraphModel"

    def test_inline_text_invalid_xml_raises(self, tmp_path: Path) -> None:
        """Diagram text that starts with '<' but is invalid XML raises error.

        This covers the fallback branch where is_compressed returns False
        (text starts with '<'), no child mxGraphModel exists, but the
        text itself is not parseable XML.
        """
        import defusedxml.ElementTree as DET
        from mcp_sh_bpmn.drawio_loader import _extract_graph_model

        diagram = DET.fromstring('<diagram id="d1" name="P"/>')
        # Starts with '<' so is_compressed() returns False,
        # but this is broken XML that will fail to parse.
        diagram.text = "<broken><<not closed properly"

        with pytest.raises(DrawioLoadError, match="not valid XML and not compressed"):
            _extract_graph_model(diagram)
