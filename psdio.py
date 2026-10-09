"""Import actual Photoshop layers, keeping masks, folders and editable type."""
import copy
import json
import hashlib
import colorsys
from pathlib import Path
import math
import numpy as np
from PIL import Image
from engine import Document, Layer, Transform, BLEND_MODES, MAX_ASSET_PIXELS, dimensions
from adjustments import default_adjustment, validate_adjustment
from typography import utf16_length, validate_style

MODE_NAMES = ("NORMAL", "MULTIPLY", "SCREEN", "OVERLAY", "DARKEN", "LIGHTEN", "DIFFERENCE", "EXCLUSION",
              "COLOR_DODGE", "COLOR_BURN", "LINEAR_BURN", "LINEAR_DODGE", "HARD_LIGHT", "SOFT_LIGHT", "SUBTRACT", "DIVIDE")


def mode_name(mode):
    if mode.name == "PASS_THROUGH": return "Normal"
    if mode.name not in MODE_NAMES: raise ValueError(f"PSD 含暂不支持的混合模式 {mode.name}，请先在 Photoshop 转换该模式。")
    return BLEND_MODES[MODE_NAMES.index(mode.name)]


def psd_adjustment(layer):
    kind = layer.kind
    if kind=="invert": return default_adjustment("Invert")
    if kind=="exposure":
        data = default_adjustment("Exposure")
        data["exposureSettings"] = dict(exposure=layer.exposure or 0, offset=layer.exposure_offset or 0, gamma=layer.gamma or 1)
    elif kind=="levels":
        data = default_adjustment("Levels")
        for index,record in enumerate(list(layer.data)[:4]):
            data["levels"]["ranges"][index] = dict(black=record.input_floor, white=record.input_ceiling, gamma=record.gamma/100,
                                                   outputBlack=record.output_floor, outputWhite=record.output_ceiling)
    elif kind=="curves":
        data = default_adjustment("Curves")
        if not layer.data or layer.data.is_map: raise ValueError("PSD 曲线查找表暂不能转换为控制点。")
        for index,points in enumerate(layer.data.data[:4]):
            values = sorted([dict(x=float(p[1]), y=float(p[0])) for p in points], key=lambda p:p["x"])
            if values[0]["x"]>0: values.insert(0,dict(x=0,y=values[0]["y"]))
            if values[-1]["x"]<255: values.append(dict(x=255,y=values[-1]["y"]))
            data["curves"]["channels"][index] = values
    elif kind=="huesaturation":
        from color_adjustments import RANGES
        data=default_adjustment("Hue/Saturation")
        values=layer.colorization if layer.enable_colorization else layer.master
        if values is None:raise ValueError("PSD 色相/饱和度结构缺失。")
        s=dict(range="Master",colorize=bool(layer.enable_colorization),invertRange=False,
            adjustments={"Master":dict(zip(("hue","saturation","lightness"),values))},bands={})
        for name,(band,values) in zip(RANGES[1:],layer.data or []):
            s["bands"][name]=dict(zip(("falloffStart","rangeStart","rangeEnd","falloffEnd"),band))
            s["adjustments"][name]=dict(zip(("hue","saturation","lightness"),values))
        data["hsvSettings"]=s
    elif kind=="colorbalance":
        data=default_adjustment("Color Balance");s=data["colorBalanceSettings"]
        for tone,values in zip(("shadow","mid","highlight"),(layer.shadows,layer.midtones,layer.highlights)):
            if values is None:raise ValueError("PSD 色彩平衡结构缺失。")
            s.update({tone+c:value for c,value in zip(("CyanRed","MagentaGreen","YellowBlue"),values)})
        s["preserveLuminosity"]=bool(layer.luminosity)
    elif kind=="blackandwhite":
        data=default_adjustment("Black & White");s=data["blackWhiteSettings"]
        s.update(zip(("reds","yellows","greens","cyans","blues","magentas"),(layer.red,layer.yellow,layer.green,layer.cyan,layer.blue,layer.magenta)))
        s["tint"]=layer.use_tint
        color=layer.tint_color
        if color and color.classID==b"RGBC":
            rgb=[float(color[key].value)/255 for key in (b"Rd  ",b"Grn ",b"Bl  ")]
            hue,_,sat=colorsys.rgb_to_hls(*rgb);s["tintHue"],s["tintSaturation"]=hue*360,sat*100
        elif layer.use_tint:raise ValueError("PSD 黑白着色颜色空间暂不能转换。")
    elif kind=="gradientmap":
        stops=layer.color_stops or []
        if len(stops)!=2 or [s.location for s in stops]!=[0,4096] or any(s.mode!=0 or s.midpoint!=50 for s in stops):
            raise ValueError("此 PSD 渐变映射使用多色标/非 RGB/非线性渐变；请先栅格化或导入合成预览。")
        if any(s.opacity!=100 for s in layer.transparency_stops or []):raise ValueError("此 PSD 渐变映射包含透明度色标，暂不能转换。")
        data=default_adjustment("Gradient Map");s=data["gradientMapSettings"]
        for key,stop in zip(("shadows","highlights"),stops):s[key]=dict(zip(("red","green","blue"),(v/65535 for v in stop.color[:3])))
        s["reversed"]=bool(layer.reversed)
    else:
        raise ValueError(f"PSD 调整层“{layer.name}”的 {kind} 尚不能转换；请先栅格化该调整层。原 PSD 不会修改。")
    validate_adjustment(data)
    return data


