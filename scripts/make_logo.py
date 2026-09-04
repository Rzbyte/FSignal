"""Render the FSignal agent mark. Run from the repo root: `python scripts/make_logo.py`

An F whose arms are directory rows. The dashed rule is the moment YC publishes:
the lower arm stops dead against it, the amber top arm is already through, and
the dot past the line is the company found before the directory had it.

Marketplaces crop an agent logo to a circle, so the mark is composed on its own
layer and then inset -- everything has to live inside the inscribed circle, not
merely inside the square.
"""
from PIL import Image, ImageChops, ImageDraw, ImageFilter

S, SS = 512, 4
W = S * SS
def u(v): return round(v * SS)

INK     = (236, 243, 255)
INK_DIM = (96, 122, 170)
AMBER   = (250, 170, 34)
RULE    = (78, 102, 150)

BOUND = 292                                    # where the directory publishes
STEM_X0, STEM_X1 = 112, 170
TOP_Y0,  TOP_Y1  = 120, 178
LOW_Y0,  LOW_Y1  = 224, 278
STEM_Y1 = 392
TOP_X1  = 336                                  # crosses the rule
DOT_C, DOT_R = (388, 149), 25

INSET = 0.84                                   # keeps the mark clear of a circular crop

# --- ground -----------------------------------------------------------------
ground = Image.new("RGB", (W, W), (7, 11, 20))
g = ImageDraw.Draw(ground)
for y in range(W):
    t = y / W
    g.line([(0, y), (W, y)], fill=(
        round(13 + (7 - 13) * t), round(20 + (11 - 20) * t), round(36 + (20 - 36) * t)))

# --- the mark, on its own layer so it can be inset as a whole ----------------
mark = Image.new("RGBA", (W, W), (0, 0, 0, 0))
m = ImageDraw.Draw(mark)

y = 96
while y < 416:                                 # dashed publication rule
    m.line([(u(BOUND), u(y)), (u(BOUND), u(min(y + 24, 416)))], fill=RULE + (255,), width=u(6))
    y += 40

R = u(11)
def bar(draw, x0, y0, x1, y1, fill):
    draw.rounded_rectangle([u(x0), u(y0), u(x1), u(y1)], radius=R, fill=fill)

def dot(draw, fill):
    draw.ellipse([u(DOT_C[0] - DOT_R), u(DOT_C[1] - DOT_R),
                  u(DOT_C[0] + DOT_R), u(DOT_C[1] + DOT_R)], fill=fill)

bar(m, STEM_X0, TOP_Y0, STEM_X1, STEM_Y1, INK + (255,))       # stem
bar(m, STEM_X0, LOW_Y0, BOUND,   LOW_Y1, INK_DIM + (255,))    # lower arm, held at the rule
bar(m, STEM_X0, TOP_Y0, TOP_X1,  TOP_Y1, AMBER + (255,))      # top arm, already through
dot(m, AMBER + (255,))                                        # the company it found

# Amber glow, screened onto the ground so it adds light rather than a grey box.
halo = Image.new("RGB", (W, W), (0, 0, 0))
h = ImageDraw.Draw(halo)
bar(h, STEM_X0, TOP_Y0, TOP_X1, TOP_Y1, (150, 96, 14))
dot(h, (150, 96, 14))

def inset(img):
    small = img.resize((int(W * INSET), int(W * INSET)), Image.LANCZOS)
    out = Image.new(img.mode, (W, W), (0, 0, 0, 0) if img.mode == "RGBA" else (0, 0, 0))
    off = (W - small.width) // 2
    out.paste(small, (off, off))
    return out

canvas = ImageChops.screen(ground, inset(halo).filter(ImageFilter.GaussianBlur(u(16))))
canvas.paste(inset(mark), (0, 0), inset(mark))

canvas = canvas.resize((S, S), Image.LANCZOS)
sq = Image.new("L", (W, W), 0)
ImageDraw.Draw(sq).rounded_rectangle([0, 0, W - 1, W - 1], radius=u(112), fill=255)
out = Image.new("RGB", (S, S), (255, 255, 255))
out.paste(canvas, (0, 0), sq.resize((S, S), Image.LANCZOS))
out.save("docs/proof/fsignal_logo.png", "PNG", optimize=True)

# The mark has to survive both a favicon and a circular avatar, so render those
# rather than assume them.
out.resize((32, 32), Image.LANCZOS).save("docs/proof/fsignal_logo_32.png")
circ = Image.new("L", (W, W), 0)
ImageDraw.Draw(circ).ellipse([0, 0, W - 1, W - 1], fill=255)
avatar = Image.new("RGB", (S, S), (255, 255, 255))
avatar.paste(canvas, (0, 0), circ.resize((S, S), Image.LANCZOS))
avatar.save("docs/proof/fsignal_logo_circle.png")
print("wrote fsignal_logo.png, _32.png, _circle.png")
