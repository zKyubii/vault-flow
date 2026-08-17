"""Generate the PNG app icons from the same artwork as icon.svg.

Why PNGs at all, when there is already an SVG: **iOS ignores SVG for
`apple-touch-icon`**. Add the app to the home screen with only an SVG declared
and you get a blank tile or a shrunken screenshot — never the icon. Android and
desktop browsers do read the SVG, so it stays; these files exist for iOS and as
a fallback.

Two shapes are produced, and the difference matters:

* `apple-touch-icon` is a **square with no rounded corners**. iOS applies its
  own squircle mask; supplying pre-rounded corners means the system rounds an
  already-rounded image and you get dark wedges at the corners.
* the `maskable` icon keeps its artwork inside the central 80%, because Android
  is free to crop it to a circle.

Run it after changing the artwork:

    python tools/make_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"

# Same palette as style.css. Kept literal rather than parsed out of the CSS:
# a build script that fails because a stylesheet was reformatted is worse than
# two values written twice.
BG = (8, 12, 16, 255)
AQUA = (33, 212, 180)
BLUE = (77, 141, 255)
DOT = (69, 224, 122)

# Artwork is authored in a 512x512 coordinate space, like the SVG, then scaled.
BASE = 512
# Draw large and shrink: Pillow has no antialiasing on shapes, so the only way
# to get clean curves is to supersample and let the resize do the smoothing.
SS = 4


def gradient(size):
    """The SVG's diagonal aqua->blue linear gradient.

    Built tiny and scaled up: computing it per pixel at 2048x2048 in Python
    takes seconds, and a bicubic upscale of a smooth ramp is indistinguishable.
    """
    small = 64
    img = Image.new("RGB", (small, small))
    pixels = img.load()
    for y in range(small):
        for x in range(small):
            t = (x + y) / (2 * (small - 1))
            pixels[x, y] = tuple(
                round(AQUA[i] + (BLUE[i] - AQUA[i]) * t) for i in range(3)
            )
    return img.resize((size, size), Image.BICUBIC)


def draw_artwork(size, rounded=True, inset=0.0):
    """Render the icon at `size` pixels.

    `rounded` draws the rounded-square background (off for iOS, which masks it
    itself). `inset` shrinks the artwork towards the centre, leaving padding
    for maskable icons.
    """
    canvas = size * SS
    scale = canvas / BASE

    img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if rounded:
        draw.rounded_rectangle(
            [0, 0, canvas - 1, canvas - 1], radius=112 * scale, fill=BG
        )
    else:
        draw.rectangle([0, 0, canvas - 1, canvas - 1], fill=BG)

    # Everything below is drawn in 512-space and mapped through `pt`, so the
    # numbers can be compared against icon.svg line by line.
    pad = inset * BASE / 2

    def pt(x, y):
        x = pad + x * (1 - inset)
        y = pad + y * (1 - inset)
        return x * scale, y * scale

    grad = gradient(canvas)

    # thin border, at half opacity like the SVG's stroke-opacity
    if rounded:
        border = Image.new("L", (canvas, canvas), 0)
        bd = ImageDraw.Draw(border)
        x0, y0 = pt(12, 12)
        x1, y1 = pt(500, 500)
        bd.rounded_rectangle(
            [x0, y0, x1, y1], radius=102 * scale, outline=128, width=round(6 * scale)
        )
        img.paste(grad, (0, 0), border)

    # the rising line and its arrow corner
    strokes = Image.new("L", (canvas, canvas), 0)
    sd = ImageDraw.Draw(strokes)
    width = round(32 * scale * (1 - inset))

    line = [pt(112, 336), pt(200, 248), pt(272, 302), pt(400, 172)]
    sd.line(line, fill=255, width=width, joint="curve")

    corner = [pt(334, 172), pt(400, 172), pt(400, 238)]
    sd.line(corner, fill=255, width=width, joint="curve")

    # Pillow draws butt caps only; the SVG uses round ones, so the ends get a
    # disc of their own. Without this the line finishes in a hard chisel.
    for point in (line[0], line[-1], corner[0], corner[-1]):
        x, y = point
        r = width / 2
        sd.ellipse([x - r, y - r, x + r, y + r], fill=255)

    img.paste(grad, (0, 0), strokes)

    # the two green nodes sitting on the line
    for cx, cy in ((200, 248), (272, 302)):
        x, y = pt(cx, cy)
        r = 18 * scale * (1 - inset)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=DOT)

    return img.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    targets = [
        # iOS home screen: square, iOS rounds it itself
        ("apple-touch-icon.png", 180, False, 0.0),
        # manifest, normal purpose
        ("icon-192.png", 192, True, 0.0),
        ("icon-512.png", 512, True, 0.0),
        # manifest, maskable: artwork inside the central 80%
        ("icon-maskable-512.png", 512, False, 0.2),
        ("favicon-32.png", 32, True, 0.0),
    ]

    for name, size, rounded, inset in targets:
        path = OUT / name
        draw_artwork(size, rounded=rounded, inset=inset).save(path, "PNG")
        print(f"{name:28} {size:>4}px  {path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