def parse_text(layer, notes):
    setting = layer.typesetting
    if not setting.runs: return None
    first = setting.runs[0].style
    xx,xy,yx,yy,tx,ty = layer.transform
    scale = math.hypot(xx,yx)
    color = first.fill_color or (1,0,0,0)
    text = layer.text.removesuffix("\r").replace("\r","\n")
    style = dict(content=text,fontName=first.font_name or "MicrosoftYaHei",fontSize=max(1,min(2000,first.font_size*scale)),
                 red=color[-3],green=color[-2],blue=color[-1],tracking=max(-100,min(1000,first.tracking*first.font_size*scale/1000)),
                 leading=0 if first.auto_leading else min(5000,first.leading*scale),alignment="Left")
    if setting.paragraphs:
        code = int(setting.paragraphs[0].style.justification)
        style["alignment"] = {0:"Left",1:"Right",2:"Center"}.get(code,"Left")
        if code>2: notes.append(f"{layer.name}：两端对齐转换为左对齐。")
    count = utf16_length(text)
    fonts,colors = [],[]
    # PSD's EngineData lengths count UTF-16 units. psd-tools' high-level runs
    # currently slice Python code points, which shifts styles following emoji.
    from psd_tools.api.typesetting import CharacterStyle
    raw=layer.engine_dict.get("StyleRun",{})
    arrays,lengths=raw.get("RunArray",[]),raw.get("RunLengthArray",[])
    entries=[]; cursor=0
    for index,length in enumerate(lengths):
        length=int(length.value if hasattr(length,"value") else length)
        if length<0: raise ValueError("PSD 文字样式长度无效。")
        data=arrays[index].get("StyleSheet",{}).get("StyleSheetData",{}) if index<len(arrays) else {}
        entries.append((cursor,cursor+length,CharacterStyle(data,setting.fonts,setting.default_style._data)))
        cursor+=length
    if not entries: entries=[(0,count,first)]
    for start,end,s in entries:
        start,end = min(count,start),min(count,end)
        if end<=start: continue
        if s.font_name and s.font_name!=style["fontName"]: fonts.append(dict(location=start,length=end-start,fontName=s.font_name))
        c = (s.fill_color or color)[-3:]
        if tuple(c)!=tuple(color[-3:]): colors.append(dict(location=start,length=end-start,red=c[0],green=c[1],blue=c[2]))
        if abs(s.font_size-first.font_size)>.01: notes.append(f"{layer.name}：局部字号统一为首段字号；导入图像保留原外观。")
    if fonts: style["fontRuns"] = fonts
    if colors: style["colorRuns"] = colors
    validate_style(style)
    return style


