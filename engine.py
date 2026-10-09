"""Windows raster engine and the supported subset of Compositor's v11 format.

Geometry and serialization follow the upstream MIT-licensed project. All raster
operations use straight-alpha RGBA; upstream's C code uses premultiplied RGBA.
"""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps
from adjustments import apply_adjustment, validate_adjustment
from effects import apply_effects, validate_effects

MAX_PIXELS = 64_000_000
MAX_ASSET_PIXELS = 192_000_000
MAX_SIDE = 12000
BLEND_MODES = ("Normal", "Multiply", "Screen", "Overlay", "Darken", "Lighten",
               "Difference", "Exclusion", "Color Dodge", "Color Burn", "Linear Burn",
               "Linear Dodge (Add)", "Hard Light", "Soft Light", "Subtract", "Divide")


def new_id():
    return str(uuid.uuid4()).upper()


def dimensions(width, height):
    if (type(width) is not int or type(height) is not int or
            not 1 <= width <= MAX_SIDE or not 1 <= height <= MAX_SIDE or
            width * height > MAX_PIXELS):
        raise ValueError("画布或图片尺寸超出限制：边长最多 12000，单张最多 6400 万像素。")


def finite(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("工程包含无效或超出范围的数值。")
    return float(value)


@dataclass
class Transform:
    x: float = 0
    y: float = 0
    width: float = 1
    height: float = 1
    rotation: float = 0
    flip_x: bool = False
    flip_y: bool = False
    sampling: str = "High quality"

    def to_dict(self):
        return {"origin": [self.x, self.y], "size": [self.width, self.height],
                "rotation": self.rotation, "flipX": self.flip_x,
                "flipY": self.flip_y, "sampling": self.sampling}

    @classmethod
    def from_dict(cls, data):
        origin, size = data["origin"], data["size"]
        if len(origin) != 2 or len(size) != 2:
            raise ValueError("无效图层变换。")
        sampling = data.get("sampling", "High quality")
        if sampling not in ("Nearest", "Smooth", "High quality"):
            raise ValueError("不支持此采样方式。")
        for flag in ("flipX", "flipY"):
            if type(data.get(flag, False)) is not bool:
                raise ValueError("无效翻转标志。")
        return cls(finite(origin[0], -1e6, 1e6), finite(origin[1], -1e6, 1e6),
                   finite(size[0], 1, 300000), finite(size[1], 1, 300000),
                   finite(data.get("rotation", 0), -1e9, 1e9),
                   data.get("flipX", False), data.get("flipY", False), sampling)

    def local(self, x, y, image_size):
        """Inverse of upstream LayerTransform.point, including flips."""
        angle = math.radians(self.rotation % 360)
        dx, dy = x - self.x - self.width / 2, y - self.y - self.height / 2
        u = (math.cos(angle) * dx + math.sin(angle) * dy) / self.width + .5
        v = (-math.sin(angle) * dx + math.cos(angle) * dy) / self.height + .5
        return ((1-u if self.flip_x else u) * image_size[0],
                (1-v if self.flip_y else v) * image_size[1])


@dataclass
class Layer:
    name: str
    image: Image.Image | None
    transform: Transform
    id: str = field(default_factory=new_id)
    visible: bool = True
    opacity: float = 1
    blend: str = "Normal"
    mask: Image.Image | None = None
    mask_enabled: bool = True
    parent_id: str | None = None
    is_group: bool = False
    clipping_id: str | None = None
    mask_placement: Transform | None = None
    metadata: dict = field(default_factory=dict)

    def raster_changed(self):
        # Editable Mac text/shapes use their PNG until a destructive edit.
        for key in ("text", "shape"):
            self.metadata.pop(key, None)


@dataclass
class Document:
    width: int = 1280
    height: int = 800
    layers: list[Layer] = field(default_factory=list)
    id: str = field(default_factory=new_id)
    active_id: str | None = None
    resolution: float = 72
    guides: list = field(default_factory=list)

    @property
    def active(self):
        return next((layer for layer in self.layers if layer.id == self.active_id), None)

    def add(self, image, name):
        dimensions(*image.size)
        if len(self.layers)>=512:raise ValueError("工程最多支持 512 个图层。")
        if sum(im.width * im.height for l in self.layers for im in (l.image, l.mask) if im is not None) + image.width * image.height > MAX_ASSET_PIXELS:
            raise ValueError("全部图层超过 1.92 亿源像素预算。")
        layer = Layer(name, image.convert("RGBA"), Transform(0, 0, *image.size))
        self.layers.append(layer)
        self.active_id = layer.id
        return layer

    def clone(self):
        return copy.deepcopy(self)

    def snapshot(self):
        """Copy editing metadata and share immutable raster assets for preview/I/O."""
        memo = {id(image): image for layer in self.layers
                for image in (layer.image, layer.mask) if image is not None}
        return copy.deepcopy(self, memo)

    def byte_size(self):
        return sum((l.image.width*l.image.height*4 if l.image else 0) +
                   (l.mask.width*l.mask.height if l.mask else 0) for l in self.layers)


class History:
    def __init__(self, budget=192 * 1024 * 1024):
        self.undo_stack, self.redo_stack = [], []
        self.budget = budget
        self.share_assets = False

    def push(self, document, share_assets=False):
        self.share_assets = share_assets
        self.undo_stack.append(document.snapshot() if share_assets else document.clone())
        self.redo_stack.clear()
        def retained_bytes():
            assets = {id(im): im for d in self.undo_stack for l in d.layers for im in (l.image, l.mask) if im is not None}
            return sum(im.width*im.height*len(im.getbands()) for im in assets.values())
        while len(self.undo_stack) > 1 and (len(self.undo_stack) > 30 or retained_bytes() > self.budget):
            self.undo_stack.pop(0)

    def undo(self, document):
        if not self.undo_stack:
            return document
        self.redo_stack.append(document.snapshot() if self.share_assets else document.clone())
        return self.undo_stack.pop()

    def redo(self, document):
        if not self.redo_stack:
            return document
        self.undo_stack.append(document.snapshot() if self.share_assets else document.clone())
        return self.redo_stack.pop()


def load_image(path):
    path = Path(path)
    from rawio import RAW_EXTENSIONS, develop_raw
    if path.suffix.lower() in RAW_EXTENSIONS:
        return develop_raw(path)
    if path.suffix.lower() in (".psd", ".psb"):
        from psd_tools import PSDImage
        psd = PSDImage.open(path, max_alloc_bytes=128 * 1024 * 1024)
        dimensions(psd.width, psd.height)
        image = psd.topil()  # Photoshop's saved merged preview, never a partial recomposite.
        if image is None:
            raise ValueError("此 PSD 没有保存合成预览；请在 Photoshop 中开启最大兼容性后保存。")
        return image.convert("RGBA")
    with Image.open(path) as image:
        dimensions(*image.size)
        return ImageOps.exif_transpose(image).convert("RGBA")


def placed(image, transform, canvas_size, scale=1, fast=False, origin=(0, 0)):
    """Map document pixels to source pixels without allocating transformed bounds."""
    angle = math.radians(transform.rotation % 360)
    c, s = math.cos(angle), math.sin(angle)
    cx, cy = transform.x + transform.width/2, transform.y + transform.height/2
    w, h = image.size
    fx, fy = (-1 if transform.flip_x else 1), (-1 if transform.flip_y else 1)
    a, b = fx*w*c/(transform.width*scale), fx*w*s/(transform.width*scale)
    d, e = -fy*h*s/(transform.height*scale), fy*h*c/(transform.height*scale)
    offset_x = w/2 - fx*w*(c*cx+s*cy)/transform.width
    offset_y = h/2 - fy*h*(-s*cx+c*cy)/transform.height
    offset_x += (a*origin[0]+b*origin[1])*scale
    offset_y += (d*origin[0]+e*origin[1])*scale
    # An integer translation requires no resampling. Pillow's RGBA affine
    # interpolation otherwise premultiplies/unpremultiplies even identity maps,
    # gradually changing semitransparent RGB on repeated PSD roundtrips.
    if (abs(a-1)<1e-12 and abs(e-1)<1e-12 and abs(b)<1e-12 and abs(d)<1e-12
            and abs(offset_x-round(offset_x))<1e-12 and abs(offset_y-round(offset_y))<1e-12):
        left,top=round(offset_x),round(offset_y)
        return image.crop((left,top,left+canvas_size[0],top+canvas_size[1]))
    resample = (Image.Resampling.NEAREST if transform.sampling == "Nearest" else
                Image.Resampling.BILINEAR if fast else Image.Resampling.BICUBIC)
    return image.transform(canvas_size, Image.Transform.AFFINE,
                           (a, b, offset_x, d, e, offset_y), resample=resample)


def blend(backdrop, source, mode="Normal"):
    if mode == "Normal":
        return Image.alpha_composite(backdrop, source)
    cb = np.asarray(backdrop, dtype=np.float32)/255
    cs = np.asarray(source, dtype=np.float32)/255
    b, s = cb[..., :3], cs[..., :3]
    ab, a = cb[..., 3:4], cs[..., 3:4]
    if mode == "Multiply": value = b*s
    elif mode == "Screen": value = b+s-b*s
    elif mode == "Overlay": value = np.where(b <= .5, 2*b*s, 1-2*(1-b)*(1-s))
    elif mode == "Hard Light": value = np.where(s <= .5, 2*b*s, 1-2*(1-b)*(1-s))
    elif mode == "Darken": value = np.minimum(b, s)
    elif mode == "Lighten": value = np.maximum(b, s)
    elif mode == "Difference": value = np.abs(b-s)
    elif mode == "Exclusion": value = b+s-2*b*s
    elif mode == "Color Dodge": value = np.where(b == 0, 0, np.where(s >= 1, 1, np.minimum(1, b/np.maximum(1-s, 1e-7))))
    elif mode == "Color Burn": value = np.where(b >= 1, 1, np.where(s == 0, 0, 1-np.minimum(1, (1-b)/np.maximum(s, 1e-7))))
    elif mode == "Linear Burn": value = np.maximum(0, b+s-1)
    elif mode == "Linear Dodge (Add)": value = np.minimum(1, b+s)
    elif mode == "Subtract": value = np.maximum(0, b-s)
    elif mode == "Divide": value = np.minimum(1, b/np.maximum(s, 1e-7))
    elif mode == "Soft Light":
        curve = np.where(b <= .25, ((16*b-12)*b+4)*b, np.sqrt(b))
        value = np.where(s <= .5, b-(1-2*s)*b*(1-b), b+(2*s-1)*(curve-b))
    else: raise ValueError(f"暂不支持混合模式：{mode}")
    alpha = a + ab*(1-a)
    rgb = ((1-a)*ab*b + (1-ab)*a*s + a*ab*value) / np.maximum(alpha, 1e-7)
    return Image.fromarray(np.uint8(np.clip(np.concatenate([rgb, alpha], axis=2)*255+.5, 0, 255)))


def render(document, max_size=None, cache=None, fast=False, region=None, gpu=None):
    dimensions(document.width, document.height)
    scale = min(1, max_size[0]/document.width, max_size[1]/document.height) if max_size else 1
    origin = (region[0], region[1]) if region else (0, 0)
    extent = (region[2]-region[0], region[3]-region[1]) if region else (document.width, document.height)
    size = (max(1, round(extent[0]*scale)), max(1, round(extent[1]*scale)))
    scale = size[0]/extent[0]
    result = Image.new("RGBA", size)
    lookup = {l.id: l for l in document.layers}
    prefix = (document.id, size, fast, region, bool(gpu))
    prefix_references = []
    children = {}
    for layer in document.layers: children.setdefault(layer.parent_id, []).append(layer)
    def needs_isolation(group):
        return (bool(group.clipping_id) or bool(group.metadata.get("effects")) or group.blend != "Normal" or group.metadata.get("windows", {}).get("isolated", False)
                or any(child.metadata.get("adjustment") or (child.is_group and needs_isolation(child)) for child in children.get(group.id, [])))
    isolated = {layer.id for layer in document.layers if layer.is_group and needs_isolation(layer)}
    if cache:
        cache.prune([im for layer in document.layers for im in (layer.image, layer.mask) if im is not None])

    def ancestors(layer):
        chain = []
        while layer.parent_id:
            layer = lookup[layer.parent_id]
            chain.append(layer)
        return chain

    def mask_on_canvas(layer):
        image = cache.source(layer.mask, layer.mask_placement or layer.transform, scale) if cache else layer.mask
        return placed(image, layer.mask_placement or layer.transform, size, scale, fast, origin)

    def surface_key(layer, chain=()):
        if layer.id in chain or len(chain) > 64:
            raise ValueError("剪贴蒙版存在循环或链条过长。")
        groups = tuple((g.id, g.opacity, id(g.mask), g.mask_enabled, g.blend,g.id in isolated,repr(g.transform),
                        repr(g.mask_placement)) for g in ancestors(layer))
        clipping = surface_key(lookup[layer.clipping_id], chain+(layer.id,)) if layer.clipping_id else None
        subtree = tuple((child.visible,child.blend,repr(child.metadata.get("adjustment")),surface_key(child,chain+(layer.id,)))
                        for child in children.get(layer.id,[])) if layer.is_group else ()
        return (layer.id, id(layer.image), repr(layer.transform), layer.opacity,
                id(layer.mask), layer.mask_enabled, repr(layer.mask_placement),
                groups, clipping, size, fast, region, repr(layer.metadata.get("effects")),subtree)

    def surface_references(layer, seen=()):
        refs = [im for current in [layer]+ancestors(layer) for im in (current.image, current.mask) if im is not None]
        if layer.clipping_id and layer.id not in seen:
            refs += surface_references(lookup[layer.clipping_id], seen+(layer.id,))
        if layer.is_group and layer.id not in seen:
            for child in children.get(layer.id,[]): refs += surface_references(child,seen+(layer.id,))
        return refs

    def surface(layer, chain=()):
        if layer.id in chain or len(chain) > 64:
            raise ValueError("剪贴蒙版存在循环或链条过长。")
        key = ("surface", surface_key(layer)) if cache else None
        cached = cache.get(key) if cache else None
        if cached is not None:
            return cached
        source = cache.source(layer.image, layer.transform, scale) if cache and layer.image else layer.image
        if layer.is_group:
            # Alpha of the folder is computed on transparent pixels even when its
            # visible color is pass-through. Visibility of the clipping source itself
            # does not disable its live coverage.
            already_isolated=layer.id in isolated
            isolated.add(layer.id)
            try: image=walk(layer.id,Image.new("RGBA",size),(document.id,layer.id,"clip",region,fast,bool(gpu)),[])[0]
            finally:
                if not already_isolated: isolated.remove(layer.id)
        else:
            image = (placed(source, layer.transform, size, scale, fast, origin) if source else Image.new("RGBA", size))
        alpha = image.getchannel("A")
        opacity = layer.opacity
        if layer.mask is not None and layer.mask_enabled:
            alpha = ImageChops.multiply(alpha, mask_on_canvas(layer))
        if layer.metadata.get("effects"):
            # Masks define the silhouette before effects. Shadows and glow extend
            # past its edge; layer opacity is applied to the complete styled surface.
            image.putalpha(alpha)
            angle = math.radians(layer.transform.rotation)
            effects = copy.deepcopy(layer.metadata["effects"])
            layer_scale = (layer.transform.width/layer.image.width+layer.transform.height/layer.image.height)/2 if layer.image else 1
            for kind, settings in effects.items():
                if not settings: continue
                for key in ("size", "distance", "blur"):
                    if key in settings: settings[key] *= layer_scale
                if "angle" in settings: settings["angle"] = ((settings["angle"]-math.degrees(angle)+180)%360)-180
            image = apply_effects(image, effects, scale, validate=False)
            alpha = image.getchannel("A")
        for group in ancestors(layer):
            if group.id in isolated: break
            opacity *= group.opacity
            if group.mask is not None and group.mask_enabled:
                alpha = ImageChops.multiply(alpha, mask_on_canvas(group))
        if layer.clipping_id:
            alpha = ImageChops.multiply(alpha, surface(lookup[layer.clipping_id], chain+(layer.id,)).getchannel("A"))
        if opacity != 1:
            alpha = alpha.point([round(n*opacity) for n in range(256)])
        image.putalpha(alpha)
        if cache:
            cache.put(key, image, surface_references(layer))
        return image

    def walk(parent, result, prefix, prefix_references):
      for layer in children.get(parent, []):
        if layer.visible:
            if layer.is_group and layer.id not in isolated:
                result, prefix, prefix_references = walk(layer.id, result, prefix, prefix_references)
                continue
            prefix_references += surface_references(layer)
            if layer.is_group:
                refs = [im for leaf in document.layers if layer in ancestors(leaf) for im in (leaf.image, leaf.mask) if im is not None]
                prefix_references += refs
                sub, subkey, _ = walk(layer.id, Image.new("RGBA", size), (document.id, layer.id, size, fast, region, bool(gpu)), [])
                alpha = sub.getchannel("A")
                opacity = layer.opacity
                if layer.mask is not None and layer.mask_enabled: alpha = ImageChops.multiply(alpha, mask_on_canvas(layer))
                if layer.metadata.get("effects"):
                    sub=sub.copy();sub.putalpha(alpha)
                    sub=apply_effects(sub,layer.metadata["effects"],scale)
                    alpha=sub.getchannel("A")
                if layer.clipping_id: alpha = ImageChops.multiply(alpha, surface(lookup[layer.clipping_id]).getchannel("A"))
                for group in ancestors(layer):
                    if group.id in isolated: break
                    opacity *= group.opacity
                    if group.mask is not None and group.mask_enabled: alpha = ImageChops.multiply(alpha, mask_on_canvas(group))
                if opacity != 1: alpha = alpha.point([round(x*opacity) for x in range(256)])
                sub = sub.copy(); sub.putalpha(alpha)
                result = (gpu.blend if gpu else blend)(result, sub, layer.blend)
                prefix = (prefix, surface_key(layer), subkey, layer.blend)
                continue
            adjustment = layer.metadata.get("adjustment")
            prefix = (prefix, surface_key(layer), layer.blend, repr(adjustment))
            key = ("composite", prefix)
            cached = cache.get(key) if cache else None
            if cached is not None:
                result = cached
            else:
                if adjustment:
                    changed = apply_adjustment(result, adjustment, scale, origin)
                    coverage = Image.new("L", size, round(layer.opacity*255))
                    if layer.mask is not None and layer.mask_enabled:
                        coverage = ImageChops.multiply(coverage, mask_on_canvas(layer))
                    if layer.clipping_id:
                        coverage = ImageChops.multiply(coverage, surface(lookup[layer.clipping_id]).getchannel("A"))
                    if layer.blend != "Normal": changed = blend(result, changed, layer.blend)
                    result = Image.composite(changed.convert("RGBa"), result.convert("RGBa"), coverage).convert("RGBA")
                else:
                    result = (gpu.blend if gpu else blend)(result, surface(layer), layer.blend)
                if cache:
                    cache.put(key, result, prefix_references)
      return result, prefix, prefix_references
    return walk(None, result, prefix, prefix_references)[0]


def _uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)).upper() != value:
        raise ValueError("工程 UUID 必须为标准大写格式。")
    return value


