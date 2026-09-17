from .image import load_image
from .judge import judge


def run(image_url: str) -> dict:
    image = load_image(image_url)
    result = judge(image)
    return result

