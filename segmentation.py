"""Offline U2-Net salient subject segmentation; optional Windows DirectML GPU."""
from pathlib import Path
import hashlib
import os
import numpy as np
from PIL import Image, ImageChops


class Segmenter:
    def __init__(self, prefer_gpu=True, model="u2net"):
        self.prefer_gpu = prefer_gpu and os.environ.get("COMPOSITOR_AI_DEVICE","auto").lower()!="cpu"
        if model not in ("u2net","u2netp"): raise ValueError("未知主体分割模型。")
        self.model = model
        self.session = None
        self.provider = "未初始化"

    def initialize(self,force_cpu=False):
        if self.session is not None and not force_cpu: return
        if force_cpu:self.session=None
        import onnxruntime as ort
        path = Path(__file__).resolve().parent/("models/"+self.model+".onnx")
        if not path.is_file(): raise ValueError("缺少本地 AI 模型 "+str(path.name)+"，请使用完整便携包。")
        checksum = "60024c5c889badc19c04ad937298a77b" if self.model=="u2net" else "8e83ca70e441ab06c318d82300c84806"
        digest=hashlib.md5()
        with path.open("rb") as stream:
            for block in iter(lambda:stream.read(1024*1024),b""): digest.update(block)
        if digest.hexdigest() != checksum:
            raise ValueError("AI 模型校验失败，请重新解压便携包。")
        options = ort.SessionOptions()
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.intra_op_num_threads = 4
        providers = ["CPUExecutionProvider"]
        if self.prefer_gpu and not force_cpu and "DmlExecutionProvider" in ort.get_available_providers():
            providers.insert(0, "DmlExecutionProvider")
        try:
            self.session = ort.InferenceSession(str(path), sess_options=options, providers=providers)
        except Exception:
            self.session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
        self.provider = self.session.get_providers()[0]

    def mask(self, image):
        self.initialize()
        pixels = np.asarray(image.convert("RGB").resize((320, 320), Image.Resampling.LANCZOS), dtype=np.float32)
        pixels /= max(float(pixels.max()), 1e-6)
        pixels = (pixels-np.float32([.485, .456, .406]))/np.float32([.229, .224, .225])
        inputs = pixels.transpose(2, 0, 1)[None].copy()
        def predict():return self.session.run(None,{self.session.get_inputs()[0].name:inputs})[0][0,0]
        try:prediction=predict()
        except Exception:
            if self.provider!="DmlExecutionProvider":raise
            self.initialize(force_cpu=True);prediction=predict()
        lo, hi = float(prediction.min()), float(prediction.max())
        prediction = (prediction-lo)/max(hi-lo, 1e-6)
        return Image.fromarray(np.uint8(np.clip(prediction*255, 0, 255))).resize(image.size, Image.Resampling.LANCZOS)


def apply_subject_mask(layer, mask):
    if layer.mask is not None:
        # Independent masks are sampled in document space, then mapped to the source grid.
        if layer.mask_placement is not None:
            # Bake existing placement through an affine mapping in layer coordinates.
            from engine import sample_mask_in_layer
            existing = sample_mask_in_layer(layer)
        else:
            existing = layer.mask.resize(mask.size, Image.Resampling.BILINEAR)
        mask = ImageChops.multiply(existing, mask)
    layer.mask, layer.mask_enabled, layer.mask_placement = mask, True, None
    layer.metadata.pop("maskPlacement", None)
    layer.metadata.pop("maskLinked", None)
