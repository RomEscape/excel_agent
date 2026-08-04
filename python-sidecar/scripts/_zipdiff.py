import os
import sys
import zipfile
from pathlib import Path

out = open(Path(os.environ["TEMP"]) / "_zipdiff.txt", "w", encoding="utf-8")
a, b = Path(sys.argv[1]), Path(sys.argv[2])
za, zb = zipfile.ZipFile(a), zipfile.ZipFile(b)
out.write(f"{a.name} 손상검사={za.testzip()} 파트={len(za.namelist())}\n")
out.write(f"{b.name} 손상검사={zb.testzip()} 파트={len(zb.namelist())}\n")
na, nb = set(za.namelist()), set(zb.namelist())
out.write(f"원본에만 있음: {sorted(na - nb)}\n")
out.write(f"편집본에만 있음: {sorted(nb - na)}\n")
for name in sorted(nb & na):
    if name.endswith((".rels", "workbook.xml", "[Content_Types].xml")):
        sa, sb = len(za.read(name)), len(zb.read(name))
        if sa != sb:
            out.write(f"크기 다름 {name}: {sa} -> {sb}\n")
out.close()