def _asset(folder, filename, expected, mode):
    if filename != expected:
        raise ValueError("无效的工程图片路径。")
    path = folder/"images"/filename
    if path.is_symlink() or path.parent.is_symlink() or path.stat().st_size > 128*1024*1024:
        raise ValueError("图片路径或大小不受支持。")
    with Image.open(path) as image:
        if image.format != "PNG":
            raise ValueError("工程图片必须为 PNG。")
        dimensions(*image.size)
        if mode == "L" and image.mode != "L":
            raise ValueError("工程蒙版必须为 8 位灰度 PNG。")
        return image.convert(mode)


def validate_structure(document):
    lookup={layer.id:layer for layer in document.layers}
    if len(lookup)!=len(document.layers) or len(lookup)>512:raise ValueError("图层数量或 UUID 无效。")
    children={}
    for layer in document.layers:
        if layer.parent_id:
            parent=lookup.get(layer.parent_id)
            if parent is None or not parent.is_group:raise ValueError("图层组链接无效。")
        if layer.clipping_id and layer.clipping_id not in lookup:raise ValueError("剪贴链接无效。")
        children.setdefault(layer.parent_id,[]).append(layer.id)
    visiting,done=set(),set()
    def visit(key,depth=0):
        if key in visiting or depth>64:raise ValueError("图层组或剪贴依赖存在循环，或深度超过 64。")
        if key in done:return
        visiting.add(key)
        for child in children.get(key,[]):visit(child,depth+1)
        if lookup[key].clipping_id:visit(lookup[key].clipping_id,depth+1)
        visiting.remove(key);done.add(key)
    for key in lookup:visit(key)


