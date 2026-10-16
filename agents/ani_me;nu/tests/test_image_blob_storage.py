import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PIL import Image

from ani_menu_agent.models import ContentPackage
from ani_menu_agent.storage import AgentRepository

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "generation_input.json"


def test_image_bytes_are_deduplicated_in_database_and_retrievable() -> None:
    package = ContentPackage.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    image_buffer = io.BytesIO()
    Image.new("RGB", (3, 2), color="purple").save(image_buffer, format="JPEG")
    binary = image_buffer.getvalue()
    package.images[0] = package.images[0].model_copy(
        update={"content_bytes": binary, "media_type": "image/jpeg"}
    )
    package.images[1] = package.images[1].model_copy(
        update={"content_bytes": binary, "media_type": "image/jpeg"}
    )
    repository = AgentRepository()

    repository.persist_images(package)

    expected_key = hashlib.sha256(binary).hexdigest()
    stored = repository.get_image_bytes(expected_key)
    assert stored == (binary, "image/jpeg")
    assert package.images[0].storage_key == expected_key
    assert package.images[0].local_path is None
    assert package.images[0].content_bytes is None
    with repository.connect() as connection:
        count = connection.execute("SELECT COUNT(*) AS count FROM image_blobs").fetchone()["count"]
        assert count == 1


def test_image_binary_is_excluded_from_package_json() -> None:
    package = ContentPackage.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    package.images[0] = package.images[0].model_copy(update={"content_bytes": b"private-binary"})

    serialized = json.loads(package.model_dump_json())

    assert "content_bytes" not in serialized["images"][0]


def test_expired_unreferenced_image_blob_is_removed() -> None:
    package = ContentPackage.model_validate_json(SAMPLE.read_text(encoding="utf-8"))
    image_buffer = io.BytesIO()
    Image.new("RGB", (3, 2), color="purple").save(image_buffer, format="JPEG")
    binary = image_buffer.getvalue()
    package.images[0] = package.images[0].model_copy(update={"content_bytes": binary})
    repository = AgentRepository()
    repository.persist_images(package)
    storage_key = package.images[0].storage_key
    expired_at = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    with repository.connect() as connection:
        connection.execute(
            "UPDATE agent_records SET expires_at=? WHERE agent_type='image'",
            (expired_at,),
        )

    repository.cleanup_expired()

    assert repository.get_image_bytes(storage_key) is None
