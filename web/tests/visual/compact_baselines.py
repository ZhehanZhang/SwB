from io import BytesIO
from pathlib import Path

from PIL import Image

RGB_LEVELS = [round(round(channel * 31 / 255) * 255 / 31) for channel in range(256)] * 3


def compact(path: Path) -> int:
    original = path.read_bytes()
    with Image.open(BytesIO(original)) as image:
        if image.convert("RGBA").getchannel("A").getextrema() != (255, 255):
            raise ValueError(f"Expected an opaque Playwright screenshot: {path}")
        rgb = image.convert("RGB")
        alternatives = [original]
        for candidate in (rgb, rgb.point(RGB_LEVELS)):
            encoded = BytesIO()
            candidate.save(encoded, format="PNG", optimize=True)
            alternatives.append(encoded.getvalue())
    result = min(alternatives, key=len)
    if result != original:
        path.write_bytes(result)
    return len(original) - len(result)


if __name__ == "__main__":
    images = sorted(Path("__screenshots__").glob("*/*.png"))
    if not images:
        raise SystemExit("No visual baselines found")
    saved = sum(map(compact, images))
    print(f"Optimized {len(images)} PNGs and saved {saved / 2**20:.2f} MiB")
