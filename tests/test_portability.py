"""Cross-platform regressions for the pieces that stopped being macOS-only.

Same stdlib-only contract as test_prod_gate: `python3 -m unittest discover -s tests`
must run with nothing installed. The resize tests skip themselves when Pillow is
absent; run them with
    uv run --with pillow --with pillow-heif -m unittest discover -s tests
"""
from __future__ import annotations

import os
import pathlib
import tempfile
import unittest

from test_prod_gate import M  # reuses the stubbed-requests import


class FindEnvStopsAtRoot(unittest.TestCase):
    """`while d != "/"` never terminated on Windows: os.path.dirname("C:\\\\")
    returns "C:\\\\", so the walk spun forever instead of raising."""

    def test_raises_instead_of_spinning_when_no_env_exists(self):
        prev = os.environ.pop("FIREARM_ENV", None)
        try:
            with tempfile.TemporaryDirectory() as d:
                # Point the walk at a directory with no mcp/.env above it inside
                # the temp tree; it must terminate at the filesystem root.
                deep = pathlib.Path(d, "a", "b", "c")
                deep.mkdir(parents=True)
                with self.assertRaises(FileNotFoundError):
                    M._find_env_from(str(deep))
        finally:
            if prev is not None:
                os.environ["FIREARM_ENV"] = prev

    def test_finds_env_above_the_start_dir(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "mcp").mkdir()
            target = pathlib.Path(d, "mcp", ".env")
            target.write_text("FRAPPE_BASE_URL=x\n")
            deep = pathlib.Path(d, "scripts", "nested")
            deep.mkdir(parents=True)
            self.assertEqual(M._find_env_from(str(deep)), str(target))


def _pillow_or_skip():
    try:
        import PIL  # noqa: F401
        import pillow_heif  # noqa: F401
    except ImportError:
        raise unittest.SkipTest("Pillow/pillow-heif not installed")


