import struct
import unittest
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "Icon"


class IntygsDriveIconAssetTests(unittest.TestCase):
    def test_application_icon_and_logo_are_vector_art(self):
        for name in ("icon.svg", "logo.svg"):
            root = ElementTree.parse(ICON_DIR / name).getroot()
            self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
            self.assertTrue(root.attrib.get("viewBox"))
            self.assertFalse(root.findall(".//{http://www.w3.org/2000/svg}image"))
        logo = (ICON_DIR / "logo.svg").read_text(encoding="utf-8")
        self.assertIn("IntygsDrive", logo)
        self.assertIn("FORDON", logo)

    def test_png_and_windows_ico_contain_all_expected_sizes(self):
        self.assertTrue((ICON_DIR / "icon.png").is_file())
        data = (ICON_DIR / "icon.ico").read_bytes()
        reserved, image_type, count = struct.unpack_from("<HHH", data)
        self.assertEqual((reserved, image_type), (0, 1))
        self.assertGreaterEqual(count, 8)
        sizes = {
            (size or 256, height or 256)
            for width, height, _colors, _reserved, _planes, _bits, _size, _offset
            in struct.iter_unpack("<BBBBHHII", data[6 : 6 + 16 * count])
            for size in (width,)
        }
        self.assertTrue({(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes)


if __name__ == "__main__":
    unittest.main()
