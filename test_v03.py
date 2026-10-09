"""Real feature regressions: GPU parity, native tiles, RAW/AI and async safety."""
import copy
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image, ImageDraw

from engine import Document, Layer, Transform, BLEND_MODES, render, blend, load_project, save_project
from effects import default_effect, apply_effects
from adjustments import default_adjustment
from tiles import render_tile, render_tiled
from typography import text_image, replace_content, set_range, utf16_length, unit_styles
from psdio import load_psd
from app import Editor

ROOT=Path(__file__).parent
FIXTURES=ROOT/"verification/v0.3-fixtures"


def layered_fixture(path):
    from psd_tools import PSDImage
    from psd_tools.constants import BlendMode
    psd=PSDImage.new("RGB",(64,48),color=(0,0,0))
    psd.create_pixel_layer(Image.new("RGB",(64,48),(40,50,60)),name="Background")
    group=psd.create_group(name="Editable folder",blend_mode=BlendMode.NORMAL,opacity=180)
    layer=group.create_pixel_layer(Image.new("RGB",(20,18),(100,150,200)),name="Masked pixels",top=9,left=11)
    mask=Image.new("L",(20,18),255); mask.putpixel((0,0),0)
    layer.create_mask(mask)
    layer.opacity=190
    hidden=psd.create_pixel_layer(Image.new("RGB",(10,10),(255,0,0)),name="Hidden",top=4,left=5)
    hidden.visible=False
    psd.save(path)
    return path


class AdvancedEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/"verification")
        self.path=Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()

    def scene(self):
        d=Document(780,490)
        d.add(Image.new("RGBA",(780,490),(40,70,110,255)),"base")
        im=Image.new("RGBA",(150,90)); ImageDraw.Draw(im).ellipse((5,5,145,85),fill=(210,110,60,190))
        layer=d.add(im,"ellipse"); layer.transform.x,layer.transform.y,layer.transform.rotation=330,190,23
        return d

    def test_native_tiles_match_full_render_with_rotation_mask_and_blend(self):
        d=self.scene(); d.active.mask=Image.new("L",(1,1),170); d.active.blend="Overlay"
        np.testing.assert_array_equal(np.asarray(render(d)),np.asarray(render_tiled(d)))

    def test_tile_halos_match_blur_and_all_effects_at_seams(self):
        d=self.scene(); d.active.metadata["effects"]={k:default_effect(k) for k in ("stroke","shadow","colorOverlay","innerShadow","outerGlow","innerGlow")}
        d.layers.append(Layer("blur",None,Transform(0,0,780,490),metadata={"adjustment":default_adjustment("Gaussian Blur")|{"blurRadius":8}}))
        full,tiled=np.asarray(render(d)).astype(int),np.asarray(render_tiled(d)).astype(int)
        self.assertLessEqual(np.abs(full-tiled).max(),1)

    def test_native_tile_contains_original_detail_lost_in_thumbnail(self):
        d=Document(800,600)
        pixels=np.zeros((600,800,4),dtype=np.uint8); pixels[...,3]=255; pixels[:,::2,:3]=255
        d.add(Image.fromarray(pixels),"stripes")
        tile=render_tile(d,(384,0,768,384))
        self.assertEqual(tile.getpixel((0,0)),(255,255,255,255))
        self.assertEqual(tile.getpixel((1,0)),(0,0,0,255))
        self.assertNotEqual(render(d,(80,60)).getpixel((38,0)),tile.getpixel((0,0)))

    def test_all_six_effects_change_appearance_without_touching_pixels(self):
        im=Image.new("RGBA",(64,64)); ImageDraw.Draw(im).rectangle((18,18,45,45),fill=(150,80,40,255))
        for kind in ("stroke","shadow","colorOverlay","innerShadow","outerGlow","innerGlow"):
            before=im.tobytes(); effects={kind:default_effect(kind)}
            result=apply_effects(im,effects)
            self.assertNotEqual(result.tobytes(),before,kind)
            self.assertEqual(im.tobytes(),before)
            effects[kind]["enabled"]=False
            self.assertEqual(apply_effects(im,effects).tobytes(),before)

    def test_inside_stroke_keeps_center_and_colors_edge(self):
        im=Image.new("RGBA",(30,30)); ImageDraw.Draw(im).rectangle((5,5,24,24),fill="red")
        result=apply_effects(im,{"stroke":default_effect("stroke")|dict(inside=True,size=3)})
        self.assertEqual(result.getpixel((15,15)),(255,0,0,255))
        self.assertEqual(result.getpixel((5,15)),(0,0,0,255))

    def test_group_adjustment_does_not_touch_outside_background(self):
        d=Document(20,10); d.add(Image.new("RGBA",(20,10),"red"),"outside")
        group=Layer("folder",None,Transform(0,0,20,10),is_group=True); d.layers.append(group)
        layer=d.add(Image.new("RGBA",(10,10),"blue"),"inside"); layer.parent_id=group.id
        d.layers.append(Layer("invert",None,Transform(0,0,20,10),parent_id=group.id,metadata={"adjustment":default_adjustment("Invert")}))
        self.assertEqual(render(d).getpixel((0,0)),(255,255,0,255))
        self.assertEqual(render(d).getpixel((15,0)),(255,0,0,255))
        save_project(d,self.path/"group.comp")
        np.testing.assert_array_equal(np.asarray(render(d)),np.asarray(render(load_project(self.path/"group.comp"))))

    def test_group_opacity_applied_once_after_adjustments(self):
        d=Document(4,4)
        g=Layer("folder",None,Transform(0,0,4,4),is_group=True,opacity=.5); d.layers.append(g)
        l=d.add(Image.new("RGBA",(4,4),"red"),"inside"); l.parent_id=g.id
        d.layers.append(Layer("invert",None,Transform(0,0,4,4),parent_id=g.id,metadata={"adjustment":default_adjustment("Invert")}))
        self.assertEqual(render(d).getpixel((0,0)),(0,255,255,128))

    def test_group_effects_and_text_roundtrip(self):
        d=self.scene(); d.active.metadata["effects"]={"shadow":default_effect("shadow")}
        style=dict(content="Aa😀中",fontName="ArialMT",fontSize=30,red=1,green=0,blue=0,alignment="Center",tracking=2,leading=40,boxSize=[180,100],fontRuns=[dict(location=4,length=1,fontName="MicrosoftYaHei")])
        d.add(text_image(style),"type").metadata["text"]=style
        save_project(d,self.path/"rich.comp")
        loaded=load_project(self.path/"rich.comp")
        self.assertEqual(loaded.active.metadata["text"],style)
        np.testing.assert_array_equal(np.asarray(render(d)),np.asarray(render(loaded)))

    def test_psd_preserves_real_folders_layers_masks_visibility_and_positions(self):
        path=layered_fixture(self.path/"layers.psd")
        d,notes=load_psd(path)
        self.assertEqual(len(d.layers),4)
        group=next(l for l in d.layers if l.is_group)
        leaf=next(l for l in d.layers if l.name=="Masked pixels")
        self.assertEqual(leaf.parent_id,group.id)
        self.assertEqual((leaf.transform.x,leaf.transform.y),(11,9))
        self.assertIsNotNone(leaf.mask); self.assertEqual(leaf.mask.getpixel((0,0)),0)
        self.assertFalse(next(l for l in d.layers if l.name=="Hidden").visible)
        self.assertAlmostEqual(leaf.opacity,190/255)
        before=render(d).tobytes()
        leaf.transform.x+=15
        self.assertNotEqual(render(d).tobytes(),before)
        save_project(d,self.path/"converted.comp")
        self.assertEqual(len(load_project(self.path/"converted.comp").layers),4)

    def test_real_raw_decode_and_exposure(self):
        from rawio import develop_raw
        path=next(FIXTURES.glob("*.KDC"))
        a,b=develop_raw(path),develop_raw(path,exposure=1)
        self.assertEqual(a.size,(768,512)); self.assertEqual(a.mode,"RGBA")
        self.assertGreater(np.asarray(b)[...,:3].mean(),np.asarray(a)[...,:3].mean())
        self.assertEqual(develop_raw(path,half_size=True).size,(384,256))

    def test_group_clip_base_uses_live_children_and_invalidates_cached_alpha(self):
        from preview import PreviewCache
        d=Document(20,10)
        group=Layer("base folder",None,Transform(0,0,20,10),is_group=True);d.layers.append(group)
        leaf=d.add(Image.new("RGBA",(10,10),"red"),"base pixels");leaf.parent_id=group.id
        top=d.add(Image.new("RGBA",(20,10),"blue"),"clipped");top.clipping_id=group.id
        cache=PreviewCache();first=render(d,cache=cache)
        self.assertEqual(first.getpixel((0,0)),(0,0,255,255));self.assertEqual(first.getpixel((15,0))[3],0)
        leaf.transform.x=10
        after=render(d,cache=cache)
        self.assertEqual(after.getpixel((0,0))[3],0);self.assertEqual(after.getpixel((15,0)),(0,0,255,255))
        np.testing.assert_array_equal(np.asarray(after),np.asarray(render(d)))
        save_project(d,self.path/"groupclip.comp")
        np.testing.assert_array_equal(np.asarray(after),np.asarray(render(load_project(self.path/"groupclip.comp"))))

    def test_clipped_folder_applies_base_alpha_once(self):
        d=Document(20,10);base=d.add(Image.new("RGBA",(10,10),"red"),"base")
        group=Layer("clipped group",None,Transform(0,0,20,10),is_group=True,clipping_id=base.id);d.layers.append(group)
        leaf=d.add(Image.new("RGBA",(20,10),"blue"),"inside");leaf.parent_id=group.id
        self.assertEqual(render(d).getpixel((0,0)),(0,0,255,255));self.assertEqual(render(d).getpixel((15,0))[3],0)

    def test_combined_group_and_clip_cycle_rejected_on_load(self):
        d=Document(10,10);g=Layer("group",None,Transform(0,0,10,10),is_group=True);d.layers.append(g)
        leaf=d.add(Image.new("RGBA",(10,10),"red"),"child");leaf.parent_id=g.id;leaf.clipping_id=g.id
        leaf.clipping_id=None;save_project(d,self.path/"cycle.comp")
        path=self.path/"cycle.comp/manifest.json";data=json.loads(path.read_text(encoding="utf-8"))
        data["layers"][1]["maskSourceID"]=g.id;path.write_text(json.dumps(data),encoding="utf-8")
        with self.assertRaises(ValueError):load_project(self.path/"cycle.comp")

    def test_native_psd_export_preserves_masks_groups_rich_type_and_adjustments(self):
        from psdexport import export_psd
        from psd_tools import PSDImage
        d=Document(220,150);d.add(Image.new("RGBA",(220,150),(30,60,90,255)),"outside")
        group=Layer("group",None,Transform(0,0,220,150),is_group=True);d.layers.append(group)
        leaf=d.add(Image.new("RGBA",(60,40),"red"),"masked");leaf.parent_id=group.id;leaf.transform.x=90
        leaf.mask=Image.new("L",(60,40),160)
        leaf.metadata["effects"]={k:default_effect(k) for k in ("shadow","stroke","innerShadow","outerGlow","innerGlow","colorOverlay")}
        for kind in ("Exposure","Levels","Curves","Invert"):
            d.layers.append(Layer(kind,None,Transform(0,0,220,150),parent_id=group.id,metadata={"adjustment":default_adjustment(kind)}))
        style=dict(content="A😀中\n",fontName="ArialMT",fontSize=24,red=1,green=0,blue=0,alignment="Center",tracking=2,leading=40,boxSize=[180,100])
        set_range(style,"fontRuns",3,4,"MicrosoftYaHei");set_range(style,"colorRuns",3,4,(0,0,1))
        text=d.add(text_image(style),"editable type");text.metadata["text"]=style
        path=self.path/"native.psd";export_psd(d,path)
        native=PSDImage.open(path);types=next(l for l in native if l.kind=="type")
        self.assertEqual(types.text.replace("\r","\n"),style["content"]+"\n")
        self.assertEqual(len(next(l for l in native.descendants() if l.name=="masked").effects),6)
        np.testing.assert_array_equal(np.asarray(native.topil()),np.asarray(render(d)))
        q,notes=load_psd(path);self.assertFalse(notes);self.assertEqual(q.active.metadata["text"],style)
        np.testing.assert_array_equal(np.asarray(render(q)),np.asarray(render(d)))
        # Removing our extra metadata proves the native UTF-16 descriptors carry
        # editable styles even to consumers unaware of Compositor's extension.
        del types.tagged_blocks[b"CpW3"];native._updated=False;native.save(self.path/"standard.psd")
        q,_=load_psd(self.path/"standard.psd")
        self.assertEqual(q.active.metadata["text"]["fontRuns"][0]["location"],3)
        self.assertEqual(q.active.metadata["text"]["colorRuns"][0]["location"],3)

    def test_psd_export_rejects_unrepresentable_adjustment_and_keeps_existing_file(self):
        from psdexport import export_psd
        path=self.path/"existing.psd";path.write_bytes(b"original")
        d=Document(10,10);d.layers.append(Layer("blur",None,Transform(0,0,10,10),metadata={"adjustment":default_adjustment("Gaussian Blur")}))
        with self.assertRaises(ValueError):export_psd(d,path)
        self.assertEqual(path.read_bytes(),b"original")

    def test_native_psd_roundtrip_group_clipping_and_disabled_mask(self):
        from psdexport import export_psd
        d=Document(20,10);g=Layer("base group",None,Transform(0,0,20,10),is_group=True);d.layers.append(g)
        leaf=d.add(Image.new("RGBA",(10,10),"red"),"child");leaf.parent_id=g.id
        top=d.add(Image.new("RGBA",(20,10),"blue"),"clip");top.clipping_id=g.id;top.mask=Image.new("L",(20,10),0);top.mask_enabled=False
        export_psd(d,self.path/"clip.psd");q,_=load_psd(self.path/"clip.psd")
        self.assertFalse(q.active.mask_enabled)
        np.testing.assert_array_equal(np.asarray(render(d)),np.asarray(render(q)))

    def test_real_ai_mask_is_nonuniform_and_respects_subject(self):
        from segmentation import Segmenter
        image=Image.open(FIXTURES/"subject.jpg")
        segmenter=Segmenter(); mask=segmenter.mask(image)
        self.assertEqual(mask.size,image.size); self.assertEqual(mask.getextrema(),(0,255))
        self.assertLess(mask.getpixel((5,5)),30)
        self.assertGreater(mask.getpixel((220,200)),180)


