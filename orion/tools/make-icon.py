"""make_icon.py — rasterize Orion's delta mark into a Marketplace-ready PNG.

The activity-bar icon (resources/orion-icon.svg) is a white triangle ring on a
transparent background — invisible on the Marketplace's light pages. This
script renders the SAME geometry (outer triangle minus inner triangle from
the SVG path) centered on the dark gallery-banner colour, with 4x
supersampling for clean edges. Pure stdlib: no image dependencies.

Usage: python3 make-icon.py [size] [out.png]
"""

import struct
import sys
import zlib

SIZE = int(sys.argv[1]) if len(sys.argv) > 1 else 256
OUT = sys.argv[2] if len(sys.argv) > 2 else "orion-icon.png"
BG = (13, 17, 23)      # #0d1117, same as galleryBanner
FG = (235, 240, 245)   # near-white, readable on dark and light pages
SS = 4                 # supersampling factor

# SVG viewBox is 24x24; outer triangle (12,2)-(2,22)-(22,22),
# inner hole (12,5.5)-(18.5,20)-(5.5,20). Map with a margin so the mark
# breathes inside the square.
MARGIN = 0.16


def _map(x: float, y: float):
    s = 1.0 - 2 * MARGIN
    return (MARGIN + x / 24.0 * s, MARGIN + y / 24.0 * s)


OUTER = [_map(12, 2), _map(2, 22), _map(22, 22)]
INNER = [_map(12, 5.5), _map(18.5, 20), _map(5.5, 20)]


def inside_tri(px, py, tri):
    (x1, y1), (x2, y2), (x3, y3) = tri
    d1 = (px - x2) * (y1 - y2) - (x1 - x2) * (py - y2)
    d2 = (px - x3) * (y2 - y3) - (x2 - x3) * (py - y3)
    d3 = (px - x1) * (y3 - y1) - (x3 - x1) * (py - y1)
    neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (neg and pos)


def coverage(px, py):
    hits = 0
    for i in range(SS):
        for j in range(SS):
            sx = px + (i + 0.5) / SS / SIZE
            sy = py + (j + 0.5) / SS / SIZE
            if inside_tri(sx, sy, OUTER) and not inside_tri(sx, sy, INNER):
                hits += 1
    return hits / (SS * SS)


rows = []
for y in range(SIZE):
    row = bytearray()
    py = (y + 0.5) / SIZE
    for x in range(SIZE):
        a = coverage((x + 0.5) / SIZE, py)
        r = round(BG[0] + (FG[0] - BG[0]) * a)
        g = round(BG[1] + (FG[1] - BG[1]) * a)
        b = round(BG[2] + (FG[2] - BG[2]) * a)
        row += bytes((r, g, b, 255))
    rows.append(bytes(row))


def chunk(tag, data):
    c = struct.pack(">I", len(data)) + tag + data
    return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


raw = b"".join(b"\x00" + r for r in rows)
png = (
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
    + chunk(b"IDAT", zlib.compress(raw, 9))
    + chunk(b"IEND", b"")
)
Path_out = OUT
with open(Path_out, "wb") as fh:
    fh.write(png)
print(f"wrote {Path_out}: {SIZE}x{SIZE}, {len(png)} bytes")
