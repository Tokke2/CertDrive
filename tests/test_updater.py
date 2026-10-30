import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from core import APP_DISPLAY_NAME, __version__ as app_version
from core.updater import (
    ReleaseUpdate,
    _version_key,
    check_latest_release,
    get_current_commit,
    run_update_helper,
    stage_release_archive,
    UPDATE_INTERVAL_HOURS,
)


class ReleaseVersionTests(unittest.TestCase):
    def test_app_version_is_pinned_until_explicitly_changed(self):
        self.assertEqual(app_version, "0.0.1")

    def test_automatic_update_interval_is_twice_daily(self):
        self.assertEqual(UPDATE_INTERVAL_HOURS, 12)

    def test_display_name_is_intygsdrive(self):
        self.assertEqual(APP_DISPLAY_NAME, "IntygsDrive")

    def test_version_tags_are_normalized_and_padded(self):
        self.assertEqual(_version_key("v1.2"), (1, 2, 0, 0))
        self.assertEqual(_version_key("1.2.0"), _version_key("v1.2"))
        self.assertIsNone(_version_key("latest"))

    def test_newer_release_resolves_commit_sha(self):
        payload = {
            "tag_name": "v0.0.2",
            "name": "IntygsDrive 0.0.2",
            "html_url": "https://github.com/Tokke2/CertDrive/releases/tag/v0.0.2",
            "body": "Bug fixes",
            "draft": False,
            "prerelease": False,
        }
        commit = "c" * 40
        with patch("core.updater._request_json", side_effect=[payload, {"sha": commit}]) as request:
            release = check_latest_release("0.0.1")
        self.assertEqual(release, ReleaseUpdate(
            version="v0.0.2",
            name="IntygsDrive 0.0.2",
            url="https://github.com/Tokke2/CertDrive/releases/tag/v0.0.2",
            notes="Bug fixes",
            commit_sha=commit,
        ))
        self.assertEqual(request.call_count, 2)

    def test_same_version_detects_new_release_commit(self):
        payload = {
            "tag_name": "v0.0.1",
            "name": "IntygsDrive 0.0.1",
            "html_url": "https://github.com/Tokke2/CertDrive/releases/tag/v0.0.1",
            "body": "Rolling release",
            "draft": False,
            "prerelease": False,
        }
        commit = "b" * 40
        with patch("core.updater._request_json", side_effect=[payload, {"sha": commit}]):
            update = check_latest_release("0.0.1", current_commit="a" * 40)
        self.assertIsNotNone(update)
        self.assertEqual(update.version, "v0.0.1")
        self.assertEqual(update.commit_sha, commit)

    def test_source_build_without_commit_identity_is_compared_by_archive(self):
        payload = {"tag_name": "0.0.1", "draft": False, "prerelease": False}
        commit = "d" * 40
        with patch("core.updater._request_json", side_effect=[payload, {"sha": commit}]):
            update = check_latest_release("0.0.1")
        self.assertEqual(update.commit_sha, commit)

    def test_matching_current_commit_still_checks_rolling_release_files(self):
        payload = {
            "tag_name": "v0.0.1",
            "name": "IntygsDrive 0.0.1",
            "html_url": "https://github.com/Tokke2/CertDrive/releases/tag/v0.0.1",
            "draft": False,
            "prerelease": False,
        }
        commit = "e" * 40
        with patch("core.updater._request_json", side_effect=[payload, {"sha": commit}]):
            update = check_latest_release("0.0.1", current_commit=commit)
        self.assertIsNotNone(update)
        self.assertEqual(update.commit_sha, commit)


def _zipball(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(f"Tokke2-CertDrive-test/{name}", content)
    return output.getvalue()


class AutomaticUpdateStagingTests(unittest.TestCase):
    def test_update_is_applied_after_shutdown_and_user_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "core").mkdir()
            (root / "js").mkdir()
            (root / "Mallar").mkdir()
            (root / "main.pyw").write_text("old main", encoding="utf-8")
            (root / "core" / "module.py").write_text("old code", encoding="utf-8")
            (root / "js" / "settings.json").write_text('{"keep": true}', encoding="utf-8")
            (root / "Mallar" / "kund.docx").write_bytes(b"user template")
            release = ReleaseUpdate("v0.0.1", "IntygsDrive 0.0.1", "", "", "a" * 40)
            archive = _zipball({
                "main.pyw": b"new main",
                "core/module.py": b"new code",
                "plugins/new_plugin.py": b"new plugin",
                "js/settings.json": b"attacker must not overwrite config",
                "Mallar/kund.docx": b"attacker must not overwrite templates",
            })

            staged = stage_release_archive(root, release, archive)
            self.assertTrue(staged.staged)
            self.assertTrue(run_update_helper(root, -12345, wait_timeout=0.01))

            self.assertEqual((root / "main.pyw").read_text(encoding="utf-8"), "new main")
            self.assertEqual((root / "core" / "module.py").read_text(encoding="utf-8"), "new code")
            self.assertEqual((root / "plugins" / "new_plugin.py").read_text(encoding="utf-8"), "new plugin")
            self.assertEqual((root / "js" / "settings.json").read_text(encoding="utf-8"), '{"keep": true}')
            self.assertEqual((root / "Mallar" / "kund.docx").read_bytes(), b"user template")
            self.assertEqual(get_current_commit(root), "a" * 40)
            self.assertFalse((root / "js" / ".update-staging").exists())

    def test_same_commit_with_local_file_drift_stages_release_contents(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "core").mkdir()
            (root / "js").mkdir()
            (root / "main.pyw").write_bytes(b"locally changed main")
            (root / "core" / "module.py").write_bytes(b"same module")
            commit = "e" * 40
            (root / "js" / "update_state.json").write_text(
                json.dumps({"version": "v0.0.1", "commit_sha": commit}),
                encoding="utf-8",
            )
            release = ReleaseUpdate("v0.0.1", "IntygsDrive 0.0.1", "", "", commit)

            result = stage_release_archive(root, release, _zipball({
                "main.pyw": b"release main",
                "core/module.py": b"same module",
            }))

            self.assertEqual(get_current_commit(root), commit)
            self.assertTrue(result.staged)

    def test_matching_source_archive_records_commit_without_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "core").mkdir()
            (root / "main.pyw").write_bytes(b"same main")
            (root / "core" / "module.py").write_bytes(b"same code")
            release = ReleaseUpdate("v0.0.1", "IntygsDrive 0.0.1", "", "", "f" * 40)

            result = stage_release_archive(
                root, release, _zipball({
                    "main.pyw": b"same main",
                    "core/module.py": b"same code",
                })
            )

            self.assertFalse(result.staged)
            self.assertEqual(get_current_commit(root), "f" * 40)
            self.assertFalse((root / "js" / ".update-staging" / "pending.json").exists())

    def test_archive_paths_cannot_escape_runtime_allowlist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "core").mkdir()
            (root / "js").mkdir()
            (root / "main.pyw").write_bytes(b"old")
            (root / "js" / "settings.json").write_text("safe", encoding="utf-8")
            release = ReleaseUpdate("v0.0.1", "IntygsDrive 0.0.1", "", "", "1" * 40)
            result = stage_release_archive(root, release, _zipball({
                "main.pyw": b"new",
                "../../js/settings.json": b"unsafe",
            }))
            self.assertTrue(result.staged)
            self.assertEqual((root / "js" / "settings.json").read_text(encoding="utf-8"), "safe")


if __name__ == "__main__":
    unittest.main()
