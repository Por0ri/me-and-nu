from PIL import Image, ImageDraw

from ani_menu_agent.vision import detect_alpha_subject_box


def test_detect_alpha_subject_box(tmp_path) -> None:
    path = tmp_path / "subject.png"
    image = Image.new("RGBA", (100, 200), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 10, 79, 189), fill=(255, 255, 255, 255))
    image.save(path)

    box = detect_alpha_subject_box(path)

    assert box is not None
    assert box.x == 0.20
    assert box.y == 0.05
    assert box.width == 0.60
    assert box.height == 0.90
