"""Tests for report-path safety contracts.

Validates that report_path+'.md' and report_path+'.json' cannot overwrite
source input files. Each test verifies: exit_code=4, source file preserved,
correct SourcePathError raised.
"""
import pytest
from cida.infrastructure.filesystem import validate_filesystem_safety
from cida.domain.errors import SourcePathError


class TestReportPathSafety:
    """Report path cannot overwrite source files."""

    def test_report_md_overwriting_source_md(self, tmp_path):
        """report_path stem + '.md' == source file → SourcePathError exit_code=4."""
        src = tmp_path / "relatorio.md"
        src.write_text("# Source content", encoding="utf-8")
        dst = tmp_path / "dst"
        # report-path = 'relatorio' → produces 'relatorio.md'
        report_stem = str(tmp_path / "relatorio")

        with pytest.raises(SourcePathError) as exc_info:
            validate_filesystem_safety(str(src), str(dst), report_stem)

        assert exc_info.value.exit_code == 4
        assert "relatorio" in str(exc_info.value).lower() or "report" in str(exc_info.value).lower()
        # Source file must still exist
        assert src.exists()
        assert src.read_text(encoding="utf-8") == "# Source content"

    def test_report_json_does_not_conflict_with_md_source(self, tmp_path):
        """report_path+'.json' when source is .md should NOT raise (different file)."""
        src = tmp_path / "doc.md"
        src.write_text("# Source", encoding="utf-8")
        dst = tmp_path / "dst"
        # report_path = report → produces report.md and report.json — no conflict with doc.md
        report_stem = str(tmp_path / "report")

        # Should not raise
        validate_filesystem_safety(str(src), str(dst), report_stem)

    def test_report_json_overwriting_source_json(self, tmp_path):
        """report_path stem + '.json' == source file -> SourcePathError."""
        src = tmp_path / "relatorio.json"
        src.write_text('{"source": true}', encoding="utf-8")
        dst = tmp_path / "dst"
        report_stem = str(tmp_path / "relatorio")

        with pytest.raises(SourcePathError) as exc_info:
            validate_filesystem_safety(str(src), str(dst), report_stem)

        assert exc_info.value.exit_code == 4
        assert src.read_text(encoding="utf-8") == '{"source": true}'

    def test_report_path_cannot_collide_with_generated_output(self, tmp_path):
        """For a single file source, report.md must not be the destination output."""
        src = tmp_path / "doc.md"
        src.write_text("# Source", encoding="utf-8")
        dst = tmp_path / "dst"
        report_stem = str(dst / "doc")

        with pytest.raises(SourcePathError) as exc_info:
            validate_filesystem_safety(str(src), str(dst), report_stem)

        assert exc_info.value.exit_code == 4
        assert "generated output" in str(exc_info.value)

    def test_report_md_same_name_as_source_directory_file(self, tmp_path):
        """report_path+'md' collides with a .md source file inside the source dir."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        src_file = src_dir / "notes.md"
        src_file.write_text("# Notes", encoding="utf-8")
        dst = tmp_path / "dst"
        # For a directory source, no conflict expected unless reportpath == src
        report_stem = str(tmp_path / "notes")

        # Should not raise (src is a dir, report is outside)
        validate_filesystem_safety(str(src_dir), str(dst), report_stem)

    def test_report_path_same_as_source_file_stem(self, tmp_path):
        """When source is a single .md file, report stem = same base → error."""
        src = tmp_path / "input.md"
        src.write_text("content", encoding="utf-8")
        dst = tmp_path / "dst"
        # report-path 'input' → 'input.md' which is exactly the source
        report_stem = str(tmp_path / "input")

        with pytest.raises(SourcePathError) as exc_info:
            validate_filesystem_safety(str(src), str(dst), report_stem)

        assert exc_info.value.exit_code == 4
        # Source preserved
        assert src.exists()

    def test_valid_report_path_no_conflict(self, tmp_path):
        """A report path that doesn't collide should pass without error."""
        src = tmp_path / "source.md"
        src.write_text("content", encoding="utf-8")
        dst = tmp_path / "dst"
        report_stem = str(tmp_path / "my_report")

        # Must not raise
        validate_filesystem_safety(str(src), str(dst), report_stem)

    def test_destination_inside_source_raises(self, tmp_path):
        """Destination nested inside source raises SourcePathError."""
        src = tmp_path / "src"
        src.mkdir()
        dst = src / "output"
        with pytest.raises(SourcePathError) as exc_info:
            validate_filesystem_safety(str(src), str(dst))
        assert exc_info.value.exit_code == 4

    def test_source_and_destination_identical_raises(self, tmp_path):
        """Identical source and destination raises SourcePathError."""
        src = tmp_path / "mydir"
        src.mkdir()
        with pytest.raises(SourcePathError) as exc_info:
            validate_filesystem_safety(str(src), str(src))
        assert exc_info.value.exit_code == 4
