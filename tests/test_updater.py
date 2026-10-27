import json
import unittest
from unittest.mock import patch

from core import APP_DISPLAY_NAME, __version__ as app_version
from core.updater import ReleaseUpdate, _version_key, check_latest_release


class ReleaseVersionTests(unittest.TestCase):
    def test_app_version_is_pinned_until_explicitly_changed(self):
        self.assertEqual(app_version, "0.0.1")

    def test_display_name_is_intygsdrive(self):
        self.assertEqual(APP_DISPLAY_NAME, "IntygsDrive")

    def test_version_tags_are_normalized_and_padded(self):
        self.assertEqual(_version_key("v1.2"), (1, 2, 0, 0))
        self.assertEqual(_version_key("1.2.0"), _version_key("v1.2"))
        self.assertIsNone(_version_key("latest"))

    def test_returns_newer_non_prerelease_release(self):
        payload = {
            "tag_name": "v0.0.2",
            "name": "IntygsDrive 0.0.2",
            "html_url": "https://github.com/Tokke2/CertDrive/releases/tag/v0.0.2",
            "body": "Bug fixes",
            "draft": False,
            "prerelease": False,
        }

        class Response:
            def __init__(self, value):
                self.value = value

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return json.dumps(self.value).encode("utf-8")

        with patch("core.updater.urlopen", return_value=Response(payload)):
            release = check_latest_release("0.0.1")
        self.assertEqual(
            release,
            ReleaseUpdate(
                version="v0.0.2",
                name="IntygsDrive 0.0.2",
                url="https://github.com/Tokke2/CertDrive/releases/tag/v0.0.2",
                notes="Bug fixes",
            ),
        )

    def test_same_version_detects_new_release_commit(self):
        release_payload = {
            "tag_name": "v0.0.1",
            "name": "IntygsDrive 0.0.1",
            "html_url": "https://github.com/Tokke2/CertDrive/releases/tag/v0.0.1",
            "body": "Rolling release",
            "draft": False,
            "prerelease": False,
        }
        release_commit = "b" * 40

        class Response:
            def __init__(self, value):
                self.value = value

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return json.dumps(self.value).encode("utf-8")

        with patch(
            "core.updater.urlopen",
            side_effect=[Response(release_payload), Response({"sha": release_commit})],
        ):
            update = check_latest_release("0.0.1", current_commit="a" * 40)
        self.assertIsNotNone(update)
        self.assertEqual(update.version, "v0.0.1")
        self.assertEqual(update.commit_sha, release_commit)

    def test_same_or_older_version_without_checkout_identity_is_not_an_update(self):
        payload = {"tag_name": "0.0.1", "draft": False, "prerelease": False}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return json.dumps(payload).encode("utf-8")

        with patch("core.updater.urlopen", return_value=Response()):
            self.assertIsNone(check_latest_release("0.0.1"))


if __name__ == "__main__":
    unittest.main()
