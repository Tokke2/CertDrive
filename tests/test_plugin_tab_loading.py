import logging
import unittest
from types import SimpleNamespace

from core import PluginManager


class PluginTabLoadingTests(unittest.TestCase):
    def make_manager(self, plugins):
        manager = object.__new__(PluginManager)
        manager._order = list(plugins)
        manager.loaded = plugins
        manager._logger = logging.getLogger("test.plugin.tabs")
        return manager

    def test_tab_name_listing_does_not_build_widgets_and_skips_service_plugins(self):
        calls = []
        plugins = {
            "Certificates": SimpleNamespace(get_tab=lambda: calls.append("certificates")),
            "Generator": SimpleNamespace(
                has_tab=False,
                get_tab=lambda: calls.append("generator"),
            ),
            "Settings": SimpleNamespace(get_tab=lambda: calls.append("settings")),
            "No factory": SimpleNamespace(),
        }
        manager = self.make_manager(plugins)

        self.assertEqual(manager.get_tab_names(), ["Certificates", "Settings"])
        self.assertEqual(calls, [])

    def test_one_plugin_tab_can_be_built_on_demand(self):
        built = []
        widget = object()
        plugins = {
            "Settings": SimpleNamespace(get_tab=lambda: built.append(True) or widget),
        }
        manager = self.make_manager(plugins)

        self.assertIs(manager.get_tab("Settings"), widget)
        self.assertEqual(built, [True])
        self.assertIsNone(manager.get_tab("Missing"))


if __name__ == "__main__":
    unittest.main()
