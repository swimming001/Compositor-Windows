"""Atomic native layered PSD writer. Unsupported semantics fail before replacement."""
import copy
import hashlib
import json
import math
import colorsys
import os
from pathlib import Path
import tempfile
import numpy as np

from PIL import Image
from engine import dimensions, placed, render, BLEND_MODES,validate_structure
from psdio import MODE_NAMES
from typography import unit_styles, utf16_length, validate_style
from effects import default_effect, validate_effects
from adjustments import validate_adjustment


def put(layer, key, data):
    from psd_tools.psd.tagged_blocks import TaggedBlock
    layer.tagged_blocks[key] = TaggedBlock(key=key.value if hasattr(key,"value") else key, data=data)


def engine_value(value, root=False):
    from psd_tools.psd import engine_data as ed
    if isinstance(value,dict):
        result=ed.EngineData() if root else ed.Dict()
        for key,item in value.items(): result[key]=engine_value(item)
        return result
    if isinstance(value,list): return ed.List([engine_value(item) for item in value])
    if isinstance(value,bool): return ed.Bool(value)
    if isinstance(value,int): return ed.Integer(value)
    if isinstance(value,float): return ed.Float(value)
    return ed.String(value)


def native_text(layer, source):
    from psd_tools.constants import Tag
    from psd_tools.psd.tagged_blocks import TypeToolObjectSetting
    from psd_tools.psd.descriptor import DescriptorBlock, String, RawData, Enumerated
    style=source.metadata["text"]
    validate_style(style)
    text=style.get("content","").replace("\n","\r")+"\r"
    _,fonts=unit_styles(style,"fontRuns"); _,colors=unit_styles(style,"colorRuns")
    fonts=fonts+[fonts[-1] if fonts else style.get("fontName","MicrosoftYaHei")]
    colors=colors+[colors[-1] if colors else tuple(style.get(c,0) for c in ("red","green","blue"))]
    names=list(dict.fromkeys(fonts))
    runs,lengths=[],[]
    previous=None
    for name,color in zip(fonts,colors):
        if previous==(name,color): lengths[-1]+=1; continue
        previous=name,color; lengths.append(1)
        size=float(style.get("fontSize",72))
        runs.append(dict(StyleSheet=dict(StyleSheetData=dict(Font=names.index(name),FontSize=size,
            AutoLeading=not bool(style.get("leading",0)),Leading=float(style.get("leading",0) or size*1.2),
            HorizontalScale=1.,VerticalScale=1.,Tracking=round(style.get("tracking",0)*1000/size),
            AutoKerning=True,Kerning=0,BaselineShift=0.,FauxBold=False,FauxItalic=False,
            FillColor=dict(Type=1,Values=[1.,*map(float,color)]),FillFlag=True,StrokeFlag=False,Ligatures=True))))
    para=dict(ParagraphSheet=dict(DefaultStyleSheet=0,Properties=dict(Justification={"Left":0,"Right":1,"Center":2}[style.get("alignment","Left")],
        AutoLeading=1.2,FirstLineIndent=0.,StartIndent=0.,EndIndent=0.,SpaceBefore=0.,SpaceAfter=0.,AutoHyphenate=False)),
        Adjustments=dict(Axis=[1.,0.,1.],XY=[0.,0.]))
    box=style.get("boxSize")
    shape=dict(ShapeType=1 if box else 0,Procession=0,Lines=dict(WritingDirection=0,Children=[]),
        Cookie=dict(Photoshop=dict(ShapeType=1 if box else 0,PointBase=[12.,12.],
            Base=dict(ShapeType=1 if box else 0,TransformPoint0=[1.,0.],TransformPoint1=[0.,1.],TransformPoint2=[0.,0.]))))
    if box: shape["Cookie"]["Photoshop"]["BoxBounds"]=[12.,12.,float(box[0])-12,float(box[1])-12]
    resources=dict(FontSet=[dict(Name=name,FontType=0,Script=0,Synthetic=False) for name in names])
    ed=engine_value(dict(EngineDict=dict(Editor=dict(Text=text),
        ParagraphRun=dict(DefaultRunData=para,RunArray=[para for p in text.split("\r")[:-1]],
                          RunLengthArray=[utf16_length(p)+1 for p in text.split("\r")[:-1]],IsJoinable=1),
        StyleRun=dict(DefaultRunData=dict(StyleSheet=dict(StyleSheetData={})),RunArray=runs,RunLengthArray=lengths,IsJoinable=2),
        AntiAlias=3,UseFractionalGlyphWidths=True,Rendered=dict(Version=1,Shapes=dict(WritingDirection=0,Children=[shape]))),
        ResourceDict=resources,DocumentResources=resources),root=True)
    transform=source.transform
    angle=math.radians(transform.rotation)
    sx=transform.width/source.image.width*(-1 if transform.flip_x else 1)
    sy=transform.height/source.image.height*(-1 if transform.flip_y else 1)
    a,b,c,d=sx*math.cos(angle),sx*math.sin(angle),-sy*math.sin(angle),sy*math.cos(angle)
    cx,cy=transform.x+transform.width/2,transform.y+transform.height/2
    tx,ty=cx-a*source.image.width/2-c*source.image.height/2,cy-b*source.image.width/2-d*source.image.height/2
    data=DescriptorBlock(classID=b"TxLr",items={b"Txt ":String(text),b"Ornt":Enumerated(typeID=b"Ornt",enum=b"Hrzn"),b"EngineData":RawData(ed)})
    warp=DescriptorBlock(classID=b"warp",items={b"warpStyle":Enumerated(typeID=b"warpStyle",enum=b"warpNone")})
    typedata=TypeToolObjectSetting(text_version=50,text_data=data,warp=warp,
        transform=(a,b,c,d,tx,ty),left=0,top=0,right=source.image.width,bottom=source.image.height)
    put(layer,Tag.TYPE_TOOL_OBJECT_SETTING,typedata)
    # Our extra style block retains paragraph box, UTF-16 runs and precise tracking;
    # other PSD readers see the standard native type descriptor above.
    put(layer,b"CpW3",json.dumps(dict(version=1,text=style,typeHash=hashlib.sha256(typedata.tobytes()).hexdigest()),ensure_ascii=False).encode("utf-8"))


