"""Fetch fixed upstream assets for the local portable release and validation."""
from pathlib import Path
import hashlib
import json
import urllib.request
import urllib.parse
import argparse

ROOT = Path(__file__).resolve().parent


def fetch(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        request = urllib.request.Request(urllib.parse.quote(url, safe=":/?=&%"), headers={"User-Agent": "Compositor-Windows-build"})
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
        target.write_bytes(data)
    print(target.name, target.stat().st_size, flush=True)
    return target.read_bytes()


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-only",action="store_true",help="Download only the two offline AI models")
    args=parser.parse_args()
    model = fetch("https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx", ROOT/"models/u2netp.onnx")
    assert hashlib.md5(model).hexdigest() == "8e83ca70e441ab06c318d82300c84806", "Model checksum mismatch"
    fetch("https://raw.githubusercontent.com/xuebinqin/U-2-Net/master/LICENSE", ROOT/"models/U-2-Net-LICENSE.txt")
    fetch("https://raw.githubusercontent.com/danielgatis/rembg/main/LICENSE.txt", ROOT/"models/rembg-LICENSE.txt")
    (ROOT/"models/SHA256SUMS.txt").write_text(hashlib.sha256(model).hexdigest()+"  u2netp.onnx\n", encoding="utf-8")
    full = fetch("https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx", ROOT/"models/u2net.onnx")
    assert hashlib.md5(full).hexdigest()=="60024c5c889badc19c04ad937298a77b", "Full model checksum mismatch"
    with (ROOT/"models/SHA256SUMS.txt").open("a",encoding="utf-8") as sums: sums.write(hashlib.sha256(full).hexdigest()+"  u2net.onnx\n")
    if args.models_only:raise SystemExit(0)
    request = urllib.request.Request("https://api.github.com/repos/letmaik/rawpy/contents/test", headers={"User-Agent": "Compositor-Windows-build"})
    with urllib.request.urlopen(request, timeout=60) as response:
        entries = json.load(response)
    samples = [e for e in entries if e["name"].lower().endswith((".nef", ".cr2", ".kdc", ".dng"))]
    sample = min(samples, key=lambda e: e["size"])
    fetch(sample["download_url"], ROOT/"verification/v0.3-fixtures"/sample["name"])
    fetch("https://raw.githubusercontent.com/danielgatis/rembg/main/examples/girl-1.jpg", ROOT/"verification/v0.3-fixtures/subject.jpg")
    request=urllib.request.Request("https://api.github.com/repos/psd-tools/psd-tools/contents/tests/psd_files",headers={"User-Agent":"Compositor-Windows-build"})
    with urllib.request.urlopen(request,timeout=60) as response: psds=json.load(response)
    (ROOT/"verification/v0.3-fixtures/psd-catalog.json").write_text(json.dumps([(e["name"],e["size"]) for e in psds],indent=2),encoding="utf-8")
    for name in ("text.psd","layer_mask_data.psd","passthrough_clipping_mask_adjustment.psd","layer_effects.psd","vector-mask.psd","group.psd"):
        entry=next((e for e in psds if e["name"]==name),None)
        if entry: fetch(entry["download_url"],ROOT/"verification/v0.3-fixtures"/name)
    (ROOT/"verification/v0.3-fixtures/SOURCES.json").write_text(json.dumps({"raw": sample["download_url"], "ai": "https://github.com/danielgatis/rembg/tree/main/examples"}, indent=2), encoding="utf-8")
    from validation_fixture import layered_fixture
    layered_fixture(ROOT/"verification/v0.3-fixtures/editable-layers.psd")
