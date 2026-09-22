from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from build_open_source_release import ROOT_FILES, DATA_FILES, build, selected_files, sha256


class ReleaseTests(unittest.TestCase):
    def fixture(self, root: Path) -> None:
        root.mkdir()
        for name in ROOT_FILES:
            (root / name).write_text("{}" if name.endswith(".json") else "fixture\n", encoding="utf-8")
        (root / "data").mkdir()
        for name in DATA_FILES:
            (root / "data" / name).write_text("{}", encoding="utf-8")
        (root / "workbench").mkdir()
        for name in ("setup-windows.ps1", "start-windows.ps1", "setup-macos.sh", ".gitignore"):
            (root / "workbench" / name).write_text("fixture\n", encoding="utf-8")

    def test_archive_preserves_original_and_redacts_only_distribution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            self.fixture(root)
            draft = root / "workbench/runtime/drafts/abc.json"
            draft.parent.mkdir(parents=True)
            draft.write_text(json.dumps({"matches": [{"path": str(root / "source.json"), "result": "胜"}]}), encoding="utf-8")
            original = draft.read_bytes()
            secret = root / "workbench/runtime/evidence/private.body"
            secret.parent.mkdir(parents=True)
            secret.write_text("downloaded full article", encoding="utf-8")
            dependency = root / "workbench/frontend/node_modules/package/index.js"
            dependency.parent.mkdir(parents=True)
            dependency.write_text("dependency", encoding="utf-8")
            review = root / "workbench/runtime/reviews/manual/review.json"
            review.parent.mkdir(parents=True)
            review.write_text('{"summary":"saved review"}', encoding="utf-8")
            raw_result = root / "workbench/runtime/reviews/manual_sources/raw.json"
            raw_result.parent.mkdir(parents=True)
            raw_result.write_text('{"payload":{"official":"raw result"}}', encoding="utf-8")
            out = Path(temporary) / "release-v1.0.0"
            report = build(root, out)
            self.assertEqual(Path(report["archive"]).name, "release-v1.0.0.zip")
            self.assertEqual(draft.read_bytes(), original)
            published = (out / draft.relative_to(root)).read_text(encoding="utf-8")
            self.assertNotIn(str(root), published)
            self.assertEqual(json.loads(published)["matches"][0]["result"], "胜")
            self.assertFalse((out / secret.relative_to(root)).exists())
            self.assertFalse((out / dependency.relative_to(root)).exists())
            self.assertEqual((out / review.relative_to(root)).read_bytes(), review.read_bytes())
            self.assertFalse((out / raw_result.relative_to(root)).exists())
            manifest = json.loads((out / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
            for item in manifest["files"]:
                self.assertEqual(sha256((out / item["path"]).read_bytes()), item["sha256"])
            with zipfile.ZipFile(report["archive"]) as archive:
                self.assertIsNone(archive.testzip())
                self.assertTrue(all(name.startswith("release-v1.0.0/") for name in archive.namelist()))
            with self.assertRaises(ValueError):
                build(root, out)

    def test_secret_rejected_before_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            self.fixture(root)
            (root / "README.md").write_text("ghp_" + "a" * 36, encoding="utf-8")
            output = Path(temporary) / "release"
            with self.assertRaisesRegex(ValueError, "Credential-like"):
                build(root, output)
            self.assertFalse(output.exists())

    def test_export_backup_excluded_original_version_retained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            self.fixture(root)
            for relative in ("exports/2026-09-23/prediction_V1.json", "exports/backup/prediction_V1.json"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")
            selected = {path.relative_to(root).as_posix() for path in selected_files(root)}
            self.assertIn("exports/2026-09-23/prediction_V1.json", selected)
            self.assertNotIn("exports/backup/prediction_V1.json", selected)


if __name__ == "__main__":
    unittest.main()