def parse_effects(layer, notes):
    from effects import default_effect, validate_effects
    mapping = dict(DropShadow="shadow",InnerShadow="innerShadow",OuterGlow="outerGlow",InnerGlow="innerGlow",ColorOverlay="colorOverlay",Stroke="stroke")
    result = {}
    for effect in layer.effects:
        name = type(effect).__name__
        if name not in mapping:
            notes.append(f"{layer.name}：{name} 效果未转换（原 PSD 保留）。")
            continue
        kind = mapping[name]
        data = default_effect(kind)
        data["enabled"] = bool(effect.enabled and layer.effects.enabled)
        data["opacity"] = effect.opacity/100
        color = getattr(effect,"color",None)
        if color:
            def channel(key): return float(getattr(color.get(key),"value",0))/255
            data.update(red=channel(b"Rd  "),green=channel(b"Grn "),blue=channel(b"Bl  "))
        if kind in ("shadow","innerShadow"):
            data.update(angle=effect.angle,distance=effect.distance,blur=effect.size)
        elif kind in ("outerGlow","innerGlow","stroke"):
            data["size"] = effect.size
            if kind=="stroke": data["inside"] = getattr(effect.position,"name","")=="INSIDE"
        if kind in result: notes.append(f"{layer.name}：同种多重效果仅转换最后一项。")
        result[kind] = data
    validate_effects(result)
    return result


def load_psd(path):
    from psd_tools import PSDImage
    from psd_tools.api.layers import AdjustmentLayer
    psd = PSDImage.open(str(path),max_alloc_bytes=512*1024*1024)
    dimensions(psd.width,psd.height)
    document,notes = Document(psd.width,psd.height),[]
    def walk(container,parent=None):
        base = None
        for source in container:
            if len(document.layers)>=512:raise ValueError("PSD 超过 512 个图层。")
            mode = mode_name(source.blend_mode)
            group = source.is_group()
            image = None if group or isinstance(source,AdjustmentLayer) else source.topil()
            if not group and not isinstance(source,AdjustmentLayer) and image is None:
                if source.width<=0 or source.height<=0:
                    image=Image.new("RGBA",(1,1))
                else:
                    # A shape/fill may have no saved pixel channels. Draw its fill
                    # separately so masks, opacity and effects remain editable.
                    from psd_tools.composite.paint import create_fill
                    color,coverage=create_fill(source,source.bbox)
                    if color is None: raise ValueError(f"PSD 图层 {source.name} 无法读取独立像素，原文件未修改。")
                    pixels=np.uint8(np.clip(color*255,0,255))
                    if pixels.shape[-1]==1: image=Image.fromarray(pixels[...,0]).convert("RGBA")
                    elif pixels.shape[-1]==4: image=Image.fromarray(pixels,"CMYK").convert("RGBA")
                    else: image=Image.fromarray(pixels).convert("RGBA")
                    if coverage is not None: image.putalpha(Image.fromarray(np.uint8(np.squeeze(coverage)*255)))
            image = image.convert("RGBA") if image else None
            if image: dimensions(*image.size)
            t = Transform(source.left,source.top,max(1,source.width),max(1,source.height)) if not group and not isinstance(source,AdjustmentLayer) else Transform(0,0,psd.width,psd.height)
            layer = Layer(source.name,image,t,visible=source.visible,opacity=source.opacity/255,blend=mode,parent_id=parent,is_group=group)
            if group and source.blend_mode.name != "PASS_THROUGH": layer.metadata["windows"] = {"isolated":True}
            if isinstance(source,AdjustmentLayer): layer.metadata["adjustment"] = psd_adjustment(source)
            if source.kind=="type":
                try:
                    style = parse_text(source,notes)
                    if style: layer.metadata["text"] = style
                    extra=source.tagged_blocks.get_data(b"CpW3")
                    if isinstance(extra,bytes) and len(extra)<=1024*1024:
                        try:
                            saved=json.loads(extra)
                            rich=saved.get("text")
                            from psd_tools.constants import Tag
                            native_hash=hashlib.sha256(source.tagged_blocks.get_data(Tag.TYPE_TOOL_OBJECT_SETTING).tobytes()).hexdigest()
                            if saved.get("version")==1 and saved.get("typeHash")==native_hash and isinstance(rich,dict) and rich.get("content")==source.text.removesuffix("\r").replace("\r","\n"):
                                validate_style(rich)
                                layer.metadata["text"]=rich
                        except (ValueError,TypeError,AttributeError): pass
                except (ValueError,AttributeError,KeyError,TypeError) as error:
                    notes.append(f"{source.name}：文字以独立像素导入，排版解析失败：{error}")
            if source.kind in ("smartobject","shape"): notes.append(f"{source.name}：{source.kind} 转为独立像素图层。")
            if source.has_effects(enabled=False): layer.metadata["effects"] = parse_effects(source,notes)
            if source.fill_opacity != 255:
                if layer.metadata.get("effects"): notes.append(f"{source.name}：填充透明度合并为图层透明度。")
                layer.opacity *= source.fill_opacity/255
            if source.has_mask():
                mask = source.mask.topil()
                if mask:
                    # Preserve the outside default as well as the mask's independent bounds.
                    cover = Image.new("L",image.size if image else (psd.width,psd.height),source.mask.background_color)
                    cover.paste(mask.convert("L"),(source.mask.left-int(t.x),source.mask.top-int(t.y)))
                    layer.mask,layer.mask_enabled = cover,not source.mask.disabled
            if source.has_vector_mask() and not source.vector_mask.disabled:
                from psd_tools.composite.vector import draw_vector_mask
                coverage=draw_vector_mask(source)
                if coverage.ndim==3: coverage=coverage[...,0]
                mask = Image.fromarray((coverage*255).clip(0,255).astype("uint8"))
                if image: mask=mask.crop((source.left,source.top,source.right,source.bottom))
                # Rasterize vector coverage to document space; retain it as an editable mask.
                from PIL import ImageChops
                if layer.mask is not None: mask = ImageChops.multiply(layer.mask,mask)
                layer.mask,layer.mask_placement = mask,None
                notes.append(f"{source.name}：矢量蒙版转为可编辑像素蒙版。")
            if source.clipping:
                if base is None: raise ValueError(f"PSD 剪贴层 {source.name} 缺少可用基础层。")
                layer.clipping_id = base.id
            else: base = layer
            document.layers.append(layer)
            if sum(im.width*im.height for l in document.layers for im in (l.image,l.mask) if im is not None)>MAX_ASSET_PIXELS: raise ValueError("PSD 超出图层源像素预算。")
            if group: walk(source,layer.id)
    walk(psd)
    if len(document.layers)>512: raise ValueError("PSD 超过 512 个图层。")
    document.active_id = document.layers[-1].id if document.layers else None
    from engine import validate_structure
    validate_structure(document)
    return document,list(dict.fromkeys(notes))


