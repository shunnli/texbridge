import gzip
from pathlib import Path
import tempfile
import unittest

from texbridge.workspace import find_git_root, find_project


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "paper" / "chapters" / "intro.tex"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("Sample source", encoding="utf-8")

    def pdf(self, relative, inputs, compressed=False):
        pdf = self.root / relative
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"PDF fixture")
        header = "SyncTeX Version:1\n" + "".join(
            f"Input:{number}:{name}\n" for number, name in enumerate(inputs, 1)
        ) + "Output:pdf\nContent:\n"
        if compressed:
            pdf.with_suffix(".synctex.gz").write_bytes(gzip.compress(header.encode("utf-8")))
        else:
            pdf.with_suffix(".synctex").write_text(header, encoding="utf-8")
        return pdf

    def test_find_repository_above_nested_source(self):
        (self.root / ".git").mkdir()
        source_directory = self.root / "paper" / "chapters"
        source_directory.mkdir(parents=True, exist_ok=True)
        self.assertEqual(find_git_root(source_directory), self.root)

    def test_nearest_nested_repository_wins(self):
        (self.root / ".git").mkdir()
        nested = self.root / "nested"
        nested.mkdir()
        (nested / ".git").mkdir()
        self.assertEqual(find_git_root(nested), nested)

    def test_worktree_git_file_is_supported(self):
        (self.root / ".git").write_text("gitdir: ../repo/.git/worktrees/paper\n", encoding="utf-8")
        self.assertEqual(find_git_root(self.root), self.root)

    def test_non_repository_has_no_root(self):
        self.assertIsNone(find_git_root(self.root))

    def test_inverse_prefers_git_root_without_pdf(self):
        (self.root / ".git").mkdir()
        self.assertEqual(find_project(self.source), self.root)

    def test_pdf_in_parent_matches_relative_child(self):
        pdf = self.pdf("paper/main.pdf", ["./main.tex", "./chapters/intro.tex"])
        self.assertEqual(find_project(self.source), pdf.parent)

    def test_build_pdf_matches_absolute_root_and_relative_child(self):
        pdf = self.pdf("paper/build/main.pdf", [str(self.root / "paper/main.tex"), "chapters/intro.tex"], compressed=True)
        self.assertEqual(find_project(self.source), pdf.parent)

    def test_unicode_absolute_input(self):
        source = self.source.with_name("中文.tex")
        source.write_text("Sample", encoding="utf-8")
        pdf = self.pdf("paper/main.pdf", [source.as_posix()], compressed=True)
        self.assertEqual(find_project(source), pdf.parent)

    def test_unrelated_pdf_does_not_select_project(self):
        self.pdf("paper/main.pdf", ["./main.tex", "./other/intro.tex"])
        self.assertIsNone(find_project(self.source))

    def test_missing_or_corrupt_metadata_falls_back(self):
        pdf = self.pdf("paper/main.pdf", [str(self.source)])
        pdf.with_suffix(".synctex").write_bytes(b"invalid metadata")
        pdf.with_suffix(".synctex.gz").write_bytes(b"not gzip")
        self.assertIsNone(find_project(self.source))

    def test_multiple_matching_folders_are_ambiguous(self):
        self.pdf("paper/main.pdf", [str(self.source)])
        self.pdf("paper/build/main.pdf", [str(self.source)])
        self.assertIsNone(find_project(self.source))

    def test_multiple_pdfs_in_one_folder_are_unambiguous(self):
        self.pdf("paper/first.pdf", [str(self.source)])
        pdf = self.pdf("paper/second.pdf", [str(self.source)])
        self.assertEqual(find_project(self.source), pdf.parent)

    def test_explicit_pdf_context_and_its_git_root(self):
        pdf = self.pdf("elsewhere/main.pdf", [str(self.source)])
        self.assertEqual(find_project(self.source, pdf), pdf.parent)
        (self.root / ".git").mkdir()
        self.assertEqual(find_project(self.source, pdf), self.root)


if __name__ == "__main__":
    unittest.main()