def translate_group(document,group_id,dx,dy):
    members={group_id}
    for _ in range(65):
        new={layer.id for layer in document.layers if layer.parent_id in members}
        if new<=members:break
        members|=new
    for layer in document.layers:
        if layer.id in members:
            layer.transform.x+=dx;layer.transform.y+=dy
            if layer.mask_placement:layer.mask_placement.x+=dx;layer.mask_placement.y+=dy


def load_project(folder):
    folder = Path(folder)
    manifest_path = folder/"manifest.json"
    if folder.is_symlink() or manifest_path.is_symlink() or manifest_path.stat().st_size > 4*1024*1024:
        raise ValueError("工程路径或清单大小不受支持。")
    raw = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if raw.get("format") != "com.compositor.project" or type(raw.get("version")) is not int or not 1 <= raw["version"] <= 11:
        raise ValueError("仅支持 Compositor 工程格式 1–11。")
    allowed = {"format", "version", "colorSpace", "documentID", "width", "height", "resolution", "activeLayerID", "layers", "guides"}
    if set(raw)-allowed or raw.get("colorSpace") != "sRGB":
        raise ValueError("工程含尚未支持的文档字段或色彩空间。")
    dimensions(raw["width"], raw["height"])
    document = Document(raw["width"], raw["height"], id=_uuid(raw["documentID"]),
                        resolution=finite(raw.get("resolution", 72), 1, 9600), guides=raw.get("guides") or [])
    if not isinstance(document.guides, list) or len(document.guides) > 1000:
        raise ValueError("无效参考线。")
    for guide in document.guides:
        if not isinstance(guide, dict) or set(guide) != {"id", "axis", "position"} or guide["axis"] not in ("horizontal", "vertical"):
            raise ValueError("无效参考线。")
        _uuid(guide["id"])
        finite(guide["position"], -1e6, 1e6)
    if not isinstance(raw["layers"], list) or len(raw["layers"]) > 512:
        raise ValueError("本移植版最多支持 512 个图层。")
    pixels = 0
    layer_keys = {"id", "name", "isVisible", "transform", "imageFile", "parentID", "isGroup", "opacity", "blendMode", "maskFile", "maskEnabled", "maskSourceID", "maskPlacement", "maskLinked", "text", "shape", "effects", "adjustment", "windows"}
    for record in raw["layers"]:
        layer_id = _uuid(record["id"])
        if set(record)-layer_keys:
            raise ValueError(f"图层 {record.get('name', layer_id)} 含尚未移植的图层效果或未知字段。请先在 Mac 另存栅格化副本。")
        if record.get("effects") is not None: validate_effects(record["effects"])
        if "windows" in record:
            settings=record["windows"]
            if not isinstance(settings,dict) or set(settings)-{"isolated"} or type(settings.get("isolated",False)) is not bool:
                raise ValueError("Windows 组隔离参数无效。")
        if record.get("adjustment") is not None:
            validate_adjustment(record["adjustment"])
            if record.get("isGroup") or record.get("imageFile") or record.get("text"):
                raise ValueError("调整层不能同时含像素、文字或作为图层组。")
        if not isinstance(record["name"], str) or len(record["name"]) > 10000:
            raise ValueError("无效图层名。")
        for flag in ("isVisible", "isGroup", "maskEnabled", "maskLinked"):
            if flag in record and record[flag] is not None and type(record[flag]) is not bool:
                raise ValueError("无效图层标志。")
        mode = record.get("blendMode") or "Normal"
        if mode not in BLEND_MODES:
            raise ValueError(f"尚未移植的混合模式：{mode}")
        image = _asset(folder, record["imageFile"], f"{layer_id}.png", "RGBA") if record.get("imageFile") else None
        mask = _asset(folder, record["maskFile"], f"{layer_id}.mask.png", "L") if record.get("maskFile") else None
        pixels += sum(im.width*im.height for im in (image, mask) if im is not None)
        if pixels > MAX_ASSET_PIXELS:
            raise ValueError("工程源图像和蒙版超过 1.92 亿像素预算。")
        layer = Layer(record["name"], image, Transform.from_dict(record["transform"]),
                      id=layer_id, visible=record["isVisible"], opacity=finite(record.get("opacity", 1), 0, 1),
                      blend=mode, mask=mask, mask_enabled=record.get("maskEnabled", True),
                      parent_id=record.get("parentID"), is_group=bool(record.get("isGroup")),
                      clipping_id=record.get("maskSourceID"),
                      mask_placement=Transform.from_dict(record["maskPlacement"]) if record.get("maskLinked") is False and record.get("maskPlacement") else None,
                      metadata={k: copy.deepcopy(record[k]) for k in ("text", "shape", "maskLinked", "maskPlacement", "adjustment", "effects", "windows") if k in record})
        if layer.is_group and image is not None:
            raise ValueError("无效图层组。")
        if (layer.mask_placement is not None or "maskLinked" in record or "maskPlacement" in record) and mask is None:
            raise ValueError("蒙版位置信息缺少蒙版图片。")
        document.layers.append(layer)
    lookup = {l.id: l for l in document.layers}
    if len(lookup) != len(document.layers):
        raise ValueError("工程存在重复图层 UUID。")
    for layer in document.layers:
        current, seen = layer, {layer.id}
        while current.parent_id:
            group = lookup.get(current.parent_id)
            if not group or not group.is_group or group.id in seen or len(seen) > 64:
                raise ValueError("图层组结构无效或存在循环。")
            seen.add(group.id)
            current = group
        current, seen = layer, {layer.id}
        while current.clipping_id:
            source = lookup.get(current.clipping_id)
            if not source or source.id in seen or len(seen) > 64:
                raise ValueError("剪贴蒙版结构无效或存在循环。")
            seen.add(source.id)
            current = source
    validate_structure(document)
    document.active_id = raw.get("activeLayerID")
    if document.active_id not in lookup:
        document.active_id = document.layers[-1].id if document.layers else None
    return document


