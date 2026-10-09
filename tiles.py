"""Native document-pixel tiles, with sampling halos for seam-free filters/effects."""
import math
from PIL import Image
from engine import render
from effects import margin

TILE_SIZE = 384


def sampling_halo(document):
    # Consecutive neighborhood adjustments require the sum of their sample radii.
    halo = 3
    for layer in document.layers:
        adjustment = layer.metadata.get("adjustment") or {}
        if adjustment.get("kind") == "Gaussian Blur": halo += math.ceil(adjustment.get("blurRadius", 10)*4+3)
        if adjustment.get("kind") == "Motion Blur": halo += math.ceil(adjustment.get("motionDistance",10)/2+3)
    effects_halo = 0
    lookup={layer.id:layer for layer in document.layers}
    for layer in document.layers:
        total=0;seen=set()
        while layer is not None and layer.id not in seen:
            seen.add(layer.id)
            scale = max(layer.transform.width/layer.image.width, layer.transform.height/layer.image.height) if layer.image else 1
            total+=math.ceil(margin(layer.metadata.get("effects"))*scale)
            layer=lookup.get(layer.parent_id)
        effects_halo=max(effects_halo,total)
    return halo+effects_halo


def render_tile(document, box, cache=None, gpu=None):
    halo = sampling_halo(document)
    left, top, right, bottom = box
    padded = (max(0, left-halo), max(0, top-halo), min(document.width, right+halo), min(document.height, bottom+halo))
    image = render(document, cache=cache, region=padded, gpu=gpu)
    return image.crop((left-padded[0], top-padded[1], right-padded[0], bottom-padded[1]))


def boxes(document, region=None):
    left, top, right, bottom = region or (0, 0, document.width, document.height)
    for y in range(max(0, top//TILE_SIZE*TILE_SIZE), min(document.height, bottom), TILE_SIZE):
        for x in range(max(0, left//TILE_SIZE*TILE_SIZE), min(document.width, right), TILE_SIZE):
            yield x, y, min(document.width, x+TILE_SIZE), min(document.height, y+TILE_SIZE)


def render_tiled(document):
    output = Image.new("RGBA", (document.width, document.height))
    for box in boxes(document): output.paste(render_tile(document, box), box[:2])
    return output
