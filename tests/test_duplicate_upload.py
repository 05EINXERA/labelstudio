import io
import pytest


def _create_image_bytes(name="test.png") -> io.BytesIO:
    # Minimal 1x1 valid PNG
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff"
        b"?"
        b"\x03\x05\x00\x0b\x28\x02\x7f\xdbQ\x98\xea\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    bio = io.BytesIO(png_bytes)
    bio.name = name
    return bio


def test_upload_allows_duplicates_by_default(client, alice):
    # 1. Create a project
    res = client.post("/api/projects", json={"name": "Dup Proj 1", "slug": "dup-proj-1", "creator": "ignored"}, headers=alice)
    assert res.status_code == 200
    project_id = res.json()["id"]

    # 2. Upload an image
    file1 = ("file", ("sample.png", _create_image_bytes("sample.png"), "image/png"))
    res_up1 = client.post(f"/api/projects/{project_id}/upload", files=[file1], headers=alice)
    assert res_up1.status_code == 200
    body1 = res_up1.json()
    assert len(body1["uploaded"]) == 1
    assert body1["uploaded"][0]["filename"] == "sample.png"
    assert len(body1["skipped"]) == 0

    # 3. Upload same image without skip_duplicates (default)
    file2 = ("file", ("sample.png", _create_image_bytes("sample.png"), "image/png"))
    res_up2 = client.post(f"/api/projects/{project_id}/upload", files=[file2], headers=alice)
    assert res_up2.status_code == 200
    body2 = res_up2.json()
    assert len(body2["uploaded"]) == 1
    assert len(body2["skipped"]) == 0

    # Verify tasks list has 2 tasks
    tasks = client.get(f"/api/tasks?projectId={project_id}", headers=alice).json()
    assert len(tasks) == 2


def test_upload_skip_duplicates_parameter(client, alice):
    # 1. Create a project
    res = client.post("/api/projects", json={"name": "Dup Proj 2", "slug": "dup-proj-2", "creator": "ignored"}, headers=alice)
    assert res.status_code == 200
    project_id = res.json()["id"]

    # 2. Upload initial image
    file1 = ("file", ("unique1.png", _create_image_bytes("unique1.png"), "image/png"))
    res_up1 = client.post(f"/api/projects/{project_id}/upload", files=[file1], headers=alice)
    assert res_up1.status_code == 200

    # 3. Upload a batch with 1 existing duplicate, 1 new unique, and 1 batch-internal duplicate
    batch = [
        ("file", ("unique1.png", _create_image_bytes("unique1.png"), "image/png")),
        ("file", ("unique2.png", _create_image_bytes("unique2.png"), "image/png")),
        ("file", ("unique2.png", _create_image_bytes("unique2.png"), "image/png")),
    ]
    res_up2 = client.post(
        f"/api/projects/{project_id}/upload?skip_duplicates=true",
        files=batch,
        headers=alice,
    )
    assert res_up2.status_code == 200
    body2 = res_up2.json()

    # Should upload unique2 once, and skip unique1 (existing) and unique2 (second duplicate)
    assert len(body2["uploaded"]) == 1
    assert body2["uploaded"][0]["filename"] == "unique2.png"
    assert len(body2["skipped"]) == 2
    skipped_names = [s["filename"] for s in body2["skipped"]]
    assert "unique1.png" in skipped_names
    assert "unique2.png" in skipped_names

    # Verify database total tasks is exactly 2
    tasks = client.get(f"/api/tasks?projectId={project_id}", headers=alice).json()
    assert len(tasks) == 2
    assert {t["description"] for t in tasks} == {"unique1.png", "unique2.png"}