def save_project(document, folder):
    """Write a complete sibling package, then swap with rollback on failure."""
    folder = Path(folder).absolute()
    if folder.suffix.lower() != ".comp" or folder.is_symlink():
        raise ValueError("请保存为 .comp 工程文件夹。")
    if folder.exists() and not (folder/"manifest.json").is_file():
        raise ValueError("目标目录不是 Compositor 工程，不能覆盖。")
    temporary = Path(tempfile.mkdtemp(prefix=f".{folder.stem}-", dir=folder.parent))
    backup = None
    try:
        (temporary/"images").mkdir()
        records = []
        for layer in document.layers:
            record = copy.deepcopy(layer.metadata)
            record.update(id=layer.id, name=layer.name, isVisible=layer.visible,
                          transform=layer.transform.to_dict(), isGroup=layer.is_group,
                          opacity=layer.opacity, blendMode=layer.blend)
            if layer.image is not None:
                record["imageFile"] = f"{layer.id}.png"
                layer.image.save(temporary/"images"/record["imageFile"])
            if layer.mask is not None:
                record["maskFile"] = f"{layer.id}.mask.png"
                record["maskEnabled"] = layer.mask_enabled
                layer.mask.save(temporary/"images"/record["maskFile"])
                if layer.mask_placement:
                    record["maskLinked"] = False
                    record["maskPlacement"] = layer.mask_placement.to_dict()
            if layer.parent_id: record["parentID"] = layer.parent_id
            if layer.clipping_id: record["maskSourceID"] = layer.clipping_id
            records.append(record)
        manifest = {"format": "com.compositor.project", "version": 11, "colorSpace": "sRGB",
                    "documentID": document.id, "width": document.width, "height": document.height,
                    "activeLayerID": document.active_id, "resolution": document.resolution,
                    "layers": records, "guides": document.guides}
        (temporary/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        load_project(temporary)  # Validate the package before replacing the user's document.
        if folder.exists():
            backup = folder.with_name(f".{folder.name}-backup-{uuid.uuid4().hex}")
            os.replace(folder, backup)
        try:
            os.replace(temporary, folder)
        except OSError:
            if backup: os.replace(backup, folder)
            raise
        if backup: shutil.rmtree(backup)
    finally:
        if temporary.exists(): shutil.rmtree(temporary)


def export_image(document, path):
    path = Path(path)
    if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        raise ValueError("导出格式必须为 PNG 或 JPEG。")
    from tiles import render_tiled
    image = render_tiled(document)
    if path.suffix.lower() in (".jpg", ".jpeg"):
        background = Image.new("RGBA", image.size, "white")
        image = Image.alpha_composite(background, image).convert("RGB")
    temporary = path.with_name(f".{path.stem}-{uuid.uuid4().hex}{path.suffix}")
    try:
        image.save(temporary, dpi=(document.resolution, document.resolution), **({"quality": 95} if image.mode == "RGB" else {}))
        os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()


def filter_image(image, kind, value=1):
    alpha = image.getchannel("A")
    rgb = image.convert("RGB")
    if kind == "brightness": rgb = ImageEnhance.Brightness(rgb).enhance(value)
    elif kind == "contrast": rgb = ImageEnhance.Contrast(rgb).enhance(value)
    elif kind == "saturation": rgb = ImageEnhance.Color(rgb).enhance(value)
    elif kind == "blur":
        # Pillow's premultiplied mode avoids dark fringes around transparent pixels.
        return image.convert("RGBa").filter(ImageFilter.GaussianBlur(value)).convert("RGBA")
    elif kind == "invert": rgb = ImageOps.invert(rgb)
    elif kind == "grayscale": rgb = ImageOps.grayscale(rgb).convert("RGB")
    elif kind == "autocontrast": rgb = ImageOps.autocontrast(rgb)
    else: raise ValueError("未知滤镜。")
    result = rgb.convert("RGBA")
    result.putalpha(alpha)
    return result


def brush(layer, start, end, radius, color, erase=False, on_mask=False):
    if not on_mask and (layer.is_group or layer.image is None):
        raise ValueError("请选择像素图层。")
    target = layer.mask if on_mask else layer.image
    if on_mask:
        # Upstream saves a uniform 1x1 mask until its first edit. Expand it before
        # painting so one brush stroke does not inadvertently change the whole mask.
        extent = layer.image.size if layer.image else (round(layer.transform.width), round(layer.transform.height))
        dimensions(*extent)
        target = (Image.new("L", extent, 255) if target is None
                  else target if target.size == extent else target.resize(extent, Image.Resampling.BILINEAR))
    transform = layer.mask_placement if on_mask and layer.mask_placement else layer.transform
    p1, p2 = transform.local(*start, target.size), transform.local(*end, target.size)
    # A round document-space stroke becomes an ellipse in the layer's local space.
    rx = max(.5, radius*target.width/transform.width)
    ry = max(.5, radius*target.height/transform.height)
    bounds = (max(0, math.floor(min(p1[0], p2[0])-rx-1)), max(0, math.floor(min(p1[1], p2[1])-ry-1)),
              min(target.width, math.ceil(max(p1[0], p2[0])+rx+2)), min(target.height, math.ceil(max(p1[1], p2[1])+ry+2)))
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]: return
    # Paint only the dirty rectangle. The final source copy keeps history and worker
    # snapshots immutable without allocating several canvas-sized intermediate images.
    patch_size = (bounds[2]-bounds[0], bounds[3]-bounds[1])
    stroke = Image.new("L", patch_size)
    draw = ImageDraw.Draw(stroke)
    distance = math.hypot(p2[0]-p1[0], p2[1]-p1[1])
    steps = min(10000, max(1, math.ceil(distance/max(.5, min(rx, ry)/2))))
    for i in range(steps+1):
        t = i/steps
        x, y = p1[0]+t*(p2[0]-p1[0])-bounds[0], p1[1]+t*(p2[1]-p1[1])-bounds[1]
        draw.ellipse((x-rx, y-ry, x+rx, y+ry), fill=255)
    if on_mask:
        result = target.copy()
        result.paste(Image.composite(Image.new("L", patch_size, 0 if erase else 255), target.crop(bounds), stroke), bounds[:2])
        layer.mask = result
    elif erase:
        result = target.copy()
        patch = target.crop(bounds)
        patch.putalpha(ImageChops.multiply(patch.getchannel("A"), ImageOps.invert(stroke)))
        result.paste(patch, bounds[:2])
        layer.image = result
        layer.raster_changed()
    else:
        paint = Image.new("RGBA", patch_size, color)
        paint.putalpha(stroke)
        result = target.copy()
        result.alpha_composite(paint, dest=bounds[:2])
        layer.image = result
        layer.raster_changed()