def native_effects(layer,source):
    from psd_tools.constants import Tag
    from psd_tools.terminology import Key, Klass, Unit
    from psd_tools.psd.descriptor import Descriptor, DescriptorBlock2, UnitFloat, Double, Bool, Enumerated
    effects=source.metadata.get("effects") or {}
    validate_effects(effects)
    mapping=dict(shadow=Klass.DropShadow,innerShadow=Klass.InnerShadow,outerGlow=Klass.OuterGlow,
                 innerGlow=Klass.InnerGlow,colorOverlay=Klass.SolidFill,stroke=Klass.FrameFX)
    top=DescriptorBlock2(classID=b"Lefx",items={b"masterFXSwitch":Bool(True),Key.Scale.value:UnitFloat(value=100.,unit=Unit.Percent)})
    scale=(source.transform.width/source.image.width+source.transform.height/source.image.height)/2 if source.image else 1
    for kind,value in effects.items():
        if not value: continue
        settings=default_effect(kind)|value
        color=Descriptor(classID=b"RGBC",items={key:Double(settings[channel]*255) for key,channel in ((b"Rd  ","red"),(b"Grn ","green"),(b"Bl  ","blue"))})
        descriptor=Descriptor(classID=mapping[kind].value,items={Key.Enabled.value:Bool(settings["enabled"]),
            b"present":Bool(True),b"showInDialog":Bool(True),Key.Mode.value:Enumerated(typeID=b"BlnM",enum=b"Nrml"),
            Key.Opacity.value:UnitFloat(value=settings["opacity"]*100,unit=Unit.Percent),Key.Color.value:color})
        if kind in ("shadow","innerShadow"):
            descriptor[Key.UseGlobalAngle.value]=Bool(False)
            descriptor[Key.LocalLightingAngle.value]=UnitFloat(value=(settings["angle"]-source.transform.rotation+180)%360-180,unit=Unit.Angle)
            descriptor[Key.Distance.value]=UnitFloat(value=settings["distance"]*scale,unit=Unit.Pixels)
            descriptor[Key.Blur.value]=UnitFloat(value=settings["blur"]*scale,unit=Unit.Pixels)
        if kind in ("outerGlow","innerGlow"):
            descriptor[Key.Blur.value]=UnitFloat(value=settings["size"]*scale,unit=Unit.Pixels)
        if kind=="stroke":
            descriptor[Key.SizeKey.value]=UnitFloat(value=settings["size"]*scale,unit=Unit.Pixels)
            descriptor[Key.Style.value]=Enumerated(typeID=b"FStl",enum=b"InsF" if settings["inside"] else b"OutF")
            descriptor[Key.PaintType.value]=Enumerated(typeID=b"FrFl",enum=b"SClr")
        top[mapping[kind].value]=descriptor
    put(layer,Tag.OBJECT_BASED_EFFECTS_LAYER_INFO,top)


