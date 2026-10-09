import copy
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from engine import Document, Layer, Transform, History, brush, render, save_project, load_project
from adjustments import default_adjustment, apply_adjustment, curve_value
from preview import PreviewCache
from app import Editor
from dialogs import text_image


class RenderTests(unittest.TestCase):
    def scene(self):
        document = Document(40, 30)
        document.add(Image.new("RGBA", (40, 30), (150, 60, 20, 255)), "base")
        layer = document.add(Image.new("RGBA", (12, 12), (25, 180, 230, 160)), "top")
        layer.transform.x, layer.transform.y = 10, 5
        return document

    def test_cached_output_matches_uncached_after_all_change_types(self):
        d, cache = self.scene(), PreviewCache()
        def check(): np.testing.assert_array_equal(np.asarray(render(d)), np.asarray(render(d, cache=cache)))
        check()
        for operation in (lambda: setattr(d.active.transform, "x", 16),
                          lambda: setattr(d.active.transform, "rotation", 30),
                          lambda: setattr(d.active, "opacity", .5),
                          lambda: setattr(d.active, "blend", "Screen"),
                          lambda: setattr(d.active, "mask", Image.new("L", (1, 1), 128)),
                          lambda: setattr(d.active, "visible", False),
                          lambda: setattr(d.active, "visible", True),
                          lambda: setattr(d.active, "image", Image.new("RGBA", (12, 12), "green"))):
            operation(); check()

    def test_static_frame_reused_and_cache_budget_enforced(self):
        d, cache = self.scene(), PreviewCache(16000)
        first = render(d, cache=cache)
        second = render(d, cache=cache)
        self.assertIs(first, second)
        for index in range(20):
            d.active.transform.x = index
            render(d, cache=cache)
            self.assertLessEqual(cache.bytes, cache.budget)

    def test_group_mask_sampling_changes_invalidate_cached_descendants(self):
        d, cache = self.scene(), PreviewCache()
        group = Layer("group", None, Transform(0, 0, 40, 30), is_group=True)
        group.mask = Image.fromarray(np.uint8([[0, 255], [255, 0]]))
        d.layers.insert(0, group)
        d.layers[1].parent_id = group.id
        render(d, cache=cache)
        group.transform.sampling = "Nearest"
        group.opacity = .6
        np.testing.assert_array_equal(np.asarray(render(d)), np.asarray(render(d, cache=cache)))

    def test_snapshot_shares_pixels_but_isolates_geometry_and_metadata(self):
        d = self.scene()
        d.active.metadata["text"] = {"content": "original"}
        snapshot = d.snapshot()
        self.assertIs(snapshot.active.image, d.active.image)
        d.active.transform.x = 20
        d.active.metadata["text"]["content"] = "new"
        self.assertEqual(snapshot.active.transform.x, 10)
        self.assertEqual(snapshot.active.metadata["text"]["content"], "original")
        before = snapshot.active.image.tobytes()
        brush(d.active, (22, 8), (22, 8), 2, "red")
        self.assertEqual(snapshot.active.image.tobytes(), before)

    def test_metadata_history_retains_many_moves_without_pixel_copies(self):
        d, history = self.scene(), History(budget=6000)
        for step in range(10):
            history.push(d, share_assets=True)
            d.active.transform.x = step
        self.assertEqual(len(history.undo_stack), 10)
        self.assertIs(history.undo_stack[0].active.image, d.active.image)
        d = history.undo(d)
        self.assertEqual(d.active.transform.x, 8)

    def test_pixel_edits_prune_old_asset_references(self):
        d, cache = self.scene(), PreviewCache()
        render(d, cache=cache)
        old = d.active.image
        d.active.image = Image.new("RGBA", (12, 12), "blue")
        render(d, cache=cache)
        self.assertFalse(any(old is ref for _, refs in cache.entries.values() for ref in refs))

    def test_adjustments_keep_source_pixels_and_identity(self):
        image = Image.new("RGBA", (8, 8), (70, 90, 120, 255))
        before = image.tobytes()
        for kind in ("Exposure", "Levels", "Curves"):
            np.testing.assert_array_equal(np.asarray(apply_adjustment(image, default_adjustment(kind))), np.asarray(image))
        self.assertEqual(image.tobytes(), before)

    def test_exposure_linear_light_matches_upstream_formula(self):
        data = default_adjustment("Exposure")
        data["exposureSettings"]["exposure"] = 1
        result = apply_adjustment(Image.new("RGBA", (1, 1), (128, 128, 128, 80)), data)
        self.assertEqual(result.getpixel((0, 0)), (176, 176, 176, 80))

    def test_levels_channel_order_and_inverted_output(self):
        data = default_adjustment("Levels")
        data["levels"]["ranges"][1]["gamma"] = 2
        data["levels"]["ranges"][0]["black"] = 50
        image = apply_adjustment(Image.new("RGBA", (1, 1), (64, 64, 64, 255)), data)
        expected = round((np.sqrt(64/255)*255-50)/205*255)
        self.assertEqual(image.getpixel((0, 0))[0], expected)
        data = default_adjustment("Levels")
        data["levels"]["ranges"][0].update(outputBlack=255, outputWhite=0)
        self.assertEqual(apply_adjustment(Image.new("RGBA", (1, 1), (20, 40, 60, 255)), data).getpixel((0, 0)), (235, 215, 195, 255))

    def test_curve_hermite_passes_points_without_overshoot(self):
        points = [dict(x=0, y=0), dict(x=80, y=120), dict(x=160, y=120), dict(x=255, y=255)]
        for point in points: self.assertAlmostEqual(curve_value(points, point["x"]), point["y"])
        self.assertTrue(all(abs(curve_value(points, x)-120) < 1e-8 for x in range(80, 161)))

    def test_adjustment_opacity_mask_and_cached_reedit(self):
        d = self.scene()
        layer = Layer("Invert", None, Transform(0, 0, 40, 30), metadata={"adjustment": default_adjustment("Invert")})
        d.layers.append(layer)
        cache = PreviewCache()
        self.assertEqual(render(d, cache=cache).getpixel((0, 0)), (105, 195, 235, 255))
        layer.opacity = .5
        self.assertAlmostEqual(render(d, cache=cache).getpixel((0, 0))[0], 127, delta=1)
        layer.mask = Image.new("L", (40, 30), 0)
        self.assertEqual(render(d, cache=cache).getpixel((0, 0)), (150, 60, 20, 255))
        layer.mask_enabled = False
        layer.metadata["adjustment"] = default_adjustment("Exposure")
        np.testing.assert_array_equal(np.asarray(render(d)), np.asarray(render(d, cache=cache)))

    def test_adjustment_and_text_project_roundtrip(self):
        scratch = Path(__file__).parent/"verification"/"test-scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            d = self.scene()
            d.layers.append(Layer("Curves", None, Transform(0, 0, 40, 30), metadata={"adjustment": default_adjustment("Curves")}))
            path = Path(temporary)/"new.comp"
            save_project(d, path)
            loaded = load_project(path)
            self.assertEqual(loaded.layers[-1].metadata["adjustment"]["kind"], "Curves")
            np.testing.assert_array_equal(np.asarray(render(d)), np.asarray(render(loaded)))

    def test_adjustment_masks_can_be_painted(self):
        layer = Layer("Invert", None, Transform(0, 0, 40, 30), metadata={"adjustment": default_adjustment("Invert")})
        brush(layer, (5, 5), (5, 5), 2, "red", on_mask=True, erase=True)
        self.assertEqual(layer.mask.getpixel((5, 5)), 0)
        self.assertEqual(layer.mask.getpixel((35, 25)), 255)


class PreviewInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.editor = Editor(self.root)
        self.editor.document = Document(100, 80)
        self.editor.document.add(Image.new("RGBA", (20, 20), "red"), "top")
        self.editor.fit_mode = False
        self.editor.zoom = 1
        self.editor.offset = (0, 0)
        self.editor.root.after_cancel(self.editor.refresh_job)
        self.editor.refresh_job = None

    def tearDown(self):
        self.editor.shutdown()
        self.root.destroy()

    def wait_preview(self):
        until = time.perf_counter()+3
        while self.editor.preview_signature != self.editor.preview_request and time.perf_counter() < until:
            self.root.update()
            time.sleep(.002)
        self.assertEqual(self.editor.preview_signature, self.editor.preview_request)

    def test_background_render_does_not_block_tk_callbacks(self):
        original = self.editor.preview_renderer.draw
        def slow(*args): time.sleep(.15); return original(*args)
        self.editor.preview_renderer.draw = slow
        self.editor.draw()
        called = []
        self.root.after(5, lambda: called.append(True))
        until = time.perf_counter()+.08
        while not called and time.perf_counter() < until:
            self.root.update(); time.sleep(.002)
        self.assertTrue(called)
        self.assertFalse(self.editor.preview_future[1].done())
        self.wait_preview()

    def test_many_requests_coalesce_and_newest_geometry_wins(self):
        self.editor.draw()
        for index in range(25):
            self.editor.document.active.transform.x = index
            self.editor.scene_revision += 1
            self.editor.draw()
        self.assertIsNotNone(self.editor.preview_pending)
        self.wait_preview()
        self.assertEqual(self.editor.preview_signature[1], self.editor.scene_revision)

    def test_opening_another_document_discards_old_worker_frame(self):
        original = self.editor.preview_renderer.draw
        def slow(*args): time.sleep(.04); return original(*args)
        self.editor.preview_renderer.draw = slow
        self.editor.draw()
        self.editor.document = Document(10, 10)
        self.editor.document.add(Image.new("RGBA", (10, 10), "blue"), "new")
        self.editor.scene_revision += 1
        self.editor.draw()
        self.wait_preview()
        self.assertEqual(self.editor.preview_rgb.getpixel((0, 0)), (0, 0, 255))

    def test_panning_reuses_photo_and_schedules_no_composition(self):
        self.editor.draw(); self.wait_preview()
        old_photo = self.editor.photo
        old_signature = self.editor.preview_signature
        self.editor.pan_down(SimpleNamespace(x=10, y=10))
        self.editor.pan_move(SimpleNamespace(x=11, y=11))
        self.assertIs(self.editor.photo, old_photo)
        self.assertEqual(self.editor.preview_signature, old_signature)
        self.assertIsNone(self.editor.preview_future)

    def test_continuous_events_do_not_restart_frame_deadline(self):
        self.editor.schedule_render()
        job = self.editor.refresh_job
        for _ in range(30): self.editor.schedule_render()
        self.assertEqual(self.editor.refresh_job, job)

    def test_live_text_cancel_and_accept_are_transactions(self):
        self.editor.add_text()
        dialog = self.editor.modal_edit
        dialog.content.delete("1.0", "end")
        dialog.content.insert("1.0", "new")
        dialog.preview()
        dialog.cancel()
        self.assertEqual(len(self.editor.document.layers), 1)
        self.assertFalse(self.editor.dirty)
        self.editor.add_text()
        self.editor.modal_edit.accept()
        self.assertEqual(len(self.editor.document.layers), 2)
        self.editor.undo()
        self.assertEqual(len(self.editor.document.layers), 1)

    def test_existing_text_edit_can_be_cancelled(self):
        self.editor.add_text()
        dialog = self.editor.modal_edit
        dialog.content.delete("1.0", "end"); dialog.content.insert("1.0", "original")
        dialog.accept()
        self.editor.add_text()
        dialog = self.editor.modal_edit
        dialog.content.delete("1.0", "end"); dialog.content.insert("1.0", "changed")
        dialog.preview(); dialog.cancel()
        self.assertEqual(self.editor.document.active.metadata["text"]["content"], "original")

    def test_accept_text_cancels_pending_callback_and_variable_traces(self):
        self.editor.add_text()
        dialog = self.editor.modal_edit
        dialog.size.set("25")
        job, size_variable = dialog.job, dialog.size
        self.assertIsNotNone(job)
        dialog.accept()
        pending = self.root.tk.call("after", "info")
        self.assertNotIn(job, pending)
        self.assertEqual(size_variable.trace_info(), [])

    def test_live_adjustment_accept_cancel_and_reedit(self):
        self.editor.new_adjustment("Exposure")
        dialog = self.editor.modal_edit
        dialog.values["exposure"].set(1)
        dialog.accept()
        self.assertEqual(self.editor.document.active.metadata["adjustment"]["exposureSettings"]["exposure"], 1)
        self.editor.edit_adjustment()
        dialog = self.editor.modal_edit
        dialog.values["exposure"].set(2)
        dialog.preview(); dialog.cancel()
        self.assertEqual(self.editor.document.active.metadata["adjustment"]["exposureSettings"]["exposure"], 1)

    def test_drag_corner_resize_and_undo(self):
        self.editor.paint_view()
        self.editor.pointer_down(SimpleNamespace(x=20, y=20, state=0))
        self.editor.pointer_move(SimpleNamespace(x=40, y=40, state=1))
        self.assertEqual(self.editor.document.active.transform.width, 40)
        self.assertEqual(self.editor.document.active.transform.height, 40)
        self.editor.pointer_up(SimpleNamespace(x=40, y=40, state=1))
        self.editor.undo()
        self.assertEqual(self.editor.document.active.transform.width, 20)


if __name__ == "__main__": unittest.main(verbosity=2)
