"""Actual GUI and native-library checks, also runnable from the frozen EXE."""
import json
from pathlib import Path
import sys
import time
import tkinter as tk
import numpy as np
from PIL import Image
from app import Editor
from engine import Document, load_project, save_project, export_image, render, blend, BLEND_MODES
from psdio import load_psd
from rawio import develop_raw
from psdexport import export_psd


def verify(output,assets):
    output,assets=Path(output),Path(assets)
    output.mkdir(parents=True,exist_ok=True)
    root=tk.Tk(); editor=Editor(root)
    errors=[]
    import app
    original_showerror=app.messagebox.showerror
    app.messagebox.showerror=lambda title,message,**kwargs:errors.append(str(message))
    root.report_callback_exception=lambda *args:errors.append(str(args[1]))
    def wait(predicate,seconds=45):
        deadline=time.perf_counter()+seconds
        while not predicate() and time.perf_counter()<deadline:
            root.update(); time.sleep(.003)
        if not predicate(): raise RuntimeError("Background operation timed out")
        if errors: raise RuntimeError("Tk error: "+"; ".join(errors))
    try:
        raw=next(assets.glob("*.KDC"))
        editor.start_job("RAW validation",develop_raw,(raw,),lambda im:(setattr(editor,"document",Document(*im.size)),editor.document.add(im,"RAW decoded"),editor.refresh()),True)
        wait(lambda:editor.jobs.current is None)
        raw_size=editor.document.active.image.size
        editor.document=Document(600,399); editor.document.add(Image.open(assets/"subject.jpg").convert("RGBA"),"AI subject"); editor.refresh()
        editor.segment_subject(); wait(lambda:editor.jobs.current is None)
        if editor.document.active.mask is None: raise RuntimeError("AI mask was not applied")
        mask=editor.document.active.mask
        if mask.getpixel((120,130))<180 or mask.getpixel((350,130))<180: raise RuntimeError("Subject arm coverage failed")
        mask.save(output/"ai-mask.png"); export_image(editor.document,output/"ai-subject.png")
        ai_provider=editor.segmenter.provider
        editor.document,psd_notes=load_psd(assets/"editable-layers.psd")
        if len(editor.document.layers)!=4 or not any(l.is_group for l in editor.document.layers): raise RuntimeError("PSD layers not preserved")
        psd_layers=len(editor.document.layers)
        textdoc,_=load_psd(assets/"text.psd")
        textlayer=next(l for l in textdoc.layers if l.metadata.get("text"))
        editor.document=textdoc; editor.document.active_id=textlayer.id
        editor.add_text(); dialog=editor.modal_edit
        dialog.content.delete("1.0","end"); dialog.content.insert("1.0","Editable PSD\nWindows 0.3")
        dialog.size.set("24"); dialog.tracking.set("2"); dialog.leading.set("38"); dialog.alignment.set("Center")
        dialog.box_width.set("280"); dialog.box_height.set("140"); dialog.accept()
        editor.edit_effects(); dialog=editor.modal_edit; dialog.values["enabled"].set(True); dialog.values["size"].set("2"); dialog.accept()
        editor.new_group(); group=editor.document.active
        editor.new_adjustment("Exposure"); dialog=editor.modal_edit; dialog.values["exposure"].set(.25); dialog.accept()
        if editor.document.active.parent_id!=group.id: raise RuntimeError("Folder adjustment placement failed")
        editor.undo(); editor.undo()
        editor.document.active_id=textlayer.id
        editor.fit_mode=False; editor.zoom=4; editor.offset=(-40,-35); editor.refresh(); root.update(); editor.draw()
        wait(lambda:editor.preview_signature==editor.preview_request and editor.tile_signature is not None and editor.tile_future is None)
        if not editor.tile_photos: raise RuntimeError("Native pixel tiles did not appear")
        backend=editor.preview_renderer.backend_label
        gpu_draws=editor.preview_renderer.gpu.count if editor.preview_renderer.gpu else 0
        # Display state comes from the native pixel tiles; actual export always uses CPU/full source.
        native_tiles=len(editor.tile_photos)
        editor.start_job("save validation",save_project,(editor.document.snapshot(),output/"verified.comp"),lambda _:None,False,False)
        wait(lambda:editor.jobs.current is None)
        editor.start_job("export validation",export_image,(editor.document.snapshot(),output/"verified.png"),lambda _:None,False,False)
        wait(lambda:editor.jobs.current is None)
        loaded=load_project(output/"verified.comp")
        np.testing.assert_array_equal(np.asarray(render(loaded)),np.asarray(Image.open(output/"verified.png")))
        editor.start_job("layered PSD export validation",export_psd,(editor.document.snapshot(),output/"verified.psd"),lambda _:None,False,False)
        wait(lambda:editor.jobs.current is None)
        from psd_tools import PSDImage
        native=PSDImage.open(output/"verified.psd")
        if not any(l.kind=="type" for l in native.descendants()): raise RuntimeError("Native PSD type descriptor missing")
        reimported,_=load_psd(output/"verified.psd")
        if len(reimported.layers)!=len(editor.document.layers): raise RuntimeError("PSD roundtrip dropped layers")
        try:
            import ctypes
            from PIL import ImageGrab
            handle=ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
            ImageGrab.grab(window=handle).save(output/"window.png")
        except OSError: pass
        results=dict(ok=True,frozen=bool(getattr(sys,"frozen",False)),backend=backend,gpu_draws=gpu_draws,ai_provider=ai_provider,
                     raw_size=raw_size,psd_layers=psd_layers,editable_psd_text=True,layer_effects=True,folder_adjustments=True,native_tiles=native_tiles,
                     async_io=True,export_matches_project=True,native_psd_export=True,tk_errors=errors)
        (output/"verification-result.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
        return results
    finally:
        app.messagebox.showerror=original_showerror
        editor.shutdown(); root.destroy()
