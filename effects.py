"""Non-destructive implementations of all six upstream layer effects."""
import math
import numpy as np
from PIL import Image, ImageChops, ImageFilter, ImageOps

KINDS = {"stroke": "描边", "shadow": "投影", "colorOverlay": "颜色叠加", "innerShadow": "内阴影", "outerGlow": "外发光", "innerGlow": "内发光"}


def default_effect(kind):
    if kind not in KINDS: raise ValueError("未知图层效果。")
    data = dict(enabled=True, red=0, green=0, blue=0, opacity=1)
    if kind == "stroke": data.update(size=4, inside=False)
    if kind in ("shadow", "innerShadow"): data.update(angle=90, distance=20, blur=20, opacity=.5)
    if kind in ("outerGlow", "innerGlow"): data.update(size=20, red=1, green=1, blue=1, opacity=.75)
    return data


def validate_effects(effects):
    if not isinstance(effects, dict) or set(effects)-set(KINDS): raise ValueError("图层含未知效果。")
    for kind, settings in effects.items():
        if settings is None: continue
        allowed = set(default_effect(kind))
        if not isinstance(settings, dict) or set(settings)-allowed: raise ValueError("图层效果含未知参数。")
        for flag in ("enabled", "inside"):
            if settings.get(flag) is not None and type(settings[flag]) is not bool: raise ValueError("图层效果标志无效。")
        bounds = dict(red=(0, 1), green=(0, 1), blue=(0, 1), opacity=(0, 1), size=(0, 500), angle=(-360, 360), distance=(0, 5000), blur=(0, 500))
        for key, value in settings.items():
            if key in bounds:
                lo, hi = bounds[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
                    raise ValueError("图层效果参数超出范围。")


def margin(effects):
    result = 0
    for kind, values in (effects or {}).items():
        if not values or values.get("enabled") is False: continue
        data = default_effect(kind) | values
        if kind == "stroke" and not data["inside"]: result = max(result, data["size"])
        if kind == "shadow": result = max(result, data["distance"]+data["blur"]*3)
        if kind == "outerGlow": result = max(result, data["size"]*3)
    return math.ceil(result)+2 if result else 0


def shift(mask, dx, dy, fill=0):
    return mask.transform(mask.size, Image.Transform.AFFINE, (1, 0, -dx, 0, 1, -dy), Image.Resampling.BILINEAR, fillcolor=fill)


def spread(alpha, radius, inside=False):
    from scipy.ndimage import distance_transform_edt
    shape = np.asarray(alpha) >= 128
    # Padding gives a well-defined transparent border for fully opaque layers.
    padded = np.pad(shape, 1)
    distance = distance_transform_edt(padded if inside else ~padded)[1:-1, 1:-1]
    values = np.clip(radius+.5-distance, 0, 1)*255
    return Image.fromarray(np.uint8(values))


def apply_effects(image, effects, scale=1, validate=True):
    if not effects: return image
    if validate: validate_effects(effects)
    alpha = image.getchannel("A")
    background = Image.new("RGBA", image.size)
    styled = image.copy()
    active = {k: default_effect(k) | v for k, v in effects.items() if v and v.get("enabled") is not False}
    def tint(mask, settings):
        color = tuple(round(settings[k]*255) for k in ("red", "green", "blue"))
        painted = Image.new("RGBA", image.size, color)
        painted.putalpha(mask.point([round(x*settings["opacity"]) for x in range(256)]))
        return painted
    def recolor(mask, settings):
        nonlocal styled
        coverage = mask.point([round(x*settings["opacity"]) for x in range(256)])
        color = Image.new("RGB", image.size, tuple(round(settings[k]*255) for k in ("red", "green", "blue")))
        styled = Image.composite(color, styled.convert("RGB"), coverage).convert("RGBA")
        styled.putalpha(alpha)
    for kind in ("shadow", "outerGlow", "stroke"):
        d = active.get(kind)
        if not d: continue
        if kind == "shadow":
            angle = math.radians(d["angle"])
            coverage = shift(alpha, -math.cos(angle)*d["distance"]*scale, math.sin(angle)*d["distance"]*scale).filter(ImageFilter.GaussianBlur(d["blur"]*scale))
        elif kind == "outerGlow":
            coverage = alpha.filter(ImageFilter.GaussianBlur(d["size"]*scale))
        else:
            if d["inside"]: continue
            coverage = ImageChops.subtract(spread(alpha, d["size"]*scale), alpha)
        background = Image.alpha_composite(background, tint(coverage, d))
    if "colorOverlay" in active: recolor(Image.new("L", image.size, 255), active["colorOverlay"])
    for kind in ("innerGlow", "innerShadow", "stroke"):
        d = active.get(kind)
        if not d: continue
        if kind == "stroke":
            if not d["inside"]: continue
            coverage = ImageChops.multiply(alpha, spread(alpha, d["size"]*scale, inside=True))
        elif kind == "innerGlow":
            coverage = ImageOps.invert(alpha.filter(ImageFilter.GaussianBlur(d["size"]*scale)))
        else:
            angle = math.radians(d["angle"])
            coverage = shift(ImageOps.invert(alpha), -math.cos(angle)*d["distance"]*scale, math.sin(angle)*d["distance"]*scale, 255).filter(ImageFilter.GaussianBlur(d["blur"]*scale))
        recolor(coverage, d)
    return Image.alpha_composite(background, styled)
