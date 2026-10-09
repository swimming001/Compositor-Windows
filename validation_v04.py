"""Verify the release's native libraries and new features in source or frozen form."""
import copy
import json
from pathlib import Path
import tkinter as tk
import numpy as np
from PIL import Image
from validation_v03 import verify as verify_previous
from engine import Document,Layer,Transform,render,load_project,save_project
from adjustments import KINDS,default_adjustment
from psdio import load_psd
from psdexport import export_psd
from psd_compat import NATIVE
from rawio import export_raw_tiff,decode_raw
from typography import text_image
from app import Editor
from version import VERSION


def verify(output,assets):
    output,assets=Path(output),Path(assets)
    result=verify_previous(output,assets)
    d=Document(80,70);d.add(Image.new("RGBA",(80,70),(70,90,130,255)),"中文😀像素")
    for kind in NATIVE:d.layers.append(Layer("中文调整 · "+kind,None,Transform(0,0,80,70),metadata={"adjustment":default_adjustment(kind)}))
    export_psd(d,output/"native-eight.psd");q,notes=load_psd(output/"native-eight.psd")
    if notes or [l.name for l in d.layers]!=[l.name for l in q.layers]:raise RuntimeError("Native PSD names/types did not roundtrip")
    np.testing.assert_array_equal(np.asarray(render(d)),np.asarray(render(q)))
    d.layers.append(Layer("motion",None,Transform(0,0,80,70),metadata={"adjustment":default_adjustment("Motion Blur")}))
    style=dict(content="中文文字",fontName="MicrosoftYaHei",fontSize=18,red=1,green=1,blue=1)
    d.add(text_image(style),"文字").metadata["text"]=style
    notes=export_psd(d,output/"compatible.psd",True);q,_=load_psd(output/"compatible.psd")
    if not notes or len(q.layers)!=2 or not q.active.metadata.get("text"):raise RuntimeError("Compatible PSD did not preserve upper text")
    np.testing.assert_array_equal(np.asarray(render(d)),np.asarray(render(q)))
    raw=next(assets.glob("*.KDC"));tiff=output/"raw16.tif"
    export_raw_tiff(raw,tiff);expected=decode_raw(raw,output_bits=16)
    with Image.open(tiff) as im:
        if tuple(im.tag_v2[258])!=(16,16,16):raise RuntimeError("TIFF is not RGB16")
        offset=im.tag_v2[273][0]
    np.testing.assert_array_equal(np.frombuffer(tiff.read_bytes()[offset:],dtype="<u2").reshape(expected.shape),expected)
    if not np.count_nonzero(expected%256):raise RuntimeError("RAW lost sixteen-bit precision")
    root=tk.Tk();root.withdraw();editor=Editor(root);errors=[]
    root.report_callback_exception=lambda *args:errors.append(str(args[1]))
    import app
    original=app.messagebox.showerror
    app.messagebox.showerror=lambda title,message,**kwargs:errors.append(str(message))
    try:
        editor.document=Document(80,70);editor.document.add(Image.new("RGBA",(80,70),"navy"),"底图")
        for kind in KINDS:
            editor.new_adjustment(kind);editor.modal_edit.accept();root.update()
        data=copy.deepcopy(default_adjustment("Curves"))
        data["curves"]["channels"][0]=[dict(x=0,y=20),dict(x=75,y=120),dict(x=170,y=140),dict(x=255,y=230)]
        editor.document.active.metadata["adjustment"]=data;editor.edit_adjustment();editor.modal_edit.accept()
        if editor.document.active.metadata["adjustment"]!=data:raise RuntimeError("Curve points lost")
        save_project(editor.document,output/"all-adjustments.comp")
        loaded=load_project(output/"all-adjustments.comp")
        np.testing.assert_array_equal(np.asarray(render(loaded)),np.asarray(render(editor.document)))
        if errors:raise RuntimeError("GUI errors: "+"; ".join(errors))
    finally:
        app.messagebox.showerror=original;editor.shutdown();root.destroy()
    from validation_save import verify as verify_saving
    result.update(verify_saving(output))
    result.update(version=VERSION,adjustment_types=12,native_psd_adjustment_types=8,
                  chinese_psd_names=True,compatible_psd_export=True,raw16_tiff=True,
                  all_adjustment_dialogs=True,curve_points_preserved=True,tk_errors=errors)
    (output/"verification-result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return result