def import_documents(document, paths, raw_options=None):
    """Prepare a complete candidate off-thread; no partial imports on failure."""
    from engine import load_image, new_id
    from rawio import RAW_EXTENSIONS, develop_raw
    candidate,notes = document.snapshot(),[]
    for path in paths:
        path = Path(path)
        if path.suffix.lower() in (".psd",".psb"):
            imported,conversion_notes = load_psd(path)
            notes += conversion_notes
            if not candidate.layers:
                candidate.width,candidate.height = imported.width,imported.height
            # PSD always remains in its native document coordinates.
            folder = Layer(path.stem,None,Transform(0,0,imported.width,imported.height),is_group=True)
            candidate.layers.append(folder)
            for layer in imported.layers:
                if layer.parent_id is None: layer.parent_id = folder.id
                candidate.layers.append(layer)
            candidate.active_id = imported.active_id
        else:
            image = develop_raw(path,**(raw_options or {})) if path.suffix.lower() in RAW_EXTENSIONS else load_image(path)
            if not candidate.layers: candidate.width,candidate.height = image.size
            layer = candidate.add(image,path.stem)
            fit = min(1,candidate.width/image.width,candidate.height/image.height)
            layer.transform.width,layer.transform.height = image.width*fit,image.height*fit
            layer.transform.x,layer.transform.y = (candidate.width-layer.transform.width)/2,(candidate.height-layer.transform.height)/2
    pixels = sum(im.width*im.height for l in candidate.layers for im in (l.image,l.mask) if im is not None)
    if pixels>MAX_ASSET_PIXELS or len(candidate.layers)>512: raise ValueError("导入结果超过工程图层或像素预算。")
    return candidate,notes
