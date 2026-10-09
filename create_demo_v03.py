"""An editable v0.3 example with rich type, six styles and folder adjustments."""
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from engine import Document, Layer, Transform, save_project, export_image
from typography import text_image, set_range, utf16_length
from effects import default_effect
from adjustments import default_adjustment

ROOT=Path(__file__).resolve().parent


def create():
    d=Document(1280,800)
    y,x=np.mgrid[0:800,0:1280]
    rgb=np.zeros((800,1280,4),dtype=np.uint8)
    rgb[...,0]=20+np.uint8(x/1280*20); rgb[...,1]=30+np.uint8(y/800*25); rgb[...,2]=60+np.uint8(x/1280*30); rgb[...,3]=255
    d.add(Image.fromarray(rgb),"深蓝渐变背景")
    group=Layer("卡片与组内调整",None,Transform(0,0,1280,800),is_group=True)
    d.layers.append(group)
    card=Image.new("RGBA",(880,500)); ImageDraw.Draw(card).rounded_rectangle((12,12,868,488),radius=35,fill=(48,61,93,255))
    layer=d.add(card,"阴影与描边卡片"); layer.parent_id=group.id; layer.transform.x,layer.transform.y=200,150
    layer.metadata["effects"]={"shadow":default_effect("shadow")|dict(distance=28,blur=20,opacity=.7),"stroke":default_effect("stroke")|dict(size=2,red=.3,green=.6,blue=1,opacity=.75)}
    orb=Image.new("RGBA",(220,220)); ImageDraw.Draw(orb).ellipse((12,12,208,208),fill=(70,170,240,255))
    layer=d.add(orb,"内外发光示例"); layer.parent_id=group.id; layer.transform.x,layer.transform.y=830,375
    layer.metadata["effects"]={"outerGlow":default_effect("outerGlow")|dict(size=15,red=.1,green=.7,blue=1),"innerGlow":default_effect("innerGlow")|dict(size=12),"innerShadow":default_effect("innerShadow")|dict(blur=12,distance=15),"colorOverlay":default_effect("colorOverlay")|dict(red=.15,green=.6,blue=.95,opacity=.3)}
    adjustment=default_adjustment("Exposure"); adjustment["exposureSettings"]["exposure"]=.2
    d.layers.append(Layer("组内曝光 +0.2",None,Transform(0,0,1280,800),parent_id=group.id,metadata={"adjustment":adjustment}))
    style=dict(content="MAKE IT YOURS\n随心创作",fontName="ArialMT",fontSize=64,red=1,green=1,blue=1,alignment="Left",tracking=1,leading=88,boxSize=[790,210])
    start=utf16_length("MAKE IT YOURS\n")
    set_range(style,"fontRuns",start,start+4,"MicrosoftYaHei")
    set_range(style,"colorRuns",start,start+4,(.35,.75,1))
    layer=d.add(text_image(style),"丰富文字排版"); layer.metadata["text"]=style; layer.transform.x,layer.transform.y=255,190
    style=dict(content="Windows 0.3\nGPU · RAW · AI · PSD layers",fontName="SegoeUI",fontSize=27,red=.7,green=.8,blue=.95,alignment="Left",tracking=.5,leading=44,boxSize=[610,140])
    layer=d.add(text_image(style),"副标题"); layer.metadata["text"]=style; layer.transform.x,layer.transform.y=260,440
    d.active_id=d.layers[-2].id
    path=ROOT/"examples/创作工作台0.3.comp"
    save_project(d,path); export_image(d,path.with_suffix(".png"))
    print(path)
    return d


if __name__=="__main__": create()
