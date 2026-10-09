"""Create tested 0.4 archive, checksums and public validation summary."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

ROOT=Path(__file__).resolve().parent


def digest(path):
    result=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):result.update(block)
    return result.hexdigest()


def main():
    release=ROOT/"dist/v0.4/Compositor-Windows"
    if not (release/"Compositor-Windows.exe").is_file():raise RuntimeError("Missing EXE")
    records={}
    for mode in ("source","frozen","frozen-cpu"):
        record=ROOT/f"verification/v0.4-{mode}/verification-result.json"
        data=json.loads(record.read_text(encoding="utf-8"))
        required=("ok","native_psd_export","chinese_psd_names","compatible_psd_export","raw16_tiff","all_adjustment_dialogs","curve_points_preserved")
        if not all(data.get(k) for k in required) or data["tk_errors"] or data["adjustment_types"]!=12 or data["native_psd_adjustment_types"]!=8:raise RuntimeError("Incomplete validation: "+mode)
        if mode=="frozen" and (not data["frozen"] or data["gpu_draws"]<1):raise RuntimeError("GPU EXE not exercised")
        if mode=="frozen-cpu" and (not data["frozen"] or data["gpu_draws"] or data["ai_provider"]!="CPUExecutionProvider"):raise RuntimeError("CPU EXE not exercised")
        destination=release/f"verification/v0.4-{mode}"
        destination.mkdir(parents=True,exist_ok=True);shutil.copy2(record,destination/record.name)
        records[mode]=data
    test_log=(ROOT/"verification/v0.4-tests.txt").read_text(encoding="utf-8-sig")
    match=re.search(r"Ran (\d+) tests",test_log)
    if not match or int(match[1])<104 or not re.search(r"\nOK\s*$",test_log):raise RuntimeError("Regression tests did not pass")
    for name in ("README.md","README-中文.md","0.4-更新与验收.md","NOTICE.md","LICENSE"):
        shutil.copy2(ROOT/name,release/name)
    shutil.copy2(ROOT/"verification/v0.4-tests.txt",release/"verification/v0.4-tests.txt")
    shutil.copytree(ROOT/"docs",release/"docs",dirs_exist_ok=True)
    summary=dict(version="0.4",regression_tests=int(match[1]),validation=records)
    (ROOT/"verification/summary-v0.4.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    shutil.copy2(ROOT/"verification/summary-v0.4.json",release/"verification/summary-v0.4.json")
    archive=Path(shutil.make_archive(str(ROOT/"dist/Compositor-Windows-0.4-portable"),"zip",release.parent,release.name))
    with zipfile.ZipFile(archive) as packed:
        if packed.testzip():raise RuntimeError("ZIP CRC check failed")
        for relative in ("Compositor-Windows.exe","README-中文.md","_internal/models/u2net.onnx","_internal/models/u2netp.onnx","verification/summary-v0.4.json"):
            if "Compositor-Windows/"+relative not in packed.namelist():raise RuntimeError("Missing packaged file: "+relative)
    checksum=digest(archive)
    (ROOT/"dist/SHA256SUMS-0.4.txt").write_text(checksum+"  "+archive.name+"\n",encoding="ascii")
    result=dict(version="0.4",archive=archive.name,bytes=archive.stat().st_size,sha256=checksum,
                executable_sha256=digest(release/"Compositor-Windows.exe"),regression_tests=int(match[1]),archive_crc=True)
    (ROOT/"verification/v0.4-release.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
