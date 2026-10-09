import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from engine import (BLEND_MODES, Document, History, Transform, blend, brush,
                    color_mask, dimensions, export_image, filter_image,
                    load_project, placed, render, save_project)


class EngineTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).parent/"verification"/"test-scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.folder = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def document(self):
        document = Document(8, 6)
        document.add(Image.new("RGBA", (8, 6), (255, 0, 0, 255)), "背景")
        layer = document.add(Image.new("RGBA", (2, 2), (0, 0, 255, 128)), "前景")
        layer.transform.x, layer.transform.y = 2, 1
        return document

    def test_alpha_composite_and_placement(self):
        image = render(self.document())
        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 255))
        self.assertEqual(image.getpixel((2, 1)), (127, 0, 128, 255))

    def test_blend_alpha_follows_source_over_not_masked_paste(self):
        result = blend(Image.new("RGBA", (1, 1), (200, 100, 50, 128)),
                       Image.new("RGBA", (1, 1), (100, 200, 100, 128)), "Multiply")
        # Analytical result: Ao=0.752, premultiplied RGB=(94.7,94.7,42.2).
        self.assertEqual(result.getpixel((0, 0)), (126, 126, 56, 192))

    def test_supported_blends_preserve_transparent_backdrop(self):
        for mode in BLEND_MODES:
            result = blend(Image.new("RGBA", (1, 1)), Image.new("RGBA", (1, 1), (88, 155, 222, 100)), mode)
            self.assertEqual(result.getpixel((0, 0)), (88, 155, 222, 100), mode)

    def test_group_opacity_mask_and_visibility(self):
        document = Document(4, 4)
        group = document.add(Image.new("RGBA", (4, 4)), "组")
        group.image, group.is_group, group.opacity = None, True, .5
        group.mask = Image.new("L", (4, 4), 128)
        layer = document.add(Image.new("RGBA", (4, 4), "red"), "子图层")
        layer.parent_id = group.id
        self.assertEqual(render(document).getpixel((0, 0)), (255, 0, 0, 64))
        group.visible = False
        self.assertEqual(render(document).getpixel((0, 0))[3], 0)

    def test_clipping_source_alpha_ignores_visibility(self):
        document = Document(4, 4)
        source = document.add(Image.new("RGBA", (2, 2), "red"), "底")
        source.visible = False
        target = document.add(Image.new("RGBA", (4, 4), "blue"), "剪贴")
        target.clipping_id = source.id
        result = render(document)
        self.assertEqual(result.getpixel((0, 0)), (0, 0, 255, 255))
        self.assertEqual(result.getpixel((3, 3))[3], 0)

    def test_clockwise_rotation_and_flip(self):
        image = Image.new("RGBA", (2, 2))
        image.putpixel((0, 0), (255, 0, 0, 255))
        transform = Transform(0, 0, 2, 2, 90, sampling="Nearest")
        self.assertEqual(placed(image, transform, (2, 2)).getpixel((1, 0)), (255, 0, 0, 255))
        transform.rotation, transform.flip_x = 0, True
        self.assertEqual(placed(image, transform, (2, 2)).getpixel((1, 0)), (255, 0, 0, 255))

    def test_project_roundtrip_keeps_metadata_pixels_masks_and_order(self):
        document = self.document()
        layer = document.active
        layer.opacity, layer.blend = .6, "Screen"
        layer.mask = Image.new("L", (1, 1), 64)
        layer.metadata["text"] = {"content": "示例", "fontName": "Arial"}
        layer.mask_placement = Transform(1, 1, 2, 2)
        path = self.folder/"项目.comp"
        save_project(document, path)
        loaded = load_project(path)
        self.assertEqual(loaded.id, document.id)
        self.assertEqual(loaded.active_id, layer.id)
        self.assertEqual(loaded.active.metadata["text"], layer.metadata["text"])
        self.assertEqual(loaded.active.mask.tobytes(), layer.mask.tobytes())
        np.testing.assert_array_equal(np.asarray(render(document)), np.asarray(render(loaded)))
        save_project(loaded, path)
        self.assertEqual(load_project(path).active.name, "前景")

    def test_upstream_example_format_is_readable(self):
        document = self.document()
        path = self.folder/"mac.comp"
        save_project(document, path)
        data = json.loads((path/"manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(data["layers"][0]["transform"]["origin"], [0, 0])
        self.assertEqual(data["format"], "com.compositor.project")
        self.assertEqual(data["version"], 11)
        self.assertEqual(data["layers"][0]["imageFile"], document.layers[0].id+".png")

    def test_unsupported_effects_refused_without_overwriting(self):
        path = self.folder/"mac.comp"
        save_project(self.document(), path)
        manifest = path/"manifest.json"
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        raw["layers"][0]["effects"] = {"dropShadow": {"opacity": 1}}
        manifest.write_text(json.dumps(raw), encoding="utf-8")
        before = manifest.read_bytes()
        with self.assertRaises(ValueError): load_project(path)
        self.assertEqual(manifest.read_bytes(), before)

    def test_missing_asset_and_traversal_rejected(self):
        path = self.folder/"mac.comp"
        save_project(self.document(), path)
        manifest = path/"manifest.json"
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        raw["layers"][0]["imageFile"] = "../../outside.png"
        manifest.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(ValueError): load_project(path)

    def test_cycles_and_duplicate_ids_rejected(self):
        path = self.folder/"mac.comp"
        save_project(self.document(), path)
        manifest = path/"manifest.json"
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        raw["layers"][0]["maskSourceID"] = raw["layers"][1]["id"]
        raw["layers"][1]["maskSourceID"] = raw["layers"][0]["id"]
        manifest.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(ValueError): load_project(path)

    def test_failed_save_retains_previous_package(self):
        path = self.folder/"mac.comp"
        document = self.document()
        save_project(document, path)
        before = (path/"manifest.json").read_bytes()
        original_replace = __import__("os").replace
        def fail_new_package(source, target):
            if Path(target) == path and "backup" not in str(source):
                raise OSError("simulated failure")
            return original_replace(source, target)
        document.active.name = "changed"
        with patch("engine.os.replace", side_effect=fail_new_package):
            with self.assertRaises(OSError): save_project(document, path)
        self.assertEqual((path/"manifest.json").read_bytes(), before)
        self.assertEqual(load_project(path).active.name, "前景")

    def test_save_refuses_unrelated_directory(self):
        path = self.folder/"notes.comp"
        path.mkdir()
        (path/"important.txt").write_text("retain")
        with self.assertRaises(ValueError): save_project(self.document(), path)
        self.assertEqual((path/"important.txt").read_text(), "retain")

    def test_brush_erase_mask_and_rotated_coordinates(self):
        document = Document(10, 10)
        layer = document.add(Image.new("RGBA", (10, 10)), "像素")
        brush(layer, (5, 5), (5, 5), 1, "red")
        self.assertEqual(layer.image.getpixel((5, 5)), (255, 0, 0, 255))
        brush(layer, (5, 5), (5, 5), 1, "red", erase=True)
        self.assertEqual(layer.image.getpixel((5, 5))[3], 0)
        brush(layer, (2, 2), (2, 2), 1, "red", erase=True, on_mask=True)
        self.assertEqual(layer.mask.getpixel((2, 2)), 0)
        brush(layer, (2, 2), (2, 2), 1, "red", on_mask=True)
        self.assertEqual(layer.mask.getpixel((2, 2)), 255)

    def test_upstream_uniform_mask_expands_before_paint(self):
        document = Document(20, 20)
        layer = document.add(Image.new("RGBA", (20, 20), "red"), "layer")
        layer.mask = Image.new("L", (1, 1), 255)
        brush(layer, (5, 5), (5, 5), 1, "red", erase=True, on_mask=True)
        self.assertEqual(layer.mask.size, (20, 20))
        self.assertEqual(layer.mask.getpixel((5, 5)), 0)
        self.assertEqual(layer.mask.getpixel((19, 19)), 255)

    def test_history_isolated_and_redo_invalidated(self):
        document, history = self.document(), History()
        history.push(document)
        document.active.image.putpixel((0, 0), (1, 2, 3, 4))
        document = history.undo(document)
        self.assertEqual(document.active.image.getpixel((0, 0)), (0, 0, 255, 128))
        document = history.redo(document)
        self.assertEqual(document.active.image.getpixel((0, 0)), (1, 2, 3, 4))
        document = history.undo(document)
        history.push(document)
        self.assertFalse(history.redo_stack)

    def test_export_alpha_and_jpeg_flatten(self):
        document = Document(2, 2)
        document.add(Image.new("RGBA", (2, 2), (255, 0, 0, 128)), "半透明")
        document.resolution = 300
        export_image(document, self.folder/"export.png")
        export_image(document, self.folder/"export.jpg")
        with Image.open(self.folder/"export.png") as image:
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 128))
            self.assertAlmostEqual(image.info["dpi"][0], 300, delta=1)
        with Image.open(self.folder/"export.jpg") as image:
            self.assertEqual(image.mode, "RGB")
            self.assertLess(abs(image.getpixel((0, 0))[1]-127), 4)

    def test_color_key_mask_and_filter_alpha(self):
        document = Document(2, 1)
        image = Image.new("RGBA", (2, 1), "white")
        image.putpixel((1, 0), (255, 0, 0, 128))
        layer = document.add(image, "图片")
        color_mask(layer, (255, 255, 255), 20)
        self.assertEqual(layer.mask.getpixel((0, 0)), 0)
        self.assertEqual(layer.mask.getpixel((1, 0)), 255)
        inverted = filter_image(image, "invert")
        self.assertEqual(inverted.getpixel((1, 0)), (0, 255, 255, 128))

    def test_limits_and_nonfinite_transform(self):
        with self.assertRaises(ValueError): dimensions(12000, 12000)
        with self.assertRaises(ValueError): Transform.from_dict({"origin": [float("nan"), 0], "size": [1, 1]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