def native_adjustment(layer,data):
    from psd_tools.constants import Tag
    from psd_tools.psd import adjustments as ad
    from psd_tools.psd.base import EmptyElement
    validate_adjustment(data)
    kind=data["kind"]
    if kind=="Exposure":
        values=data["exposureSettings"]
        put(layer,Tag.EXPOSURE,ad.Exposure(version=1,exposure=values["exposure"],offset=values["offset"],gamma=values["gamma"]))
    elif kind=="Levels":
        records=[ad.LevelRecord(round(v["black"]),round(v["white"]),round(v["outputBlack"]),round(v["outputWhite"]),round(v["gamma"]*100)) for v in data["levels"]["ranges"]]
        records += [ad.LevelRecord(0,255,0,255,100) for _ in range(29-len(records))]
        put(layer,Tag.LEVELS,ad.Levels(items=records,version=2))
    elif kind=="Curves":
        values=[[(round(p["y"]),round(p["x"])) for p in points] for points in data["curves"]["channels"]]
        if any(len(points)>19 for points in values): raise ValueError("原生 PSD 曲线每通道最多 19 个控制点；请减少控制点或保存 .comp。")
        put(layer,Tag.CURVES,ad.Curves(is_map=False,version=4,count_map=4,data=values))
    elif kind=="Hue/Saturation":
        from color_adjustments import RANGES,BANDS
        s=copy.deepcopy(data.get("hsvSettings") or dict(colorize=data.get("colorize",False),range="Master",adjustments={"Master":{k:data.get(k,0) for k in ("hue","saturation","lightness")}},bands={}))
        for key in ("adjustments","bands"):
            if isinstance(s.get(key),list):s[key]=dict(zip(s[key][::2],s[key][1::2]))
        if s.get("invertRange"):raise ValueError("PSD 色相/饱和度不支持此反选色域参数；请使用兼容导出或保留 .comp。")
        def triple(value):return tuple(round(value.get(k,0)) for k in ("hue","saturation","lightness"))
        records=[]
        for name in RANGES[1:]:
            band=s.get("bands",{}).get(name)
            values=tuple(round(band[k]) for k in ("falloffStart","rangeStart","rangeEnd","falloffEnd")) if band else BANDS[name]
            records.append([values,triple(s.get("adjustments",{}).get(name,{}))])
        put(layer,Tag.HUE_SATURATION,ad.HueSaturation(version=2,enable=int(s.get("colorize",False)),
            colorization=triple(s.get("adjustments",{}).get(s.get("range","Master"),{})),master=triple(s.get("adjustments",{}).get("Master",{})),items=records))
    elif kind=="Color Balance":
        from color_adjustments import defaults
        s=defaults(data,"colorBalanceSettings")
        values=[tuple(round(s[tone+c]) for c in ("CyanRed","MagentaGreen","YellowBlue")) for tone in ("shadow","mid","highlight")]
        put(layer,Tag.COLOR_BALANCE,ad.ColorBalance(*values,luminosity=s["preserveLuminosity"]))
    elif kind=="Black & White":
        from color_adjustments import defaults
        from psd_tools.psd.descriptor import DescriptorBlock,Descriptor,Integer,Double,Bool
        s=defaults(data,"blackWhiteSettings")
        r,g,b=colorsys.hls_to_rgb(s["tintHue"]/360,.5,s["tintSaturation"]/100)
        descriptor=DescriptorBlock(classID=b"blwh",items={key:Integer(round(s[name])) for name,key in zip(("reds","yellows","greens","cyans","blues","magentas"),(b"Rd  ",b"Yllw",b"Grn ",b"Cyn ",b"Bl  ",b"Mgnt"))})
        descriptor[b"useTint"]=Bool(s["tint"])
        descriptor[b"tintColor"]=Descriptor(classID=b"RGBC",items={key:Double(value*255) for key,value in zip((b"Rd  ",b"Grn ",b"Bl  "),(r,g,b))})
        put(layer,Tag.BLACK_AND_WHITE,descriptor)
    elif kind=="Gradient Map":
        from color_adjustments import defaults
        s=defaults(data,"gradientMapSettings")
        stops=[ad.ColorStop(location=position,midpoint=50,mode=0,color=tuple(round(s[key][c]*65535) for c in ("red","green","blue"))+(0,)) for key,position in (("shadows",0),("highlights",4096))]
        put(layer,Tag.GRADIENT_MAP,ad.GradientMap(version=1,name="Compositor gradient",is_reversed=int(s["reversed"]),
            color_stops=stops,transparency_stops=[ad.TransparencyStop(0,50,100),ad.TransparencyStop(4096,50,100)],
            interpolation=4096,minimum_color=[0]*4,maximum_color=[65535]*4))
    elif kind=="Invert": put(layer,Tag.INVERT,EmptyElement())
    else: raise ValueError(f"{kind} 没有原生 PSD 调整层对应项；请使用兼容导出或保留 .comp 工程。PSD 未写入。")


