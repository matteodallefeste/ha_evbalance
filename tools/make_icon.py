"""Genera icon/logo (SVG + PNG) per EV Balance (brands HACS).

Icona: quadrante "gauge" di sfondo + una BILANCIA (asta su fulcro) con una
batteria (auto/EV) su un piatto e una lavatrice (carico di casa) sull'altro ->
idea di bilanciamento dei consumi. Logotipo: "EV·Balance".

Il colore del testo (BASE) genera anche la sfumatura dell'icona. Nessun
rasterizzatore SVG esterno: l'SVG e' scritto a mano e i PNG sono renderizzati
con PIL dalla stessa geometria.
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

OUT = os.environ["OUT_DIR"]
SS = 4  # supersampling PNG

BASE = (39, 190, 150)     # verde-teal (colore logotipo + seme gradiente)


def shade(c, f):
    if f >= 0:
        return tuple(int(round(c[i] + (255 - c[i]) * f)) for i in range(3))
    return tuple(int(round(c[i] * (1 + f))) for i in range(3))


LIGHT = shade(BASE, 0.16)
DARK = shade(BASE, -0.42)
TEXT = BASE
DOT = (16, 78, 92)        # interpunto scuro
WHITE = (255, 255, 255)


def rgb(c):
    return f"rgb({c[0]},{c[1]},{c[2]})"


def _polar(cx, cy, r, deg):
    a = math.radians(deg)
    return (cx + r * math.cos(a), cy + r * math.sin(a))


def bolt_pts(cx, cy, s):
    pts = [
        (0.10, -0.55), (-0.28, 0.06), (-0.02, 0.06),
        (-0.12, 0.55), (0.30, -0.10), (0.02, -0.10),
    ]
    return [(cx + x * s, cy + y * s) for x, y in pts]


# ---- Gauge di sfondo ----
ARC_START, ARC_END, N_TICKS = 150, 390, 7


def _gauge_geom(S):
    cx = cy = S * 0.5
    R = S * 0.36
    p0 = _polar(cx, cy, R, ARC_START)
    p1 = _polar(cx, cy, R, ARC_END)
    ticks = [(_polar(cx, cy, R, ARC_START + (ARC_END - ARC_START) * i / (N_TICKS - 1)),
              _polar(cx, cy, R - S * 0.05, ARC_START + (ARC_END - ARC_START) * i / (N_TICKS - 1)))
             for i in range(N_TICKS)]
    return cx, cy, R, p0, p1, ticks


# ---- Simbolo BILANCIA (primitive bianche opache) ----
def _balance(S):
    yb = 0.52 * S                     # asse dell'asta
    bx0, bx1 = 0.24 * S, 0.76 * S
    hb = 0.05 * S
    ytop = yb - hb / 2                # superficie su cui poggiano i carichi
    swi = 0.020 * S                   # tratto icone
    p = []

    # asta
    p.append({"t": "rrect", "x": bx0, "y": yb - hb / 2, "w": bx1 - bx0,
              "h": hb, "r": hb / 2, "mode": "fill"})
    # fulcro (triangolo) + base a terra
    fw, fbase = 0.10 * S, 0.74 * S
    p.append({"t": "poly", "mode": "fill", "pts": [
        (0.5 * S, yb + hb / 2), (0.5 * S - fw, fbase), (0.5 * S + fw, fbase)]})
    p.append({"t": "rrect", "x": 0.5 * S - fw * 1.15, "y": fbase - 0.004 * S,
              "w": fw * 2.3, "h": 0.03 * S, "r": 0.015 * S, "mode": "fill"})

    # --- carico sinistro: BATTERIA (EV) ---
    xL, bw, bh = 0.325 * S, 0.13 * S, 0.145 * S
    bxL, byL = xL - bw / 2, ytop - bh
    p.append({"t": "rrect", "x": bxL, "y": byL, "w": bw, "h": bh,
              "r": 0.026 * S, "mode": "stroke", "sw": swi})
    tw, th = 0.05 * S, 0.022 * S       # polo
    p.append({"t": "rrect", "x": xL - tw / 2, "y": byL - th, "w": tw, "h": th,
              "r": 0.008 * S, "mode": "fill"})
    p.append({"t": "poly", "mode": "fill",
              "pts": bolt_pts(xL, byL + bh / 2, 0.085 * S)})  # fulmine interno

    # --- carico destro: LAVATRICE ---
    xR, mw, mh = 0.675 * S, 0.16 * S, 0.16 * S
    mxR, myR = xR - mw / 2, ytop - mh
    p.append({"t": "rrect", "x": mxR, "y": myR, "w": mw, "h": mh,
              "r": 0.026 * S, "mode": "stroke", "sw": swi})
    cpy = myR + 0.036 * S               # pannello comandi
    p.append({"t": "line", "x1": mxR + 0.028 * S, "y1": cpy,
              "x2": mxR + mw - 0.06 * S, "y2": cpy, "sw": 0.014 * S})
    p.append({"t": "circle", "cx": mxR + mw - 0.038 * S, "cy": cpy,
              "r": 0.011 * S, "mode": "fill"})               # manopola
    dcy = myR + mh * 0.60
    p.append({"t": "circle", "cx": xR, "cy": dcy, "r": 0.05 * S,
              "mode": "stroke", "sw": swi})                  # oblo'
    p.append({"t": "circle", "cx": xR, "cy": dcy, "r": 0.021 * S,
              "mode": "stroke", "sw": 0.012 * S})            # cestello
    return p


# =============== renderers primitive (bianco) ===============
def _prim_svg(p, ox=0, oy=0):
    m = p.get("mode", "stroke")
    common = 'fill="#fff"' if m == "fill" else (
        f'fill="none" stroke="#fff" stroke-width="{p.get("sw",1):.2f}" '
        f'stroke-linecap="round" stroke-linejoin="round"')
    t = p["t"]
    if t == "rrect":
        return (f'<rect x="{ox+p["x"]:.2f}" y="{oy+p["y"]:.2f}" '
                f'width="{p["w"]:.2f}" height="{p["h"]:.2f}" rx="{p["r"]:.2f}" '
                f'{common}/>')
    if t == "circle":
        return (f'<circle cx="{ox+p["cx"]:.2f}" cy="{oy+p["cy"]:.2f}" '
                f'r="{p["r"]:.2f}" {common}/>')
    if t == "line":
        return (f'<line x1="{ox+p["x1"]:.2f}" y1="{oy+p["y1"]:.2f}" '
                f'x2="{ox+p["x2"]:.2f}" y2="{oy+p["y2"]:.2f}" {common}/>')
    if t == "poly":
        pts = " ".join(f"{ox+x:.2f},{oy+y:.2f}" for x, y in p["pts"])
        return f'<polygon points="{pts}" {common}/>'
    return ""


def _seg(d, p1, p2, w, fill):
    d.line([p1, p2], fill=fill, width=max(1, int(round(w))))
    r = w / 2
    for q in (p1, p2):
        d.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], fill=fill)


def _prim_pil(d, p):
    m = p.get("mode", "stroke")
    col = WHITE + (255,)
    t = p["t"]
    sw = max(1, int(round(p.get("sw", 1))))
    if t == "rrect":
        box = [p["x"], p["y"], p["x"] + p["w"], p["y"] + p["h"]]
        if m == "fill":
            d.rounded_rectangle(box, radius=p["r"], fill=col)
        else:
            d.rounded_rectangle(box, radius=p["r"], outline=col, width=sw)
    elif t == "circle":
        box = [p["cx"] - p["r"], p["cy"] - p["r"], p["cx"] + p["r"], p["cy"] + p["r"]]
        if m == "fill":
            d.ellipse(box, fill=col)
        else:
            d.ellipse(box, outline=col, width=sw)
    elif t == "line":
        _seg(d, (p["x1"], p["y1"]), (p["x2"], p["y2"]), p["sw"], col)
    elif t == "poly":
        d.polygon(p["pts"], fill=col)


# =================== ICONA ===================
def _icon_svg_defs():
    return (f'<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{rgb(LIGHT)}"/>'
            f'<stop offset="1" stop-color="{rgb(DARK)}"/></linearGradient>')


def _icon_svg_body(S, ox=0, oy=0):
    cx, cy, R, p0, p1, ticks = _gauge_geom(S)
    el = [f'<rect x="{ox}" y="{oy}" width="{S}" height="{S}" '
          f'rx="{S*0.22:.2f}" fill="url(#bg)"/>']
    el.append(f'<path d="M {ox+p0[0]:.2f},{oy+p0[1]:.2f} A {R:.2f} {R:.2f} 0 1 1 '
              f'{ox+p1[0]:.2f},{oy+p1[1]:.2f}" fill="none" stroke="#fff" '
              f'stroke-opacity="0.34" stroke-width="{S*0.028:.2f}" '
              f'stroke-linecap="round"/>')
    for a, b in ticks:
        el.append(f'<line x1="{ox+a[0]:.2f}" y1="{oy+a[1]:.2f}" '
                  f'x2="{ox+b[0]:.2f}" y2="{oy+b[1]:.2f}" stroke="#fff" '
                  f'stroke-opacity="0.42" stroke-width="{S*0.016:.2f}" '
                  f'stroke-linecap="round"/>')
    for p in _balance(S):
        el.append(_prim_svg(p, ox, oy))
    return "\n".join(el)


def _translucent(size, draw_fn, opacity):
    mask = Image.new("L", size, 0)
    draw_fn(ImageDraw.Draw(mask))
    layer = Image.new("RGBA", size, WHITE + (0,))
    layer.putalpha(mask.point(lambda a: int(a * opacity)))
    return layer


def _draw_icon_full(S):
    grad = Image.new("RGB", (S, S))
    px = grad.load()
    for y in range(S):
        c = tuple(int(round(LIGHT[i] + (DARK[i] - LIGHT[i]) * y / (S - 1)))
                  for i in range(3))
        for x in range(S):
            px[x, y] = c
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1],
                                           radius=int(S * 0.22), fill=255)
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    icon.paste(grad, (0, 0), mask)

    cx, cy, R, p0, p1, ticks = _gauge_geom(S)

    def arc(d):
        d.arc([cx - R, cy - R, cx + R, cy + R], ARC_START, ARC_END,
              fill=255, width=int(S * 0.028))
        r = S * 0.028 / 2
        for q in (p0, p1):
            d.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], fill=255)
    icon = Image.alpha_composite(icon, _translucent((S, S), arc, 0.34))

    def tks(d):
        for a, b in ticks:
            _seg(d, a, b, S * 0.016, 255)
    icon = Image.alpha_composite(icon, _translucent((S, S), tks, 0.42))

    d = ImageDraw.Draw(icon)
    for p in _balance(S):
        _prim_pil(d, p)
    return icon


# =================== LOGOTIPO "EV·Balance" ===================
def _load_font(px):
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default()


def _word_metrics(S):
    font = _load_font(int(0.40 * S))
    d = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    segs = [("EV", TEXT), ("·", DOT), ("Balance", TEXT)]
    widths = [d.textbbox((0, 0), t, font=font)[2] for t, _ in segs]
    gap = int(0.045 * S)
    total = sum(widths) + gap * 2
    return font, segs, widths, gap, total


def _draw_word(img, ox, S):
    font, segs, widths, gap, _ = _word_metrics(S)
    ascent, descent = font.getmetrics()
    y = (S - (ascent + descent)) // 2
    d = ImageDraw.Draw(img)
    x = ox
    for (txt, color), w in zip(segs, widths):
        d.text((x, y), txt, font=font, fill=color + (255,))
        x += w + (gap if txt != "Balance" else 0)


def _word_svg(ox, S):
    fs = int(0.40 * S)
    return (f'<text x="{ox}" y="{S*0.5:.1f}" font-family="Segoe UI,Arial,'
            f'sans-serif" font-weight="700" font-size="{fs}" '
            f'dominant-baseline="central" fill="{rgb(TEXT)}">EV'
            f'<tspan dx="2" fill="{rgb(DOT)}">·</tspan>'
            f'<tspan dx="2">Balance</tspan></text>')


# =================== output ===================
def write_icon_svg(path, S=256):
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{S}" '
                f'height="{S}" viewBox="0 0 {S} {S}"><defs>{_icon_svg_defs()}'
                f'</defs>\n{_icon_svg_body(S)}\n</svg>\n')


def write_logo_svg(path, S=256):
    pad = 0.14 * S
    _, _, _, _, wtxt = _word_metrics(S)
    W = int(round(S + pad + wtxt + pad))
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" '
                f'height="{S}" viewBox="0 0 {W} {S}"><defs>{_icon_svg_defs()}'
                f'</defs>\n{_icon_svg_body(S)}\n{_word_svg(int(S+pad), S)}\n'
                f'</svg>\n')


def build_icon_png(px):
    return _draw_icon_full(px * SS).resize((px, px), Image.LANCZOS)


def build_logo_png(px):
    S = px * SS
    pad = 0.14 * S
    _, _, _, _, wtxt = _word_metrics(S)
    W = int(round(S + pad + wtxt + pad))
    logo = Image.new("RGBA", (W, S), (0, 0, 0, 0))
    logo.alpha_composite(_draw_icon_full(S), (0, 0))
    _draw_word(logo, int(S + pad), S)
    return logo.resize((int(round(W / SS)), px), Image.LANCZOS)


write_icon_svg(os.path.join(OUT, "icon.svg"))
write_logo_svg(os.path.join(OUT, "logo.svg"))
build_icon_png(256).save(os.path.join(OUT, "icon.png"))
build_icon_png(512).save(os.path.join(OUT, "icon@2x.png"))
build_logo_png(256).save(os.path.join(OUT, "logo.png"))
build_logo_png(512).save(os.path.join(OUT, "logo@2x.png"))
print("generati:", sorted(os.listdir(OUT)))