class Resize(unittest.TestCase):
    """resize() replaced macOS `sips`; these pin the two deliberate differences."""

    def _make(self, size, mode="RGB", exif_orientation=None):
        from PIL import Image
        im = Image.new(mode, size, (10, 20, 30) if mode == "RGB" else (10, 20, 30, 128))
        p = os.path.join(self.tmp, f"in-{size[0]}x{size[1]}-{mode}.png")
        if exif_orientation is not None:
            p = p.replace(".png", ".jpg")
            ex = im.getexif()
            ex[274] = exif_orientation
            im.convert("RGB").save(p, exif=ex)
        else:
            im.save(p)
        return p

    def setUp(self):
        _pillow_or_skip()
        self.tmp = tempfile.mkdtemp()

    def _resized(self, src):
        from PIL import Image
        dst = os.path.join(self.tmp, "out.jpg")
        M.resize(src, dst)
        with Image.open(dst) as im:
            return im.size, im.format

    def test_long_edge_is_capped(self):
        size, fmt = self._resized(self._make((4000, 2000)))
        self.assertEqual(size, (M.MAXPX, M.MAXPX // 2))
        self.assertEqual(fmt, "JPEG")

    def test_caps_the_long_edge_whichever_it_is(self):
        size, _ = self._resized(self._make((1000, 4000)))
        self.assertEqual(size, (M.MAXPX // 4, M.MAXPX))

    def test_small_images_are_not_upscaled(self):
        """`sips -Z` enlarged them; enlarging invents no detail and grows the upload."""
        size, _ = self._resized(self._make((800, 600)))
        self.assertEqual(size, (800, 600))

    def test_rgba_png_survives_the_jpeg_conversion(self):
        size, fmt = self._resized(self._make((2600, 1300), mode="RGBA"))
        self.assertEqual((size, fmt), ((M.MAXPX, M.MAXPX // 2), "JPEG"))

    def test_exif_rotation_is_baked_into_the_pixels(self):
        """Orientation 6 = rotate 90, so a 3000x1000 source lands portrait."""
        size, _ = self._resized(self._make((3000, 1000), exif_orientation=6))
        self.assertLess(size[0], size[1])


if __name__ == "__main__":
    unittest.main()


class PortraitCheck(unittest.TestCase):
    """portrait_photos() gates attach: the store's grid and gallery are landscape."""

    def setUp(self):
        _pillow_or_skip()
        self.tmp = tempfile.mkdtemp()

    def _photo(self, name, size, exif_orientation=None):
        from PIL import Image
        im = Image.new("RGB", size, (10, 20, 30))
        path = os.path.join(self.tmp, name)
        if exif_orientation is None:
            im.save(path)
        else:
            ex = im.getexif()
            ex[274] = exif_orientation
            im.save(path, exif=ex)
        return name

    def test_taller_than_wide_is_portrait(self):
        names = [self._photo("a.jpg", (1000, 2000)), self._photo("b.jpg", (2000, 1000))]
        self.assertEqual(M.portrait_photos(self.tmp, names), ["a.jpg"])

    def test_square_is_not_portrait(self):
        self.assertEqual(M.portrait_photos(self.tmp, [self._photo("s.jpg", (1500, 1500))]), [])

    def test_exif_rotation_is_applied_before_judging(self):
        """A landscape file tagged 'rotate 90' displays portrait — and is judged portrait;
        a portrait file tagged 'rotate 90' displays landscape and passes."""
        tagged_landscape = self._photo("t1.jpg", (2000, 1000), exif_orientation=6)
        tagged_portrait = self._photo("t2.jpg", (1000, 2000), exif_orientation=6)
        self.assertEqual(M.portrait_photos(self.tmp, [tagged_landscape, tagged_portrait]), ["t1.jpg"])

    def test_no_photos_no_flags(self):
        self.assertEqual(M.portrait_photos(self.tmp, []), [])


class RotateCommand(unittest.TestCase):
    """rotate turns a photo clockwise in place and keeps the original as .orig."""

    def setUp(self):
        _pillow_or_skip()
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "SN1"))

    def _photo(self, name, size, exif_orientation=None):
        from PIL import Image
        im = Image.new("RGB", size, (10, 20, 30))
        path = os.path.join(self.tmp, "SN1", name)
        if exif_orientation is None:
            im.save(path)
        else:
            ex = im.getexif(); ex[274] = exif_orientation
            im.save(path, exif=ex)
        return path

    def _size(self, path):
        from PIL import Image
        with Image.open(path) as im:
            return im.size

    def _rotate(self, name, degrees):
        import argparse, contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            M.cmd_rotate(argparse.Namespace(root=self.tmp, folder="SN1", file=name, degrees=degrees))
        return out.getvalue()

    def test_90_makes_a_portrait_photo_landscape_and_keeps_the_original(self):
        path = self._photo("main.jpg", (1000, 2000))
        out = self._rotate("main.jpg", 90)
        self.assertEqual(self._size(path), (2000, 1000))
        self.assertTrue(os.path.exists(path + ".orig"))
        self.assertEqual(self._size(path + ".orig"), (1000, 2000))
        self.assertIn("landscape", out)
        # the backup is not an image to the importer
        self.assertEqual(M.list_images(os.path.join(self.tmp, "SN1")), ["main.jpg"])

    def test_180_keeps_the_shape(self):
        path = self._photo("main.jpg", (2000, 1000))
        self._rotate("main.jpg", 180)
        self.assertEqual(self._size(path), (2000, 1000))

    def test_exif_is_applied_before_turning(self):
        """Stored landscape + 'rotate 90' tag displays portrait; one 90° turn must give landscape."""
        path = self._photo("main.jpg", (2000, 1000), exif_orientation=6)
        self._rotate("main.jpg", 90)
        self.assertEqual(self._size(path), (2000, 1000))
        self.assertEqual(M.portrait_photos(os.path.join(self.tmp, "SN1"), ["main.jpg"]), [])

    def test_second_turn_does_not_overwrite_the_first_backup(self):
        path = self._photo("main.jpg", (1000, 2000))
        self._rotate("main.jpg", 90)
        self._rotate("main.jpg", 90)
        self.assertEqual(self._size(path + ".orig"), (1000, 2000))  # still the untouched original
        self.assertEqual(self._size(path), (1000, 2000))            # two quarter turns = upside-down portrait

