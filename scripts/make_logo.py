"""Render the FSignal agent mark. Run from the repo root: `python scripts/make_logo.py`

An F whose arms are directory rows. The dashed rule is the moment YC publishes:
the lower arm stops dead against it, the amber top arm is already through, and
the dot past the line is the company found before the directory had it.
"""
from PIL import Image, ImageDraw, ImageFilter, ImageChops

S, SS = 512, 4
W = S * SS
def u(v): return int(round(v * SS))

INK     = (236, 243, 255)
INK_DIM = (96, 122, 170)
AMBER   = (250, 170, 34)
RULE    = (72, 95, 142)

BOUND = 292                                   # where the directory publishes
STEM_X0, STEM_X1 = 112, 170
TOP_Y0,  TOP_Y1  = 120, 178
LOW_Y0,  LOW_Y1  = 224, 278
STEM_Y1 = 392
TOP_X1  = 336                                 # crosses the rule
DOT_C, DOT_R = (388, 149), 25

base = Image.new("RGB", (W, W), (7, 11, 20))
d = ImageDraw.Draw(base)
for y in range(W):
    t = y / W
    d.line([(0, y), (W, y)], fill=(
        round(13 + (7 - 13) * t), round(20 + (11 - 20) * t), round(36 + (20 - 36) * t)))

y = 96                                        # dashed publication rule
while y < 416:
    d.line([(u(BOUND), u(y)), (u(BOUND), u(min(y + 21, 416)))], fill=RULE, width=u(5))
    y += 36

R = u(11)
def bar(draw, x0, y0, x1, y1, fill):
    draw.rounded_rectangle([u(x0), u(y0), u(x1), u(y1)], radius=R, fill=fill)

# Amber glow, screened over the ground so it adds light instead of a grey box.
glow = Image.new("RGB", (W, W), (0, 0, 0))
gd = ImageDraw.Draw(glow)
bar(gd, STEM_X0, TOP_Y0, TOP_X1, TOP_Y1, (150, 96, 14))
gd.ellipse([u(DOT_C[0] - DOT_R), u(DOT_C[1] - DOT_R),
            u(DOT_C[0] + DOT_R), u(DOT_C[1] + DOT_R)], fill=(150, 96, 14))
base = ImageChops.screen(base, glow.filter(ImageFilter.GaussianBlur(u(16))))

d = ImageDraw.Draw(base)
bar(d, STEM_X0, TOP_Y0, STEM_X1, STEM_Y1, INK)        # stem
bar(d, STEM_X0, LOW_Y0, BOUND,   LOW_Y1, INK_DIM)     # lower arm, held at the rule
bar(d, STEM_X0, TOP_Y0, TOP_X1,  TOP_Y1, AMBER)       # top arm, already through
d.ellipse([u(DOT_C[0] - DOT_R), u(DOT_C[1] - DOT_R),
           u(DOT_C[0] + DOT_R), u(DOT_C[1] + DOT_R)], fill=AMBER)

base = base.resize((S, S), Image.LANCZOS)
mask = Image.new("L", (W, W), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, W - 1], radius=u(112), fill=255)
out = Image.new("RGB", (S, S), (255, 255, 255))
out.paste(base, (0, 0), mask.resize((S, S), Image.LANCZOS))
out.save("docs/proof/fsignal_logo.png", "PNG", optimize=True)

# The mark has to survive a favicon, so check that size rather than assume it.
out.resize((32, 32), Image.LANCZOS).save("docs/proof/fsignal_logo_32.png")
print("wrote docs/proof/fsignal_logo.png and fsignal_logo_32.png")
