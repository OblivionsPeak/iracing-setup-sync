"""Generate iRacingSetupSync.ico — steering wheel + sync arrow on Discord purple."""
from PIL import Image, ImageDraw
import math

def make_frame(size: int) -> Image.Image:
    s = size
    img = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy = s / 2, s / 2

    # ── Background circle ────────────────────────────────────────────────
    pad = s * 0.04
    d.ellipse([pad, pad, s - pad, s - pad], fill='#5865f2')

    # ── Steering wheel ───────────────────────────────────────────────────
    # Outer ring
    ring_r  = s * 0.34
    ring_w  = max(2, int(s * 0.07))
    d.ellipse([cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r],
              outline='white', width=ring_w)

    # Hub (centre circle)
    hub_r = s * 0.09
    d.ellipse([cx - hub_r, cy - hub_r, cx + hub_r, cy + hub_r], fill='white')

    # Three spokes at 90°, 210°, 330° (classic 3-spoke wheel)
    spoke_w = max(2, int(s * 0.06))
    for angle_deg in (90, 210, 330):
        angle = math.radians(angle_deg)
        inner_r = hub_r + s * 0.02
        outer_r = ring_r - ring_w / 2
        x1 = cx + inner_r * math.cos(angle)
        y1 = cy - inner_r * math.sin(angle)
        x2 = cx + outer_r * math.cos(angle)
        y2 = cy - outer_r * math.sin(angle)
        d.line([(x1, y1), (x2, y2)], fill='white', width=spoke_w)

    # ── Down-arrow (sync / download) — bottom-right corner ──────────────
    if size >= 32:
        ar = s * 0.22          # arrow circle radius
        ax = cx + s * 0.26
        ay = cy + s * 0.26

        # Badge circle (slightly darker purple for contrast)
        d.ellipse([ax - ar, ay - ar, ax + ar, ay + ar], fill='#23a55a')

        # Arrow body + head
        aw  = max(2, int(s * 0.05))   # shaft width
        ah  = ar * 0.55               # shaft half-height
        hw  = ar * 0.52               # arrowhead half-width
        hh  = ar * 0.38               # arrowhead height

        # Shaft
        d.rectangle([ax - aw, ay - ah, ax + aw, ay + hh - hh * 0.1],
                    fill='white')
        # Arrowhead (triangle)
        d.polygon([
            (ax - hw, ay + hh - hh * 0.55),
            (ax + hw, ay + hh - hh * 0.55),
            (ax,      ay + ah + hh * 0.15),
        ], fill='white')

    return img


if __name__ == '__main__':
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [make_frame(s) for s in sizes]
    out = 'iracing_setup_sync.ico'
    frames[0].save(
        out, format='ICO',
        sizes=[(s, s) for s in sizes],
        append_images=frames[1:],
    )
    print(f'Saved {out}')
    # Also save a 256px PNG for preview
    frames[-1].save('icon_preview.png')
    print('Saved icon_preview.png')
