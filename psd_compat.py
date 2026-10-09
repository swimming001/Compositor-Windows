"""Explicit PSD compatibility export: bake only prefixes containing unsupported adjustments."""
from engine import Document,Layer,Transform,render,validate_structure,MAX_ASSET_PIXELS

NATIVE=("Exposure","Levels","Curves","Invert","Hue/Saturation","Gradient Map","Black & White","Color Balance")


def unsupported(layer):
    data=layer.metadata.get("adjustment")
    if not data:return False
    return (data["kind"] not in NATIVE or (data["kind"]=="Curves" and any(len(points)>19 for points in data["curves"]["channels"]))
            or (data["kind"]=="Hue/Saturation" and (data.get("hsvSettings") or {}).get("invertRange",False)))


def flatten(document,reason):
    from tiles import render_tiled
    result=Document(document.width,document.height,resolution=document.resolution)
    result.add(render_tiled(document),"兼容导出 · 完整合成")
    return result,[reason+"：PSD 输出为单层合成。原 .comp 工程与当前编辑内容保持可编辑。"]


def prepare_compatible(document):
    validate_structure(document)
    has_unsupported=any(unsupported(layer) for layer in document.layers)
    # Arbitrary clipping references may point to an alpha which would disappear
    # when its prefix is merged. Preserve the visible result rather than rewiring it.
    children={}
    for layer in document.layers:children.setdefault(layer.parent_id,[]).append(layer)
    for siblings in children.values():
        base=None
        for layer in siblings:
            if layer.clipping_id and (layer.clipping_id!=base or has_unsupported):
                return flatten(document,"工程含需合并的调整及剪贴链接，或 PSD 无法表达的跨层剪贴")
            if not layer.clipping_id:base=layer.id
    result=document.snapshot();notes=[]
    def process(parent):
        siblings=[layer for layer in result.layers if layer.parent_id==parent]
        for group in siblings:
            if group.is_group:process(group.id)
        siblings=[layer for layer in result.layers if layer.parent_id==parent]
        indices=[i for i,layer in enumerate(siblings) if unsupported(layer)]
        if not indices:return
        end=max(indices);prefix=siblings[:end+1];ids={layer.id for layer in prefix}
        for _ in range(65):
            more={layer.id for layer in result.layers if layer.parent_id in ids}
            if more<=ids:break
            ids|=more
        subset=result.snapshot();subset.layers=[layer for layer in subset.layers if layer.id in ids]
        for layer in subset.layers:
            if layer.parent_id==parent:layer.parent_id=None
        pixels=render(subset)
        merged=Layer("兼容合成 · "+prefix[-1].name,pixels,Transform(0,0,result.width,result.height),parent_id=parent)
        insertion=min(i for i,layer in enumerate(result.layers) if layer.id in ids)
        result.layers=[layer for layer in result.layers if layer.id not in ids]
        result.layers.insert(insertion,merged)
        if result.active_id in ids:result.active_id=merged.id
        notes.append(f"{prefix[-1].name} 及本组其下方 {len(ids)-1} 个图层合并为像素层；上方图层仍保持独立。")
    process(None)
    if sum(im.width*im.height for layer in result.layers for im in (layer.image,layer.mask) if im is not None)>MAX_ASSET_PIXELS:
        return flatten(document,"分层兼容导出超过像素预算")
    validate_structure(result)
    return result,notes
