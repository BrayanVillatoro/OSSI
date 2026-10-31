"""Regenerates src/ossi/assets/ossi.ico, the green ring. Run it after changing the design."""

import math
import struct
from pathlib import Path

ICON = Path(__file__).resolve().parents[1] / "src" / "ossi" / "assets" / "ossi.ico"
SIZES = (16, 24, 32, 48, 64, 128)
COLOR = (0x4C, 0xC9, 0xA4)


def ring_alpha(x, y, size):
    center = size / 2
    distance = math.hypot(x + 0.5 - center, y + 0.5 - center)
    outer = min(max(size * 0.48 - distance + 0.5, 0), 1)
    inner = min(max(distance - size * 0.28 + 0.5, 0), 1)
    return round(255 * min(outer, inner))


def bitmap(size):
    """One icon image: a 32-bit bitmap, bottom row first, then an empty 1-bit mask."""
    red, green, blue = COLOR
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = bytearray()
    for y in reversed(range(size)):
        for x in range(size):
            pixels += bytes((blue, green, red, ring_alpha(x, y, size)))
    mask_row = ((size + 31) // 32) * 4
    return header + bytes(pixels) + bytes(mask_row * size)


def main():
    images = [bitmap(size) for size in SIZES]
    offset = 6 + 16 * len(images)
    data = struct.pack("<HHH", 0, 1, len(images))
    for size, image in zip(SIZES, images):
        data += struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(image), offset)
        offset += len(image)
    ICON.parent.mkdir(parents=True, exist_ok=True)
    ICON.write_bytes(data + b"".join(images))
    print(f"Wrote {ICON} ({ICON.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