class TypographyTests(unittest.TestCase):
    def style(self): return dict(content="ab😀中文",fontName="ArialMT",fontSize=30,red=1,green=0,blue=0,tracking=0,leading=0,alignment="Left")
    def test_utf16_range_and_typing_inherit_style(self):
        style=self.style(); set_range(style,"colorRuns",2,4,(0,1,0))
        edited=replace_content(style,"ab😀X中文")
        _,colors=unit_styles(edited,"colorRuns")
        self.assertEqual(colors[2:5],[(0,1,0)]*3)
        self.assertEqual(utf16_length(edited["content"]),7)
    def test_tracking_leading_box_and_alignment(self):
        style=self.style(); image=text_image(style)
        tracked=text_image(style|{"tracking":8})
        self.assertGreater(tracked.width,image.width)
        normal=text_image(style|{"content":"A\nB"})
        leading=text_image(style|{"content":"A\nB","leading":80})
        self.assertGreater(leading.height,normal.height)
        left=text_image(style|{"content":"AA","boxSize":[220,100]})
        right=text_image(style|{"content":"AA","boxSize":[220,100],"alignment":"Right"})
        self.assertGreater(right.getbbox()[0],left.getbbox()[0])
    def test_rich_color_and_font_survive_content_edit(self):
        style=self.style(); set_range(style,"fontRuns",4,6,"MicrosoftYaHei"); set_range(style,"colorRuns",4,6,(0,0,1))
        result=replace_content(style,"!ab😀中文")
        self.assertEqual(result["fontRuns"][0]["location"],5)
        self.assertEqual(result["colorRuns"][0]["location"],5)
        self.assertIsNotNone(text_image(result).getbbox())


class GPUParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gpu import create_backend
        cls.backend,cls.label=create_backend()
    @classmethod
    def tearDownClass(cls):
        if cls.backend: cls.backend.close()
    def test_actual_gpu_all_blends_match_cpu_with_one_byte_tolerance(self):
        if not self.backend: self.skipTest(self.label)
        rng=np.random.default_rng(42)
        a,b=[Image.fromarray(rng.integers(0,256,(30,30,4),dtype=np.uint8)) for _ in range(2)]
        for mode in BLEND_MODES:
            cpu=np.asarray(blend(a,b,mode)).astype(int)
            gpu=np.asarray(self.backend.blend(a,b,mode)).astype(int)
            self.assertLessEqual(np.abs(cpu-gpu).max(),1,mode)


class AsyncInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk(); self.root.withdraw(); self.editor=Editor(self.root)
        self.editor.document=Document(200,100); self.editor.document.add(Image.new("RGBA",(200,100),"red"),"base")
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/"verification"); self.path=Path(self.temp.name)
        self.errors=patch("app.messagebox.showerror"); self.error=self.errors.start()
    def tearDown(self):
        self.editor.shutdown(); self.root.destroy(); self.errors.stop(); self.temp.cleanup()
    def wait_job(self):
        limit=time.perf_counter()+10
        while self.editor.jobs.current and time.perf_counter()<limit: self.root.update(); time.sleep(.002)
        self.assertIsNone(self.editor.jobs.current)
    def test_background_job_keeps_tk_responsive_and_cancel_discards_edit(self):
        before=self.editor.document
        def slow(snapshot): time.sleep(.15); snapshot.active.name="changed"; return snapshot
        self.editor.start_job("test",slow,(before.snapshot(),),lambda d:setattr(self.editor,"document",d),True)
        tick=[]; self.root.after(5,lambda:tick.append(1))
        limit=time.perf_counter()+.1
        while not tick and time.perf_counter()<limit: self.root.update(); time.sleep(.002)
        self.assertTrue(tick); self.editor.cancel_job(); self.wait_job()
        self.assertIs(self.editor.document,before)
    def test_save_snapshot_does_not_clear_new_edits(self):
        import app
        original=app.save_project
        def slow(d,path): time.sleep(.1); original(d,path)
        self.editor.dirty=True; self.editor.path=self.path/"saved.comp"
        with patch("app.save_project",side_effect=slow): self.editor.save()
        self.editor.document.active.name="later"; self.editor.changed()
        self.wait_job()
        self.assertTrue(self.editor.dirty)
        self.assertEqual(load_project(self.editor.path).active.name,"base")
    def test_failed_save_does_not_continue_discard(self):
        self.editor.dirty=True; continued=[]; self.editor.path=self.path/"saved.comp"
        with patch("app.save_project",side_effect=OSError("disk error")),patch("app.messagebox.askyesnocancel",return_value=True): self.editor.after_discard(lambda:continued.append(True))
        self.wait_job(); self.assertFalse(continued); self.assertTrue(self.editor.dirty); self.assertTrue(self.error.called)
    def test_saved_discard_continues_only_after_write_finishes(self):
        self.editor.dirty=True; continued=[]; self.editor.path=self.path/"saved.comp"
        with patch("app.messagebox.askyesnocancel",return_value=True): self.editor.after_discard(lambda:continued.append(load_project(self.editor.path).active.name))
        self.assertFalse(continued); self.wait_job(); self.assertEqual(continued,["base"])
    def test_adjustment_created_inside_selected_folder_and_undo(self):
        self.editor.new_group(); group=self.editor.document.active
        self.editor.new_adjustment("Exposure"); self.editor.modal_edit.accept()
        self.assertEqual(self.editor.document.active.parent_id,group.id)
        self.editor.undo(); self.assertEqual(len(self.editor.document.layers),2)
    def test_effect_dialog_cancel_apply_reedit_and_undo(self):
        self.editor.edit_effects(); dialog=self.editor.modal_edit; dialog.values["enabled"].set(True); dialog.preview(); dialog.cancel()
        self.assertNotIn("effects",self.editor.document.active.metadata)
        self.editor.edit_effects(); dialog=self.editor.modal_edit; dialog.values["enabled"].set(True); dialog.accept()
        self.assertIn("stroke",self.editor.document.active.metadata["effects"])
        self.editor.undo(); self.assertNotIn("effects",self.editor.document.active.metadata)
    def test_native_tile_request_and_stale_scene_rejection(self):
        self.root.deiconify(); self.root.update()
        self.editor.fit_mode=False; self.editor.zoom=4; self.editor.offset=(0,0); self.editor.draw()
        limit=time.perf_counter()+10
        while (self.editor.preview_future or not self.editor.tile_signature) and time.perf_counter()<limit: self.root.update(); time.sleep(.002)
        self.assertIsNotNone(self.editor.tile_signature)
        self.assertTrue(self.editor.tile_photos)
        self.editor.scene_revision+=1; self.editor.paint_tiles()
        self.assertFalse(self.editor.tile_photos)


if __name__=="__main__": unittest.main(verbosity=2)
