"""Bounded caches used exclusively by the single background preview worker."""
from collections import OrderedDict
from PIL import Image, ImageDraw

from engine import render


class PreviewCache:
    def __init__(self, budget=96*1024*1024):
        self.entries = OrderedDict()
        self.bytes = 0
        self.budget = budget
        self.hits = self.misses = 0

    def get(self, key):
        value = self.entries.get(key)
        if value is None:
            self.misses += 1
            return None
        self.entries.move_to_end(key)
        self.hits += 1
        return value[0]

    def prune(self, images):
        current = {id(image) for image in images}
        for key in list(self.entries):
            image, refs = self.entries[key]
            if any(id(ref) not in current for ref in refs):
                self.entries.pop(key)
                self.bytes -= image.width*image.height*len(image.getbands())

    def put(self, key, image, references=()):
        if key in self.entries:
            old, _ = self.entries.pop(key)
            self.bytes -= old.width*old.height*len(old.getbands())
        size = image.width*image.height*len(image.getbands())
        if size > self.budget:
            return
        # References keep the identity keys valid even after the live layer changes.
        self.entries[key] = (image, tuple(references))
        self.bytes += size
        while self.bytes > self.budget:
            old, _ = self.entries.popitem(last=False)[1]
            self.bytes -= old.width*old.height*len(old.getbands())

    def source(self, image, transform, scale):
        if image is None or transform.sampling == "Nearest":
            return image
        ratio = max(transform.width*scale/image.width, transform.height*scale/image.height)
        if ratio >= .65:
            return image
        # A bounded thumbnail pyramid removes repeated sampling of full-size photos.
        divisor = 1
        while ratio*divisor < .65 and min(image.size)/divisor > 2:
            divisor *= 2
        key = ("source", id(image), divisor)
        cached = self.get(key)
        if cached is None:
            cached = image.resize((max(1, round(image.width/divisor)), max(1, round(image.height/divisor))), Image.Resampling.LANCZOS)
            self.put(key, cached, (image,))
        return cached


class PreviewRenderer:
    def __init__(self):
        self.cache = PreviewCache()
        self.checkers = {}
        self.last_raster = None
        self.last_rgb = None
        self.gpu = None
        self.backend_label = "初始化渲染器…"
        self.initialized = False

    def initialize(self):
        if self.initialized: return
        from gpu import create_backend
        self.gpu,self.backend_label = create_backend()
        self.initialized = True

    def raster(self, document, max_size=None, fast=False, region=None):
        self.initialize()
        try:
            return render(document, max_size, self.cache, fast, region, self.gpu)
        except Exception:
            if self.gpu is None: raise
            try:self.gpu.close()
            except Exception:pass
            self.gpu = None
            self.backend_label = "CPU（GPU 渲染失败，已回退）"
            return render(document, max_size, self.cache, fast, region)

    def draw(self, document, max_size, fast=False):
        raster = self.raster(document, max_size, fast)
        if raster is self.last_raster:
            return self.last_rgb
        checker = self.checkers.get(raster.size)
        if checker is None:
            # A small repeated tile avoids allocating two large NumPy coordinate grids.
            tile = Image.new("RGBA", (32, 32), (104, 104, 104, 255))
            pen = ImageDraw.Draw(tile)
            pen.rectangle((16, 0, 31, 15), fill=(80, 80, 80, 255))
            pen.rectangle((0, 16, 15, 31), fill=(80, 80, 80, 255))
            checker = Image.new("RGBA", raster.size)
            for y in range(0, raster.height, 32):
                for x in range(0, raster.width, 32):
                    checker.paste(tile, (x, y))
            self.checkers = {raster.size: checker}
        self.last_raster = raster
        self.last_rgb = Image.alpha_composite(checker, raster).convert("RGB")
        return self.last_rgb

    def draw_tiles(self, document, viewport):
        from tiles import boxes, render_tile
        self.initialize()
        self.cache.prune([im for layer in document.layers for im in (layer.image,layer.mask) if im is not None])
        signature = tuple((l.id,id(l.image),id(l.mask),repr(l.transform),repr(l.mask_placement),l.visible,l.opacity,l.blend,l.parent_id,l.clipping_id,l.mask_enabled,repr(l.metadata)) for l in document.layers)
        references = [im for l in document.layers for im in (l.image,l.mask) if im is not None]
        results = {}
        for box in boxes(document,viewport):
            key = ("native-tile",document.id,document.width,document.height,signature,box,bool(self.gpu))
            tile = self.cache.get(key)
            if tile is None:
                try: tile = render_tile(document,box,self.cache,self.gpu)
                except Exception:
                    if not self.gpu: raise
                    try:self.gpu.close()
                    except Exception:pass
                    self.gpu=None; self.backend_label="CPU（GPU 渲染失败，已回退）"
                    tile = render_tile(document,box,self.cache)
                checker = Image.new("RGBA",tile.size,(104,104,104,255))
                draw = ImageDraw.Draw(checker)
                for y in range(tile.height):
                    for x in range(0,tile.width,16):
                        if ((x+box[0])//16+(y+box[1])//16)%2: draw.line((x,y,min(tile.width-1,x+15),y),fill=(80,80,80,255))
                tile = Image.alpha_composite(checker,tile).convert("RGB")
                self.cache.put(key,tile,references)
            results[box] = tile
        return results

    def close(self):
        if self.gpu: self.gpu.close(); self.gpu=None
