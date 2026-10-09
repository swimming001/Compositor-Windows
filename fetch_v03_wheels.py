"""Download verified official wheels into the workspace for an offline install."""
import hashlib
import json
from pathlib import Path
import urllib.request
from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename

ROOT = Path(__file__).resolve().parent/"downloads/wheels-v0.3"
ROOT.mkdir(parents=True,exist_ok=True)
TAGS = set(sys_tags())
packages = ("moderngl","glcontext","rawpy","onnxruntime-directml","scipy","coloredlogs","humanfriendly","flatbuffers","protobuf","sympy","mpmath")
versions = {}
for package in packages:
    suffix = "/1.3.0/json" if package=="mpmath" else "/json"
    with urllib.request.urlopen("https://pypi.org/pypi/"+package+suffix,timeout=45) as response: data = json.load(response)
    wheels = [f for f in data["urls"] if f["filename"].endswith(".whl") and parse_wheel_filename(f["filename"])[3]&TAGS]
    if not wheels: raise RuntimeError("No matching official wheel: "+package)
    wheel = wheels[0]
    destination = ROOT/wheel["filename"]
    if not destination.is_file():
        with urllib.request.urlopen(wheel["url"],timeout=90) as response: destination.write_bytes(response.read())
    assert hashlib.sha256(destination.read_bytes()).hexdigest()==wheel["digests"]["sha256"]
    versions[package] = data["info"]["version"]
    print(package,versions[package],destination.stat().st_size,flush=True)
(ROOT/"versions.json").write_text(json.dumps(versions,indent=2),encoding="utf-8")
