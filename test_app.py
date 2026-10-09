"""GUI action regressions with real Tk widgets and mocked file dialogs."""
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
import time
from unittest.mock import patch

from PIL import Image

from app import Editor
from engine import Document, render, load_project


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).parent/"verification"/"test-scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.folder = Path(self.temporary.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.editor = Editor(self.root)
        self.editor.document = Document(100, 80)
        self.editor.document.add(Image.new("RGBA", (100, 80), "red"), "背景")
        self.errors = patch("app.messagebox.showerror")
        self.error_mock = self.errors.start()

    def tearDown(self):
        self.editor.shutdown()
        self.root.destroy()
        self.errors.stop()
        self.temporary.cleanup()

    def wait_job(self):
        until=time.perf_counter()+5
        while self.editor.jobs.current and time.perf_counter()<until:
            self.root.update(); time.sleep(.002)
        self.assertIsNone(self.editor.jobs.current)

    def test_inspector_transform_opacity_blend_and_undo(self):
        for key, value in {"x": "15", "y": "10", "width": "80", "height": "64", "rotation": "35", "opacity": "50"}.items():
            self.editor.inspector[key].set(value)
        self.editor.blend.set("Multiply")
        self.editor.apply_inspector()
        self.assertFalse(self.error_mock.called)
        self.assertEqual(self.editor.document.active.transform.rotation, 35)
        self.assertEqual(self.editor.document.active.opacity, .5)
        self.editor.undo()
        self.assertEqual(self.editor.document.active.transform.x, 0)
        self.assertEqual(self.editor.document.active.opacity, 1)

    def test_invalid_inspector_does_not_change_document(self):
        self.editor.update_inspector()
        self.editor.inspector["width"].set("nan")
        self.editor.apply_inspector()
        self.assertTrue(self.error_mock.called)
        self.assertFalse(self.editor.dirty)
        self.assertEqual(self.editor.document.active.transform.width, 100)

    def test_import_save_export_and_reopen(self):
        source = self.folder/"test.png"
        Image.new("RGBA", (20, 20), "blue").save(source)
        with patch("app.filedialog.askopenfilenames", return_value=(str(source),)):
            self.editor.import_images()
        self.wait_job()
        self.assertEqual(len(self.editor.document.layers), 2)
        project = self.folder/"demo.comp"
        with patch("app.filedialog.asksaveasfilename", return_value=str(project)):
            self.editor.save()
        self.wait_job()
        self.assertFalse(self.editor.dirty)
        export = self.folder/"image.png"
        with patch("app.filedialog.asksaveasfilename", return_value=str(export)):
            self.editor.export()
        self.wait_job()
        self.assertTrue(export.is_file())
        self.assertEqual(len(load_project(project).layers), 2)
        with patch("app.filedialog.askdirectory", return_value=str(project)):
            self.editor.open_document()
        self.wait_job()
        self.assertFalse(self.error_mock.called)

    def test_failed_multi_import_keeps_document(self):
        source = self.folder/"valid.png"
        Image.new("RGBA", (20, 20), "blue").save(source)
        with patch("app.filedialog.askopenfilenames", return_value=(str(source), str(self.folder/"missing.png"))):
            self.editor.import_images()
        self.wait_job()
        self.assertTrue(self.error_mock.called)
        self.assertEqual(len(self.editor.document.layers), 1)
        self.assertFalse(self.editor.dirty)

    def test_mask_operations_and_pixel_filters(self):
        self.editor.add_mask()
        self.editor.invert_mask()
        self.assertEqual(render(self.editor.document).getpixel((0, 0))[3], 0)
        self.editor.toggle_mask()
        self.assertEqual(render(self.editor.document).getpixel((0, 0)), (255, 0, 0, 255))
        self.editor.adjust("invert")
        self.wait_job()
        self.assertEqual(self.editor.document.active.image.getpixel((0, 0)), (0, 255, 255, 255))
        self.assertFalse(self.error_mock.called)

    def test_crop_preserves_source_pixels_and_undo(self):
        self.editor.crop_box = (10, 20, 60, 70)
        self.editor.apply_crop()
        self.assertEqual((self.editor.document.width, self.editor.document.height), (50, 50))
        self.assertEqual(self.editor.document.active.image.size, (100, 80))
        self.assertEqual(self.editor.document.active.transform.x, -10)
        self.editor.undo()
        self.assertEqual(self.editor.document.width, 100)

    def test_duplicate_delete_reorder(self):
        self.editor.duplicate_layer()
        self.assertEqual(len(self.editor.document.layers), 2)
        selected = self.editor.document.active_id
        self.editor.reorder(-1)
        self.assertEqual(self.editor.document.layers[0].id, selected)
        self.editor.delete_layer()
        self.assertEqual(len(self.editor.document.layers), 1)
        self.editor.undo()
        self.assertEqual(len(self.editor.document.layers), 2)

    def test_editable_text_created(self):
        self.editor.add_text()
        dialog = self.editor.modal_edit
        dialog.content.delete("1.0", "end")
        dialog.content.insert("1.0", "中文测试")
        dialog.size.set("24")
        dialog.accept()
        self.assertEqual(len(self.editor.document.layers), 2)
        self.assertIsNotNone(self.editor.document.active.image.getbbox())
        self.assertEqual(self.editor.document.active.metadata["text"]["content"], "中文测试")
        self.assertFalse(self.error_mock.called)

    def test_cancel_save_keeps_unsaved_changes(self):
        self.editor.dirty = True
        with patch("app.messagebox.askyesnocancel", return_value=True), patch("app.filedialog.asksaveasfilename", return_value=""):
            self.assertFalse(self.editor.confirm_discard())
        self.assertTrue(self.editor.dirty)


if __name__ == "__main__":
    unittest.main(verbosity=2)
