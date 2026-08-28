# -*- coding: utf-8 -*-
"""Generate StratoXFilter.ico -- industrial 'X-axis' motif.
Dark steel rounded square, bold amber X, horizontal travel arrow beneath."""
from PIL import Image, ImageDraw

S = 512
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# rounded-square background (steel blue-grey)
m = 24
bg = (38, 50, 66, 255)      # dark steel
edge = (70, 90, 115, 255)   # lighter rim
d.rounded_rectangle([m, m, S - m, S - m], radius=90, fill=bg, outline=edge, width=10)

# bold X (amber) drawn as two thick strokes
amber = (245, 170, 40, 255)
w = 46
x0, y0, x1, y1 = 150, 120, 362, 332
d.line([(x0, y0), (x1, y1)], fill=amber, width=w)
d.line([(x0, y1), (x1, y0)], fill=amber, width=w)
# round the stroke ends
r = w // 2
for (cx, cy) in [(x0, y0), (x1, y1), (x0, y1), (x1, y0)]:
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=amber)

# horizontal double-headed travel arrow beneath the X (X-axis hint)
ay = 400
axl, axr = 150, 362
lw = 20
light = (205, 215, 228, 255)
d.line([(axl, ay), (axr, ay)], fill=light, width=lw)
head = 34
d.polygon([(axl, ay), (axl + head, ay - head // 2), (axl + head, ay + head // 2)], fill=light)
d.polygon([(axr, ay), (axr - head, ay - head // 2), (axr - head, ay + head // 2)], fill=light)

sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
img.save("StratoXFilter.ico", sizes=sizes)
img.resize((256, 256), Image.LANCZOS).save("icon_preview.png")
print("wrote StratoXFilter.ico + icon_preview.png")
