"""Recover vector geometry from an isolated render of ONE object (transparent background).

GraphWorX32's automation API does not expose polyline/polygon vertices, so the exporter
renders each such object alone; here we turn its pixels back into points:

* filled shapes  -> outer contour (Moore neighbour tracing) -> Douglas-Peucker
* open polylines -> Zhang-Suen thinning (skeleton) -> path walk -> Douglas-Peucker

Pure Python + Pillow; objects are small crops, so this is fast enough.
"""
from __future__ import annotations

from PIL import Image

N8 = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]


def load_mask(path):
    im = Image.open(path).convert("RGBA")
    w, h = im.size
    a = im.getchannel("A").load()
    return [[a[x, y] > 0 for x in range(w)] for y in range(h)], w, h


def _largest_component(m, w, h):
    seen = [[False] * w for _ in range(h)]
    best = []
    for y in range(h):
        for x in range(w):
            if m[y][x] and not seen[y][x]:
                comp, stack = [], [(x, y)]
                seen[y][x] = True
                while stack:
                    cx, cy = stack.pop()
                    comp.append((cx, cy))
                    for dx, dy in N8:
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < w and 0 <= ny < h and m[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True
                            stack.append((nx, ny))
                if len(comp) > len(best):
                    best = comp
    out = [[False] * w for _ in range(h)]
    for x, y in best:
        out[y][x] = True
    return out, len(best)


def contour(m, w, h):
    """Moore-neighbour outer boundary of the (single) component, clockwise pixel list."""
    start = None
    for y in range(h):
        for x in range(w):
            if m[y][x]:
                start = (x, y)
                break
        if start:
            break
    if not start:
        return []
    pts = [start]
    cur, back = start, 4             # we entered from the left (west)
    for _ in range(4 * (w + h) * 8):
        found = False
        for i in range(8):
            d = (back + 1 + i) % 8
            nx, ny = cur[0] + N8[d][0], cur[1] + N8[d][1]
            if 0 <= nx < w and 0 <= ny < h and m[ny][nx]:
                back = (d + 4) % 8
                cur = (nx, ny)
                found = True
                break
        if not found or cur == start:
            break
        pts.append(cur)
    return pts


def _dp(pts, eps):
    if len(pts) < 3:
        return pts
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2 - x1, y2 - y1
    L = (dx * dx + dy * dy) ** 0.5 or 1e-9
    best, idx = -1.0, 0
    for i in range(1, len(pts) - 1):
        px, py = pts[i]
        d = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / L if (dx or dy) else ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
        if d > best:
            best, idx = d, i
    if best <= eps:
        return [pts[0], pts[-1]]
    return _dp(pts[: idx + 1], eps)[:-1] + _dp(pts[idx:], eps)


def simplify_closed(pts, eps=1.0):
    if len(pts) < 4:
        return pts
    # split at the point farthest from the first one so DP works on two open halves
    far = max(range(len(pts)), key=lambda i: (pts[i][0] - pts[0][0]) ** 2 + (pts[i][1] - pts[0][1]) ** 2)
    a = _dp(pts[: far + 1], eps)
    b = _dp(pts[far:] + [pts[0]], eps)
    return a[:-1] + b[:-1]


def thin(m, w, h):
    """Zhang-Suen thinning (in place copy)."""
    img = [[1 if m[y][x] else 0 for x in range(w)] for y in range(h)]
    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            rem = []
            for y in range(1, h - 1):
                row_u, row, row_d = img[y - 1], img[y], img[y + 1]
                for x in range(1, w - 1):
                    if not row[x]:
                        continue
                    p2, p3, p4, p5 = row_u[x], row_u[x + 1], row[x + 1], row_d[x + 1]
                    p6, p7, p8, p9 = row_d[x], row_d[x - 1], row[x - 1], row_u[x - 1]
                    nb = [p2, p3, p4, p5, p6, p7, p8, p9]
                    B = sum(nb)
                    if B < 2 or B > 6:
                        continue
                    A = sum(1 for i in range(8) if nb[i] == 0 and nb[(i + 1) % 8] == 1)
                    if A != 1:
                        continue
                    if step == 0 and (p2 * p4 * p6 or p4 * p6 * p8):
                        continue
                    if step == 1 and (p2 * p4 * p8 or p2 * p6 * p8):
                        continue
                    rem.append((x, y))
            for x, y in rem:
                img[y][x] = 0
            changed = changed or bool(rem)
    return img


def skeleton_path(img, w, h):
    pix = {(x, y) for y in range(h) for x in range(w) if img[y][x]}
    if not pix:
        return []

    def nbrs(p):
        return [(p[0] + dx, p[1] + dy) for dx, dy in N8 if (p[0] + dx, p[1] + dy) in pix]

    ends = [p for p in pix if len(nbrs(p)) == 1]

    def walk(start):
        path, seen, cur = [start], {start}, start
        while True:
            nx = [q for q in nbrs(cur) if q not in seen]
            if not nx:
                return path
            # prefer 4-connected steps (avoids zig-zag), then the one continuing the direction
            nx.sort(key=lambda q: (abs(q[0] - cur[0]) + abs(q[1] - cur[1])))
            cur = nx[0]
            seen.add(cur)
            path.append(cur)

    if ends:
        # longest path from any endpoint (sample a few endpoints for speed)
        best = []
        for e in ends[:6]:
            p = walk(e)
            if len(p) > len(best):
                best = p
        return best
    return walk(next(iter(pix)))


def vectorize(path, filled: bool, ox: float, oy: float, eps: float = 1.0):
    """-> {"points": [[x,y],...], "closed": bool, "width": est. stroke width} or None"""
    m, w, h = load_mask(path)
    m, n = _largest_component(m, w, h)
    if n < 2:
        return None
    if filled:
        c = contour(m, w, h)
        if len(c) < 3:
            return None
        pts = simplify_closed(c, eps)
        return {"points": [[round(x + ox + 0.5, 1), round(y + oy + 0.5, 1)] for x, y in pts], "closed": True}
    sk = thin(m, w, h)
    skel_n = sum(map(sum, sk)) or 1
    width = max(1, round(n / skel_n))
    path_px = skeleton_path(sk, w, h)
    if len(path_px) < 2:
        return None
    closed = len(path_px) > 8 and abs(path_px[0][0] - path_px[-1][0]) <= 1 and abs(path_px[0][1] - path_px[-1][1]) <= 1
    pts = _dp(path_px, eps)
    return {"points": [[round(x + ox + 0.5, 1), round(y + oy + 0.5, 1)] for x, y in pts], "closed": closed, "width": width}
