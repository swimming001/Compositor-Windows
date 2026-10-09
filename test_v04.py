"""Regressions for release 0.4: new adjustments, export fidelity and editing failures."""
import copy
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image,ImageDraw
from engine import Document,Layer,Transform,render,save_project,load_project,translate_group,validate_structure
from adjustments import KINDS,default_adjustment,apply_adjustment,validate_adjustment
from psdio import load_psd
from psdexport import export_psd
from tiles import render_tiled
from app import Editor

ROOT=Path(__file__).resolve().parent


class ExtendedColorTests(unittest.TestCase):
    def test_hue_rotation_targets_red_and_leaves_blue(self):
        im=Image.new("RGBA",(2,1));im.putdata([(255,0,0,80),(0,0,255,160)])
        data=default_adjustment("Hue/Saturation");data["hsvSettings"]=dict(range="Reds",adjustments={"Reds":dict(hue=120,saturation=0,lightness=0)})
        out=apply_adjustment(im,data)
        self.assertEqual(out.getpixel((0,0)),(0,255,0,80));self.assertEqual(out.getpixel((1,0)),(0,0,255,160))

    def test_hue_desaturation_and_white_black_extremes(self):
        im=Image.new("RGBA",(1,1),(240,100,40,70))
        data=default_adjustment("Hue/Saturation");data["saturation"]=-100
        self.assertEqual(apply_adjustment(im,data).getpixel((0,0)),(140,140,140,70))
        for light,expected in ((100,255),(-100,0)):
            data["lightness"]=light;self.assertEqual(apply_adjustment(im,data).getpixel((0,0)),(expected,expected,expected,70))

    def test_gradient_endpoints_reverse_and_alpha(self):
        im=Image.new("RGBA",(2,1));im.putdata([(0,0,0,70),(255,255,255,90)])
        data=default_adjustment("Gradient Map");data["gradientMapSettings"].update(shadows=dict(red=1,green=0,blue=0),highlights=dict(red=0,green=0,blue=1))
        self.assertEqual(list(apply_adjustment(im,data).get_flattened_data()),[(255,0,0,70),(0,0,255,90)])
        data["gradientMapSettings"]["reversed"]=True
        self.assertEqual(list(apply_adjustment(im,data).get_flattened_data()),[(0,0,255,70),(255,0,0,90)])

    def test_black_white_six_primary_and_secondary_weights(self):
        im=Image.new("RGBA",(6,1));im.putdata([(255,0,0,255),(255,255,0,255),(0,255,0,255),(0,255,255,255),(0,0,255,255),(255,0,255,255)])
        out=apply_adjustment(im,default_adjustment("Black & White"))
        self.assertEqual([p[0] for p in out.get_flattened_data()],[102,153,102,153,51,204])
        self.assertTrue(all(p[0]==p[1]==p[2] for p in out.get_flattened_data()))

    def test_color_balance_preserves_luminance_without_touching_alpha(self):
        im=Image.new("RGBA",(1,1),(90,110,100,180));data=default_adjustment("Color Balance")
        data["colorBalanceSettings"]["midCyanRed"]=30
        before=np.asarray(im)[0,0,:3]@np.array([.299,.587,.114]);after=apply_adjustment(im,data).getpixel((0,0))
        self.assertGreater(after[0],90);self.assertLess(abs(np.array(after[:3])@np.array([.299,.587,.114])-before),1);self.assertEqual(after[3],180)

    def test_noise_and_grain_tiles_match_whole_and_seed_changes_pattern(self):
        for kind in ("Grain","Add Noise"):
            d=Document(420,100);d.add(Image.new("RGBA",(420,100),(90,110,100,180)),"pixels")
            data=default_adjustment(kind)
            d.layers.append(Layer(kind,None,Transform(0,0,420,100),metadata={"adjustment":data}))
            np.testing.assert_array_equal(np.asarray(render(d)),np.asarray(render_tiled(d)))
            before=render(d).tobytes()
            if kind=="Grain":data["grainSettings"]["seed"]=99
            else:data["noiseSeed"]=99;data["noiseGaussian"]=True;data["noiseMonochromatic"]=True
            self.assertNotEqual(render(d).tobytes(),before)

    def test_motion_blur_has_support_beyond_impulse_and_matches_tile_seams(self):
        d=Document(420,80);im=Image.new("RGBA",(420,80));ImageDraw.Draw(im).rectangle((375,25,395,55),fill="white");d.add(im,"impulse")
        data=default_adjustment("Motion Blur")|dict(motionDistance=30,motionAngle=35)
        d.layers.append(Layer("motion",None,Transform(0,0,420,80),metadata={"adjustment":data}))
        out=render(d);self.assertLess(out.getbbox()[0],375);self.assertGreater(out.getbbox()[2],396)
        self.assertLessEqual(np.abs(np.asarray(out).astype(int)-np.asarray(render_tiled(d)).astype(int)).max(),1)

    def test_every_adjustment_roundtrips_project_with_parameters(self):
        with tempfile.TemporaryDirectory(dir=ROOT/"verification") as folder:
            d=Document(30,24);d.add(Image.new("RGBA",(30,24),(70,90,130,170)),"pixels")
            for kind in KINDS:d.layers.append(Layer(kind,None,Transform(0,0,30,24),metadata={"adjustment":default_adjustment(kind)}))
            save_project(d,Path(folder)/"all.comp");q=load_project(Path(folder)/"all.comp")
            self.assertEqual([l.metadata for l in d.layers],[l.metadata for l in q.layers]);np.testing.assert_array_equal(np.asarray(render(d)),np.asarray(render(q)))

    def test_invalid_noise_seed_and_color_parameters_rejected(self):
        for key,value in (("noiseSeed",-1),("noiseSeed",2**32),("noiseSeed",.2),("noiseAmount",float("nan")),("noiseGaussian","false")):
            with self.assertRaises(ValueError):validate_adjustment(default_adjustment("Add Noise")|{key:value})


class ExportAndFallbackTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory(dir=ROOT/"verification");self.path=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_kerned_text_right_alignment_uses_drawn_run_width(self):
        from typography import text_image,font
        style=dict(content="AVAV",fontName="ArialMT",fontSize=48,red=1,green=1,blue=1,alignment="Right",boxSize=[300,100])
        result=text_image(style);expected=Image.new("RGBA",(300,100));face=font("ArialMT",48)
        ImageDraw.Draw(expected).text((300-12-face.getlength("AVAV"),12+face.getmetrics()[0]),"AVAV",font=face,anchor="ls",fill="white")
        np.testing.assert_array_equal(np.asarray(result),np.asarray(expected))

    def test_integer_translation_keeps_semitransparent_source_rgb(self):
        from engine import placed
        im=Image.new("RGBA",(2,1));im.putdata([(163,163,163,146),(170,90,40,83)])
        for _ in range(5):im=placed(im,Transform(0,0,2,1),(2,1))
        self.assertEqual(list(im.get_flattened_data()),[(163,163,163,146),(170,90,40,83)])

    def test_native_psd_chinese_names_and_eight_adjustment_types(self):
        from psd_compat import NATIVE
        d=Document(50,40);d.add(Image.new("RGBA",(50,40),(70,90,130,255)),"中文😀像素")
        for kind in NATIVE:d.layers.append(Layer("中文调整 · "+kind,None,Transform(0,0,50,40),metadata={"adjustment":default_adjustment(kind)}))
        export_psd(d,self.path/"中文.psd");q,notes=load_psd(self.path/"中文.psd")
        self.assertFalse(notes);self.assertEqual([l.name for l in d.layers],[l.name for l in q.layers]);np.testing.assert_array_equal(np.asarray(render(d)),np.asarray(render(q)))

    def test_compatible_psd_bakes_blur_prefix_and_keeps_upper_type_editable(self):
        from typography import text_image
        d=Document(80,70);d.add(Image.new("RGBA",(80,70),"navy"),"base")
        d.layers.append(Layer("blur",None,Transform(0,0,80,70),metadata={"adjustment":default_adjustment("Gaussian Blur")}))
        style=dict(content="字",fontName="MicrosoftYaHei",fontSize=28,red=1,green=1,blue=1);d.add(text_image(style),"中文文字").metadata["text"]=style
        before=d.snapshot();notes=export_psd(d,self.path/"compatible.psd",True);q,_=load_psd(self.path/"compatible.psd")
        self.assertTrue(notes);self.assertEqual(len(q.layers),2);self.assertIn("text",q.active.metadata)
        np.testing.assert_array_equal(np.asarray(render(d)),np.asarray(render(q)));self.assertEqual([l.id for l in d.layers],[l.id for l in before.layers])

    def test_compatible_psd_clipping_fallback_keeps_visible_result(self):
        d=Document(40,30);base=d.add(Image.new("RGBA",(20,30),"red"),"base");top=d.add(Image.new("RGBA",(40,30),"blue"),"clip");top.clipping_id=base.id
        d.layers.append(Layer("grain",None,Transform(0,0,40,30),metadata={"adjustment":default_adjustment("Grain")}))
        notes=export_psd(d,self.path/"flat.psd",True);q,_=load_psd(self.path/"flat.psd")
        self.assertTrue(notes);self.assertEqual(len(q.layers),1);np.testing.assert_array_equal(np.asarray(render(d)),np.asarray(render(q)))

    def test_empty_psd_and_long_names_export_without_partial_file(self):
        d=Document(12,10);self.assertTrue(export_psd(d,self.path/"blank.psd"));q,_=load_psd(self.path/"blank.psd");self.assertIsNone(render(q).getbbox())
        d.add(Image.new("RGBA",(1,1),"red"),"😀"*200);self.assertTrue(export_psd(d,self.path/"long.psd"));q,_=load_psd(self.path/"long.psd")
        self.assertLessEqual(len(q.active.name.encode("utf-16-le")),510)

    def test_raw16_tiff_preserves_every_decoded_word(self):
        from rawio import export_raw_tiff,decode_raw
        raw=next((ROOT/"verification/v0.3-fixtures").glob("*.KDC"));path=self.path/"raw16.tif"
        self.assertEqual(export_raw_tiff(raw,path),(768,512));expected=decode_raw(raw,output_bits=16)
        with Image.open(path) as im:self.assertEqual(tuple(im.tag_v2[258]),(16,16,16));offset=im.tag_v2[273][0]
        np.testing.assert_array_equal(np.frombuffer(path.read_bytes()[offset:],dtype="<u2").reshape(expected.shape),expected)
        self.assertGreater(np.count_nonzero(expected%256),1000)

    def test_raw16_failure_keeps_existing_target(self):
        from rawio import export_raw_tiff
        path=self.path/"original.tif";path.write_bytes(b"keep")
        with patch("rawio.decode_raw",side_effect=OSError("RAW read failed")),self.assertRaises(OSError):export_raw_tiff("missing.raw",path)
        self.assertEqual(path.read_bytes(),b"keep")

    def test_gpu_failure_including_cleanup_failure_returns_cpu_frame(self):
        from preview import PreviewRenderer
        renderer=PreviewRenderer();renderer.initialized=True;renderer.gpu=SimpleNamespace(blend=lambda *args:(_ for _ in ()).throw(RuntimeError("GPU lost")),close=lambda:(_ for _ in ()).throw(RuntimeError("context lost")))
        d=Document(4,4);d.add(Image.new("RGBA",(4,4),"red"),"base")
        np.testing.assert_array_equal(np.asarray(renderer.raster(d)),np.asarray(render(d)));self.assertIsNone(renderer.gpu)

    def test_directml_runtime_failure_retries_cpu(self):
        from segmentation import Segmenter
        segment=Segmenter();segment.provider="DmlExecutionProvider"
        fault=SimpleNamespace(get_inputs=lambda:[SimpleNamespace(name="image")],run=lambda *args:(_ for _ in ()).throw(RuntimeError("DML device lost")))
        output=np.arange(320*320,dtype=np.float32).reshape(1,1,320,320)
        cpu=SimpleNamespace(get_inputs=lambda:[SimpleNamespace(name="image")],run=lambda *args:[output])
        segment.session=fault
        def initialize(force_cpu=False):
            if force_cpu:segment.session=cpu;segment.provider="CPUExecutionProvider"
        with patch.object(segment,"initialize",side_effect=initialize) as called:mask=segment.mask(Image.new("RGB",(20,20),"red"))
        self.assertEqual(segment.provider,"CPUExecutionProvider");self.assertGreater(mask.getextrema()[1],200);called.assert_any_call(force_cpu=True)


class AdvancedInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw();self.editor=Editor(self.root);self.editor.document=Document(200,100)
        self.editor.document.add(Image.new("RGBA",(200,100),"red"),"base")
        self.errors=patch("app.messagebox.showerror");self.error=self.errors.start()
    def tearDown(self):self.editor.shutdown();self.root.destroy();self.errors.stop()

    def test_all_twelve_dialogs_apply_and_cancel_without_tk_errors(self):
        callbacks=[];self.root.report_callback_exception=lambda *args:callbacks.append(args)
        for kind in KINDS:
            self.editor.new_adjustment(kind)
            if self.editor.modal_edit:self.editor.modal_edit.accept()
            self.assertEqual(self.editor.document.active.metadata["adjustment"]["kind"],kind)
            self.editor.undo();self.root.update()
        self.assertFalse(callbacks);self.assertFalse(self.error.called)

    def test_curves_accept_preserves_all_imported_points_and_channels(self):
        data=default_adjustment("Curves");data["curves"]["channels"][0]=[dict(x=0,y=20),dict(x=75,y=120),dict(x=170,y=140),dict(x=255,y=230)]
        layer=Layer("curve",None,Transform(0,0,200,100),metadata={"adjustment":copy.deepcopy(data)});self.editor.document.layers.append(layer);self.editor.document.active_id=layer.id
        self.editor.edit_adjustment();dialog=self.editor.modal_edit;dialog.accept();self.assertEqual(layer.metadata["adjustment"],data)

    def test_levels_channels_and_outputs_remain_independent(self):
        self.editor.new_adjustment("Levels");dialog=self.editor.modal_edit
        dialog.values["outputBlack"].set(17);dialog.channel.set("Red");dialog.change_channel()
        dialog.values["black"].set(40);dialog.values["outputWhite"].set(220);dialog.accept()
        values=self.editor.document.active.metadata["adjustment"]["levels"]["ranges"]
        self.assertEqual(values[0]["outputBlack"],17);self.assertEqual(values[1]["black"],40);self.assertEqual(values[1]["outputWhite"],220);self.assertEqual(values[2]["black"],0)

    def test_group_translation_moves_nested_pixels_and_independent_masks_once(self):
        self.editor.new_group();g=self.editor.document.active
        leaf=self.editor.document.add(Image.new("RGBA",(10,10),"blue"),"inside");leaf.parent_id=g.id;leaf.mask=Image.new("L",(10,10),255);leaf.mask_placement=Transform(2,3,10,10)
        self.editor.document.active_id=g.id;self.editor.update_inspector();self.editor.inspector["x"].set(20);self.editor.inspector["y"].set(15);self.editor.apply_inspector()
        self.assertEqual((leaf.transform.x,leaf.transform.y),(20,15));self.assertEqual((leaf.mask_placement.x,leaf.mask_placement.y),(22,18));self.editor.undo()
        restored=next(l for l in self.editor.document.layers if l.id==leaf.id);self.assertEqual((restored.transform.x,restored.transform.y),(0,0))

    def test_group_reorder_keeps_members_and_move_rejects_clip_cycle(self):
        self.editor.new_group();g=self.editor.document.active
        child=self.editor.document.add(Image.new("RGBA",(5,5),"blue"),"child");child.parent_id=g.id
        upper=self.editor.document.add(Image.new("RGBA",(5,5),"green"),"upper");upper.clipping_id=g.id
        self.editor.document.active_id=g.id;self.editor.reorder(-1);validate_structure(self.editor.document);self.assertEqual(child.parent_id,g.id)
        self.editor.document.active_id=upper.id
        with patch("extended_dialogs.GroupDialog",return_value=SimpleNamespace(result=g.id)):self.editor.move_to_group()
        self.assertTrue(self.error.called);self.assertIsNone(self.editor.document.active.parent_id)

    def test_duplicate_budget_counts_masks_and_does_not_edit_on_failure(self):
        active=self.editor.document.active;active.mask=Image.new("L",active.image.size,255)
        with patch("app.MAX_ASSET_PIXELS",60000):self.editor.duplicate_layer()
        self.assertTrue(self.error.called);self.assertEqual(len(self.editor.document.layers),1)


if __name__=="__main__":unittest.main(verbosity=2)
