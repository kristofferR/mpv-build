#!/usr/bin/env python3
"""Inspect a completed cloud build's packaged cores without compiling or media."""
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import zipfile


def output(*args):
    return subprocess.check_output([str(arg) for arg in args], text=True)


expected = {
    ("ios", "arm64"): 2,
    ("isimulator", "arm64"): 7,
    ("isimulator", "x86_64"): 7,
    ("tvos", "arm64"): 3,
    ("tvsimulator", "arm64"): 8,
}
seen = set()
packages = sorted(Path(sys.argv[1]).rglob("libmpv-all-*.zip"))
if len(packages) != 6:
    raise SystemExit(f"expected six libmpv packages, found {len(packages)}")
with tempfile.TemporaryDirectory() as temporary:
    work = Path(temporary)
    for package in packages:
        with zipfile.ZipFile(package) as archive:
            for member in archive.namelist():
                match = re.search(r"(?:^|/)([^/]+)/thin/([^/]+)/lib/[Ll]ibmpv\.a$", member)
                if not match:
                    continue
                platform, arch = match.groups()
                key = platform, arch
                if key in seen:
                    raise SystemExit(f"duplicate installed slice: {key}")
                seen.add(key)
                library = work / "libmpv.a"
                library.write_bytes(archive.read(member))
                names = output("xcrun", "nm", "-gUj", library).splitlines()
                if key not in expected:
                    if any(name.startswith(("_sab_", "_mp_spatial_")) for name in names):
                        raise SystemExit(f"unexpected spatial symbols in {key}")
                    print(f"{platform}/{arch}: PCM-only package", flush=True)
                    continue
                members = output("xcrun", "ar", "-t", library).splitlines()
                if members.count("SpatialAudioCore.o") != 1:
                    raise SystemExit(f"expected one prelinked core in {key}")
                core = work / "SpatialAudioCore.o"
                core.write_bytes(subprocess.check_output(["xcrun", "ar", "-p", str(library), "SpatialAudioCore.o"]))
                exported = output("xcrun", "nm", "-gUj", core).splitlines()
                if not any(name.startswith("_sab_") for name in exported) or any(
                    not name.startswith("_sab_") and name != "_rust_eh_personality" for name in exported
                ):
                    raise SystemExit(f"unexpected core exports in {key}: {exported}")
                symbols = output("xcrun", "nm", "-m", core)
                if not re.search(r"weak external (?:\[no dead strip\] )?_rust_eh_personality$", symbols, re.MULTILINE):
                    raise SystemExit(f"personality is not weak in {key}")
                data = core.read_bytes()
                offset = 32
                builds = []
                for _ in range(struct.unpack_from("<I", data, 16)[0]):
                    command, size = struct.unpack_from("<II", data, offset)
                    if command == 0x32:  # LC_BUILD_VERSION on the final prelinked object
                        builds.append(struct.unpack_from("<II", data, offset + 8))
                    offset += size
                if builds != [(expected[key], 14 << 16)]:
                    raise SystemExit(f"unexpected platform/minimum OS in {key}: {builds}")
                print(f"{platform}/{arch}: only sab_* and weak rust_eh_personality exported; minimum OS 14.0", flush=True)
                print(output("xcrun", "vtool", "-show-build", core), flush=True)
if len(seen) != 11 or not expected.keys() <= seen:
    raise SystemExit(f"incomplete installed slices: {sorted(seen)}")
print("All eleven packaged slices verified")