def color_mask(layer, color, tolerance):
    """Color-distance key; deliberately not presented as AI subject segmentation."""
    rgb = np.asarray(layer.image.convert("RGB"), dtype=np.float32)
    delta = np.sqrt(np.mean((rgb-np.array(color, dtype=np.float32))**2, axis=2))
    coverage = np.uint8(np.clip((delta-tolerance)/max(1, tolerance*.25), 0, 1)*255)
    mask = Image.fromarray(coverage)
    layer.mask = ImageChops.multiply(layer.mask.resize(mask.size), mask) if layer.mask else mask
    layer.mask_enabled = True
    layer.mask_placement = None
    layer.metadata.pop("maskPlacement", None)
    layer.metadata.pop("maskLinked", None)


def sample_mask_in_layer(layer):
    """Resample an independent document-space mask into the layer source grid."""
    mask, t, source = layer.mask, layer.transform, layer.image
    placement = layer.mask_placement or t
    angle = math.radians(t.rotation)
    def mapped(x,y):
        dx,dy = (x/source.width-.5)*t.width*( -1 if t.flip_x else 1), (y/source.height-.5)*t.height*(-1 if t.flip_y else 1)
        docx = t.x+t.width/2+dx*math.cos(angle)-dy*math.sin(angle)
        docy = t.y+t.height/2+dx*math.sin(angle)+dy*math.cos(angle)
        return placement.local(docx,docy,mask.size)
    p,a,b = mapped(0,0),mapped(1,0),mapped(0,1)
    return mask.transform(source.size, Image.Transform.AFFINE,(a[0]-p[0],b[0]-p[0],p[0],a[1]-p[1],b[1]-p[1],p[1]),Image.Resampling.BILINEAR)