def raster_bounds(source):
    t=source.transform
    angle=math.radians(t.rotation)
    w,h=abs(math.cos(angle))*t.width+abs(math.sin(angle))*t.height,abs(math.sin(angle))*t.width+abs(math.cos(angle))*t.height
    left,top=math.floor(t.x+t.width/2-w/2),math.floor(t.y+t.height/2-h/2)
    right,bottom=math.ceil(t.x+t.width/2+w/2),math.ceil(t.y+t.height/2+h/2)
    dimensions(right-left,bottom-top)
    return left,top,right,bottom


def export_psd(document,path,compatible=False):
    from psd_tools import PSDImage
    from psd_tools.api.layers import Group,PixelLayer
    from psd_tools.constants import BlendMode
    notes=[]
    if compatible:
        from psd_compat import prepare_compatible
        document,notes=prepare_compatible(document)
    path=Path(path)
    if path.suffix.lower()!=".psd": raise ValueError("分层导出扩展名必须为 .psd。")
    dimensions(document.width,document.height)
    validate_structure(document)
    if not document.layers:
        document=document.snapshot();document.add(Image.new("RGBA",(document.width,document.height)),"空白画布")
        notes.append("空文档已写为一个透明像素图层。")
    lookup={layer.id:layer for layer in document.layers}
    children={}
    for source in document.layers: children.setdefault(source.parent_id,[]).append(source)
    # PSD clips to the nearest lower base in the same group. Arbitrary links cannot
    # be converted by silently changing layer order or inventing a different base.
    for siblings in children.values():
        base=None
        for source in siblings:
            if source.clipping_id:
                if source.clipping_id!=base: raise ValueError(f"{source.name} 的剪贴基础不是同组紧邻的下方基础层，无法原样导出 PSD；请调整层序或保存 .comp。")
            else: base=source.id
            if source.metadata.get("adjustment",{}).get("kind")=="Gaussian Blur":
                raise ValueError("高斯模糊没有原生 PSD 调整层对应项；请保留 .comp 工程或导出 PNG。PSD 未写入。")
    psd=PSDImage.new("RGBA",(document.width,document.height))
    def isolates(source):
        return source.blend!="Normal" or bool(source.clipping_id) or bool(source.metadata.get("effects")) or source.metadata.get("windows",{}).get("isolated",False) or any(
            item.metadata.get("adjustment") or (item.is_group and isolates(item)) for item in children.get(source.id,[]))
    def walk(parent,container):
        for source in children.get(parent,[]):
            if source.is_group:
                target=Group.new(container,name=source.name)
                target.blend_mode=getattr(BlendMode,MODE_NAMES[BLEND_MODES.index(source.blend)]) if isolates(source) else BlendMode.PASS_THROUGH
                if source.metadata.get("effects"): native_effects(target,source)
            else:
                adjustment=source.metadata.get("adjustment")
                if adjustment:
                    target=PixelLayer.frompil(Image.new("RGBA",(1,1)),container,name=source.name)
                    native_adjustment(target,adjustment)
                else:
                    left,top,right,bottom=raster_bounds(source)
                    im=placed(source.image,source.transform,(right-left,bottom-top),origin=(left,top))
                    target=PixelLayer.frompil(im,container,name=source.name,left=left,top=top)
                    if source.metadata.get("text"): native_text(target,source)
                    if source.metadata.get("effects"): native_effects(target,source)
                    if source.transform.rotation or source.transform.flip_x or source.transform.flip_y:
                        notes.append("像素图层的缩放/旋转/翻转已写入独立像素；.comp 保留原图与变换参数。文字仍写入原生文字变换。")
                target.blend_mode=getattr(BlendMode,MODE_NAMES[BLEND_MODES.index(source.blend)])
            name=source.name
            if len(name.encode("utf-16-le"))>510:
                name=name.encode("utf-16-le")[:510].decode("utf-16-le",errors="ignore")
                notes.append("PSD 图层名限制为 255 个 UTF-16 单元，超长名称已截短；.comp 保留完整名称。")
            target.name=name  # Write Unicode name separately from the legacy Pascal name.
            target.visible=source.visible; target.opacity=round(source.opacity*255)
            if source.clipping_id:
                # The high-level API blocks folder clipping in Photoshop mode;
                # native PSD fixtures use the same record flag for clipped groups.
                from psd_tools.constants import Clipping
                target._record.clipping=Clipping.NON_BASE
            if source.mask is not None:
                transform=source.mask_placement or source.transform
                # The mask can cover different bounds from the image, including groups.
                class Proxy: pass
                proxy=Proxy(); proxy.transform=transform
                ml,mt,mr,mb=raster_bounds(proxy)
                mask=placed(source.mask,transform,(mr-ml,mb-mt),origin=(ml,mt))
                target.create_mask(mask,left=ml,top=mt)
                target._record.mask_data.flags.mask_disabled=not source.mask_enabled
            if source.is_group: walk(source.id,target)
    walk(None,psd)
    merged=render(document)
    # Negative layer count identifies the fourth merged channel as transparency.
    # Native PSD previews store white-matted RGB, unlike individual layer channels.
    psd._record.layer_and_mask_information.layer_info.layer_count=-abs(psd._record.layer_and_mask_information.layer_info.layer_count)
    psd._merged_alpha=True
    from psd_tools.api.numpy_io import encode_image_data
    pixels=np.asarray(merged,dtype=np.float32)/255
    psd._record.image_data.set_data(encode_image_data(psd,pixels[...,:3],pixels[...,3:4]),psd._record.header)
    psd._updated=False
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(prefix=".compositor-",suffix=".psd",dir=path.parent)
    os.close(fd)
    try:
        psd.save(temporary)
        checked=PSDImage.open(temporary,max_alloc_bytes=512*1024*1024)
        if (checked.width,checked.height)!=(document.width,document.height) or len(list(checked.descendants()))!=len(document.layers):
            raise ValueError("PSD 写入后的图层校验失败，目标文件未替换。")
        from psdio import load_psd
        load_psd(temporary)  # Reject unreadable native adjustment/type/mask structures.
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return list(dict.fromkeys(notes))
