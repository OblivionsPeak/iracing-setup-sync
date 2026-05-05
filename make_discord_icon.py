"""Generate discord_app_icon.png — 1024x1024, designed for Discord's circular crop."""
from PIL import Image, ImageDraw, ImageFilter
import math

SIZE = 1024
cx, cy = SIZE / 2, SIZE / 2

img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
d   = ImageDraw.Draw(img)

# ── Background — solid Discord purple, fills full square ─────────────────
d.rectangle([0, 0, SIZE, SIZE], fill='#5865f2')

# ── Subtle radial vignette (darkens edges slightly) ──────────────────────
vig = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
vd  = ImageDraw.Draw(vig)
for r in range(int(SIZE * 0.72), int(SIZE * 0.5), -4):
    alpha = int(80 * (1 - (r - SIZE * 0.5) / (SIZE * 0.22)))
    vd.ellipse([cx - r, cy - r, cx + r, cy + r],
               outline=(0, 0, 20, max(0, alpha)), width=4)
img = Image.alpha_composite(img, vig)
d   = ImageDraw.Draw(img)

# ── Glow behind wheel ────────────────────────────────────────────────────
glow = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
gd   = ImageDraw.Draw(glow)
gr   = int(SIZE * 0.38)
gd.ellipse([cx - gr, cy - gr, cx + gr, cy + gr], fill=(255, 255, 255, 28))
glow = glow.filter(ImageFilter.GaussianBlur(radius=SIZE * 0.06))
img  = Image.alpha_composite(img, glow)
d    = ImageDraw.Draw(img)

# ── Steering wheel ───────────────────────────────────────────────────────
wheel_r = SIZE * 0.33
ring_w  = int(SIZE * 0.055)

# Outer ring
d.ellipse([cx - wheel_r, cy - wheel_r, cx + wheel_r, cy + wheel_r],
          outline='white', width=ring_w)

# Grip flat-spots on ring (3 pairs of darker arcs for realism)
for angle_deg in (90, 210, 330):
    for delta in (-18, 18):
        a = math.radians(angle_deg + delta)
        x = cx + wheel_r * math.cos(a)
        y = cy - wheel_r * math.sin(a)

# Hub
hub_r = SIZE * 0.075
d.ellipse([cx - hub_r, cy - hub_r, cx + hub_r, cy + hub_r], fill='white')
# Hub inner detail
hib_r = SIZE * 0.035
d.ellipse([cx - hib_r, cy - hib_r, cx + hib_r, cy + hib_r], fill='#5865f2')

# Three spokes — thicker at hub, slightly tapered feel via two overlapping lines
for angle_deg in (90, 210, 330):
    angle   = math.radians(angle_deg)
    inner_r = hub_r + SIZE * 0.01
    outer_r = wheel_r - ring_w * 0.5
    x1 = cx + inner_r * math.cos(angle)
    y1 = cy - inner_r * math.sin(angle)
    x2 = cx + outer_r * math.cos(angle)
    y2 = cy - outer_r * math.sin(angle)
    sw = int(SIZE * 0.052)
    d.line([(x1, y1), (x2, y2)], fill='white', width=sw)
    # Slightly narrower centre highlight for depth
    d.line([(x1, y1), (x2, y2)], fill=(255, 255, 255, 120), width=max(2, sw // 3))

# ── Download arrow — centred below wheel hub ──────────────────────────────
arrow_cy = cy + SIZE * 0.115     # slightly below centre
shaft_hw = SIZE * 0.045          # shaft half-width
shaft_top = cy + SIZE * 0.01     # shaft starts just below hub
shaft_bot = arrow_cy + SIZE * 0.025
head_hw   = SIZE * 0.105         # arrowhead half-width
head_tip  = arrow_cy + SIZE * 0.115

# White arrow
d.rectangle([cx - shaft_hw, shaft_top, cx + shaft_hw, shaft_bot], fill='white')
d.polygon([
    (cx - head_hw, shaft_bot),
    (cx + head_hw, shaft_bot),
    (cx,           head_tip),
], fill='white')

# ── Thin speed-line arcs (decorative, subtle) ────────────────────────────
arc_r = SIZE * 0.44
arc_w = max(2, int(SIZE * 0.008))
for offset, span, alpha in [(-28, 56, 60), (-20, 40, 35)]:
    arc_img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    ad = ImageDraw.Draw(arc_img)
    ad.arc([cx - arc_r, cy - arc_r, cx + arc_r, cy + arc_r],
           start=180 + offset, end=180 + offset + span,
           fill=(255, 255, 255, alpha), width=arc_w)
    ad.arc([cx - arc_r, cy - arc_r, cx + arc_r, cy + arc_r],
           start=offset, end=offset + span,
           fill=(255, 255, 255, alpha), width=arc_w)
    img = Image.alpha_composite(img, arc_img)

# ── Save ──────────────────────────────────────────────────────────────────
out = 'discord_app_icon.png'
img.save(out, 'PNG')
print(f'Saved {out}  ({SIZE}x{SIZE})')
