"""Refresh verified release documentation and archive without rebuilding binaries."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT=Path(__file__).resolve().parent


def main():
    release=ROOT/"dist/v0.3/Compositor-Windows"
    if not (release/"Compositor-Windows.exe").is_file(): raise RuntimeError("Missing built EXE")
    for mode in ("source","frozen","frozen-cpu"):
        record=ROOT/f"verification/v0.3-{mode}/verification-result.json"
        data=json.loads(record.read_text(encoding="utf-8"))
        if not data["ok"] or data["tk_errors"] or not data["native_psd_export"]: raise RuntimeError("Incomplete validation: "+mode)
        destination=release/f"verification/v0.3-{mode}"
        destination.mkdir(parents=True,exist_ok=True)
        shutil.copy2(record,destination/record.name)
    for name in ("README-中文.md","0.3-任务验收说明.md","NOTICE.md","LICENSE"):
        shutil.copy2(ROOT/name,release/name)
    shutil.copy2(ROOT/"verification/v0.3-tests.txt",release/"verification/v0.3-tests.txt")
    archive=Path(shutil.make_archive(str(ROOT/"dist/Compositor-Windows-0.3-portable"),"zip",release.parent,release.name))
    with zipfile.ZipFile(archive) as packed:
        if packed.testzip(): raise RuntimeError("Archive CRC check failed")
        for relative in ("Compositor-Windows.exe","README-中文.md","_internal/models/u2net.onnx","_internal/models/u2netp.onnx","verification/v0.3-frozen/verification-result.json"):
            if "Compositor-Windows/"+relative not in packed.namelist(): raise RuntimeError("Missing packaged file: "+relative)
    def digest(path):
        value=hashlib.sha256()
        with path.open("rb") as stream:
            for part in iter(lambda:stream.read(1024*1024),b""): value.update(part)
        return value.hexdigest()
    checksum=digest(archive)
    (ROOT/"dist/SHA256SUMS-0.3.txt").write_text(checksum+"  "+archive.name+"\n",encoding="ascii")
    result=dict(version="0.3",archive=str(archive),bytes=archive.stat().st_size,sha256=checksum,
                executable_sha256=digest(release/"Compositor-Windows.exe"),regression_tests=79,
                source_validation=True,frozen_gpu_validation=True,frozen_cpu_validation=True,archive_crc=True)
    (ROOT/"verification/v0.3-release.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
