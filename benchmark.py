"""Repeatable navigation/brush measurements, including the retained 0.1 engine."""
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import time
import tkinter as tk
from types import SimpleNamespace

from PIL import Image

import engine
from preview import PreviewCache
from app import Editor


ROOT = Path(__file__).parent


def load_baseline():
    spec = importlib.util.spec_from_file_location("baseline_engine", ROOT/"verification"/"v0.1-baseline"/"engine.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def scene(module):
    document = module.Document(2400, 1600)
    for index in range(6):
        document.add(Image.new("RGBA", (2400, 1600), (40+index*24, 80, 130, 120)), "Layer "+str(index))
    return document


def measure(operation, count=8):
    samples = []
    for index in range(count):
        start = time.perf_counter()
        operation(index)
        samples.append((time.perf_counter()-start)*1000)
    return {"median_ms": statistics.median(samples), "samples_ms": samples}


def main():
    baseline = load_baseline()
    old, current = scene(baseline), scene(engine)
    old_render = baseline.render
    def move_old(index):
        old.active.transform.x = index*7
        old_render(old, (1100, 740))
    cache = PreviewCache()
    engine.render(current, (1100, 740), cache)
    def move_current(index):
        current.active.transform.x = index*7
        engine.render(current, (1100, 740), cache)
    fast_cache = PreviewCache()
    engine.render(current, (1100, 740), fast_cache, True)
    def move_fast(index):
        current.active.transform.x = index*7
        engine.render(current, (1100, 740), fast_cache, True)
    results = {"scene": "6 RGBA layers, each 2400x1600; preview 1100x733; warm caches",
               "v01_top_move": measure(move_old), "v02_top_move_quality": measure(move_current),
               "v02_top_move_interactive": measure(move_fast)}
    old_history, history = baseline.History(), engine.History()
    results["v01_checkpoint"] = measure(lambda i: old_history.push(old), 4)
    results["v02_checkpoint"] = measure(lambda i: history.push(current, share_assets=True), 4)
    results["v01_brush"] = measure(lambda i: baseline.brush(old.active, (400, 300), (420, 310), 12, "white"), 4)
    results["v02_brush"] = measure(lambda i: engine.brush(current.active, (400, 300), (420, 310), 12, "white"), 4)
    root = tk.Tk()
    editor = Editor(root)
    editor.document = current
    editor.refresh()
    root.update()
    editor.draw()
    until = time.perf_counter()+10
    while editor.preview_signature != editor.preview_request and time.perf_counter() < until:
        root.update(); time.sleep(.002)
    if editor.preview_signature != editor.preview_request: raise RuntimeError("Benchmark preview timed out")
    editor.pan_down(SimpleNamespace(x=50, y=50))
    results["v02_pan_ui"] = measure(lambda i: editor.pan_move(SimpleNamespace(x=50+i*3, y=50+i*2)), 20)
    results["v02_zoom_ui"] = measure(lambda i: editor.set_zoom(.3+(i%6)*.025), 12)
    editor.shutdown()
    root.destroy()
    results["note"] = "Render timings are not end-to-end FPS. Pan/zoom figures measure the immediate Tk handler; background refinement is excluded. Hardware-specific medians, not a universal guarantee."
    (ROOT/"verification"/"v0.2-performance.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    for key, value in results.items():
        if isinstance(value, dict): print(f"{key}: {value['median_ms']:.2f} ms")


if __name__ == "__main__": main()
