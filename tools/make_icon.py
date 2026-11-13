"""Draw the Noelle icosahedron logo with Pillow (same geometry/colours as web/img/noelle-logo.svg)
and write build/noelle.ico (16..256 px) + build/noelle-256.png."""
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build")

HEX = [(205, 8), (392, 125), (393, 343), (205, 440), (14, 340), (14, 120)]
FRONT_TRI = [(95, 165), (330, 168), (210, 365)]
BACK = [((205, 95), (85, 298)), ((85, 298), (330, 300)), ((330, 300), (205, 95)), ((205, 8), (205, 95)), ((14, 120), (205, 95)),
        ((392, 125), (205, 95)), ((14, 120), (85, 298)), ((14, 340), (85, 298)), ((205, 440), (85, 298)), ((392, 125), (330, 300)),
        ((393, 343), (330, 300)), ((205, 440), (330, 300))]
FRONT = [((205, 8), (95, 165)), ((205, 8), (330, 168)), ((14, 120), (95, 165)), ((14, 340), (95, 165)), ((14, 340), (210, 365)),
         ((392, 125), (330, 168)), ((393, 343), (330, 168)), ((393, 343), (210, 365)), ((210, 365), (205, 440))]


def draw(size):
    S = 4 * size                                    # supersample for smooth edges
    pad = 0.06
    sc = S * (1 - 2 * pad) / 452
    ox = S * pad + (S * (1 - 2 * pad) - 410 * sc) / 2
    oy = S * pad
    T = lambda p: (ox + p[0] * sc, oy + p[1] * sc)
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # golden body: vertical gradient clipped to the hexagon
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    top, mid, bot = (255, 241, 184), (244, 207, 90), (201, 154, 46)
    for y in range(S):
        f = y / S
        c = [int(a + (b - a) * (f / 0.45)) for a, b in zip(top, mid)] if f < 0.45 else [int(a + (b - a) * ((f - 0.45) / 0.55)) for a, b in zip(mid, bot)]
        gd.line([(0, y), (S, y)], fill=tuple(c) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).polygon([T(p) for p in HEX], fill=255)
    im.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(im, "RGBA")
    d.polygon([T(p) for p in FRONT_TRI], fill=(255, 240, 190, 45))
    wb, wf = max(2, int(10 * sc)), max(3, int(12 * sc))
    if size >= 32:
        for a, b in BACK:
            d.line([T(a), T(b)], fill=(176, 138, 82, 220), width=wb)
    for a, b in FRONT:
        d.line([T(a), T(b)], fill=(107, 63, 29, 255), width=wf)
    hexp = [T(p) for p in HEX]
    d.line(hexp + [hexp[0]], fill=(107, 63, 29, 255), width=wf, joint="curve")
    tri = [T(p) for p in FRONT_TRI]
    d.line(tri + [tri[0]], fill=(107, 63, 29, 255), width=wf, joint="curve")
    for p in hexp + tri:                             # round joints
        r = wf / 2
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=(107, 63, 29, 255))
    return im.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [draw(s) for s in sizes]
    imgs[-1].save(os.path.join(OUT, "noelle-256.png"))
    imgs[-1].save(os.path.join(OUT, "noelle.ico"), sizes=[(s, s) for s in sizes], append_images=imgs[:-1])
    print("wrote", os.path.join(OUT, "noelle.ico"))
