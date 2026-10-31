"""Smooth rounded shapes for Tkinter, which can't antialias canvas drawings itself."""

import math
import tkinter as tk


def blend(under, over, amount):
    """Mix two #RRGGBB colors. An amount of 1 gives all of `over`."""
    channels = []
    for start in (1, 3, 5):
        low = int(under[start : start + 2], 16)
        high = int(over[start : start + 2], 16)
        channels.append(round(low + (high - low) * amount))
    return "#{:02X}{:02X}{:02X}".format(*channels)


def rounded_image(width, height, radius, fill, outside, border=None):
    """Draw a rounded rectangle. `outside` is the color showing past the corners."""
    edge = border or fill
    image = tk.PhotoImage(width=width, height=height)
    image.put(edge, to=(0, 0, width, height))
    if border:
        image.put(fill, to=(1, 1, width - 1, height - 1))

    for x in range(radius):
        for y in range(radius):
            distance = math.hypot(radius - x - 0.5, radius - y - 0.5)
            outer = min(max(radius - distance + 0.5, 0), 1)
            inner = min(max(radius - 1 - distance + 0.5, 0), 1)
            color = blend(outside, blend(edge, fill, inner), outer)
            for px in (x, width - 1 - x):
                for py in (y, height - 1 - y):
                    image.put(color, (px, py))
    return image
