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

MAX_PIXELS = 12_000_000
MAX_ASSET_PIXELS = 24_000_000
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
        raise ValueError("画布或图片尺寸超出限制：边长最多 12000，单张最多 1200 万像素。")


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
        if sum(im.width * im.height for l in self.layers for im in (l.image, l.mask) if im is not None) + image.width * image.height > MAX_ASSET_PIXELS:
            raise ValueError("全部图层超过 2400 万源像素预算。")
        layer = Layer(name, image.convert("RGBA"), Transform(0, 0, *image.size))
        self.layers.append(layer)
        self.active_id = layer.id
        return layer

    def clone(self):
        return copy.deepcopy(self)

    def byte_size(self):
        return sum((l.image.width*l.image.height*4 if l.image else 0) +
                   (l.mask.width*l.mask.height if l.mask else 0) for l in self.layers)


class History:
    def __init__(self, budget=192 * 1024 * 1024):
        self.undo_stack, self.redo_stack = [], []
        self.budget = budget

    def push(self, document):
        self.undo_stack.append(document.clone())
        self.redo_stack.clear()
        while len(self.undo_stack) > 1 and (len(self.undo_stack) > 30 or
                sum(d.byte_size() for d in self.undo_stack) > self.budget):
            self.undo_stack.pop(0)

    def undo(self, document):
        if not self.undo_stack:
            return document
        self.redo_stack.append(document.clone())
        return self.undo_stack.pop()

    def redo(self, document):
        if not self.redo_stack:
            return document
        self.undo_stack.append(document.clone())
        return self.redo_stack.pop()


def load_image(path):
    path = Path(path)
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


def placed(image, transform, canvas_size, scale=1):
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
    resample = Image.Resampling.NEAREST if transform.sampling == "Nearest" else Image.Resampling.BICUBIC
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


def render(document, max_size=None):
    dimensions(document.width, document.height)
    scale = min(1, max_size[0]/document.width, max_size[1]/document.height) if max_size else 1
    size = (max(1, round(document.width*scale)), max(1, round(document.height*scale)))
    scale = size[0]/document.width
    result = Image.new("RGBA", size)
    lookup = {l.id: l for l in document.layers}

    def ancestors(layer):
        chain = []
        while layer.parent_id:
            layer = lookup[layer.parent_id]
            chain.append(layer)
        return chain

    def mask_on_canvas(layer):
        return placed(layer.mask, layer.mask_placement or layer.transform, size, scale)

    def surface(layer, chain=()):
        if layer.id in chain or len(chain) > 64:
            raise ValueError("剪贴蒙版存在循环或链条过长。")
        image = (placed(layer.image, layer.transform, size, scale) if layer.image
                 else Image.new("RGBA", size))
        alpha = image.getchannel("A")
        opacity = layer.opacity
        if layer.mask is not None and layer.mask_enabled:
            alpha = ImageChops.multiply(alpha, mask_on_canvas(layer))
        for group in ancestors(layer):
            opacity *= group.opacity
            if group.mask is not None and group.mask_enabled:
                alpha = ImageChops.multiply(alpha, mask_on_canvas(group))
        if layer.clipping_id:
            alpha = ImageChops.multiply(alpha, surface(lookup[layer.clipping_id], chain+(layer.id,)).getchannel("A"))
        if opacity != 1:
            alpha = alpha.point([round(n*opacity) for n in range(256)])
        image.putalpha(alpha)
        return image

    for layer in document.layers:
        if not layer.is_group and layer.visible and all(g.visible for g in ancestors(layer)):
            result = blend(result, surface(layer), layer.blend)
    return result


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
    layer_keys = {"id", "name", "isVisible", "transform", "imageFile", "parentID", "isGroup", "opacity", "blendMode", "maskFile", "maskEnabled", "maskSourceID", "maskPlacement", "maskLinked", "text", "shape", "effects", "adjustment"}
    for record in raw["layers"]:
        layer_id = _uuid(record["id"])
        if set(record)-layer_keys or record.get("adjustment") is not None or record.get("effects"):
            raise ValueError(f"图层 {record.get('name', layer_id)} 含尚未移植的调整层、图层效果或未知字段。请先在 Mac 另存栅格化副本。")
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
            raise ValueError("工程源图像和蒙版超过 2400 万像素预算。")
        layer = Layer(record["name"], image, Transform.from_dict(record["transform"]),
                      id=layer_id, visible=record["isVisible"], opacity=finite(record.get("opacity", 1), 0, 1),
                      blend=mode, mask=mask, mask_enabled=record.get("maskEnabled", True),
                      parent_id=record.get("parentID"), is_group=bool(record.get("isGroup")),
                      clipping_id=record.get("maskSourceID"),
                      mask_placement=Transform.from_dict(record["maskPlacement"]) if record.get("maskLinked") is False and record.get("maskPlacement") else None,
                      metadata={k: copy.deepcopy(record[k]) for k in ("text", "shape", "maskLinked", "maskPlacement") if k in record})
        if layer.is_group and (image is not None or mode != "Normal" or layer.clipping_id):
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
            if not source or source.is_group or source.id in seen or len(seen) > 64:
                raise ValueError("剪贴蒙版结构无效或存在循环。")
            seen.add(source.id)
            current = source
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
    image = render(document)
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
    if layer.is_group or layer.image is None:
        raise ValueError("请选择像素图层。")
    target = layer.mask if on_mask else layer.image
    if on_mask:
        # Upstream saves a uniform 1x1 mask until its first edit. Expand it before
        # painting so one brush stroke does not inadvertently change the whole mask.
        target = (Image.new("L", layer.image.size, 255) if target is None
                  else target.resize(layer.image.size, Image.Resampling.BILINEAR))
    transform = layer.mask_placement if on_mask and layer.mask_placement else layer.transform
    p1, p2 = transform.local(*start, target.size), transform.local(*end, target.size)
    # A round document-space stroke becomes an ellipse in the layer's local space.
    stroke = Image.new("L", target.size)
    draw = ImageDraw.Draw(stroke)
    rx = max(.5, radius*target.width/transform.width)
    ry = max(.5, radius*target.height/transform.height)
    distance = math.hypot(p2[0]-p1[0], p2[1]-p1[1])
    steps = min(10000, max(1, math.ceil(distance/max(.5, min(rx, ry)/2))))
    for i in range(steps+1):
        t = i/steps
        x, y = p1[0]+t*(p2[0]-p1[0]), p1[1]+t*(p2[1]-p1[1])
        draw.ellipse((x-rx, y-ry, x+rx, y+ry), fill=255)
    if on_mask:
        layer.mask = Image.composite(Image.new("L", target.size, 0 if erase else 255), target, stroke)
    elif erase:
        result = target.copy()
        result.putalpha(ImageChops.multiply(target.getchannel("A"), ImageOps.invert(stroke)))
        layer.image = result
        layer.raster_changed()
    else:
        paint = Image.new("RGBA", target.size, color)
        paint.putalpha(stroke)
        layer.image = Image.alpha_composite(target, paint)
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
