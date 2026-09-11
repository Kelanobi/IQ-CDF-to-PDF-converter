from pathlib import Path
import shutil
from PIL import Image, ImageDraw, ImageFont

assets = Path(__file__).parent / "assets"
assets.mkdir(exist_ok=True)
shutil.copy2(Path.home() / "Downloads" / "Qualitrol-logo.png", assets / "Qualitrol-logo.png")
# A dedicated launcher glyph stays legible at small Windows icon sizes.
icon = Image.new("RGBA", (256, 256), (255, 255, 255, 255))
draw = ImageDraw.Draw(icon)
font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 220)
box = draw.textbbox((0, 0), "Q", font=font)
draw.text(((256 - (box[2] - box[0])) / 2 - box[0], (246 - (box[3] - box[1])) / 2 - box[1]), "Q", font=font, fill="#343a3c")
draw.rectangle((0, 242, 256, 256), fill="#da0712")
icon.save(assets / "q.ico", sizes=[(16,16), (32,32), (48,48), (64,64), (128,128), (256,256)])
