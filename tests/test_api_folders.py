from tests.conftest import ALICE, BOB, OPS


def make_folder(client, name="Holidays", headers=ALICE):
    resp = client.post("/v1/folders", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def make_video(client, headers=ALICE, **overrides):
    body = {"filename": "holiday.mp4", "content_type": "video/mp4", "size_bytes": 11} | overrides
    return client.post("/v1/videos", json=body, headers=headers)


def listed(client, query="", headers=ALICE):
    return [v["id"] for v in client.get(f"/v1/videos{query}", headers=headers).json()["items"]]


def test_create_and_list_folders(client):
    folder = make_folder(client, "  Trips  ")
    assert folder["name"] == "Trips"
    assert folder["owner"] == "alice"
    assert folder["mine"] is True
    make_folder(client, "Birthdays")

    names = [f["name"] for f in client.get("/v1/folders", headers=ALICE).json()["items"]]
    assert names == ["Birthdays", "Trips"]


def test_folder_name_validation(client):
    assert client.post("/v1/folders", json={"name": "   "}, headers=ALICE).status_code == 422
    assert client.post("/v1/folders", json={"name": "x" * 101}, headers=ALICE).status_code == 422


def test_duplicate_folder_name_conflicts_per_owner(client):
    make_folder(client)
    resp = client.post("/v1/folders", json={"name": "Holidays"}, headers=ALICE)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "folder_exists"
    make_folder(client, headers=BOB)


def test_folders_are_isolated_between_owners(client):
    folder = make_folder(client)
    assert client.get("/v1/folders", headers=BOB).json()["items"] == []
    assert client.delete(f"/v1/folders/{folder['id']}", headers=BOB).status_code == 404

    as_admin = client.get("/v1/folders", headers=OPS).json()["items"]
    assert [(f["id"], f["mine"]) for f in as_admin] == [(folder["id"], False)]


def test_upload_into_existing_folder(client):
    folder = make_folder(client)
    filed = make_video(client, folder_id=folder["id"]).json()["video"]
    loose = make_video(client).json()["video"]

    assert filed["folder_id"] == folder["id"]
    assert loose["folder_id"] is None
    assert listed(client, f"?folder_id={folder['id']}") == [filed["id"]]
    assert listed(client, "?folder_id=none") == [loose["id"]]
    assert set(listed(client)) == {filed["id"], loose["id"]}


def test_upload_rejects_unusable_folder(client):
    assert make_video(client, folder_id="missing").status_code == 404
    bobs = make_folder(client, headers=BOB)
    assert make_video(client, folder_id=bobs["id"]).status_code == 404
    # An admin can see the folder but the new video would be theirs, not its owner's.
    resp = make_video(client, headers=OPS, folder_id=bobs["id"])
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "folder_owner_mismatch"
    assert listed(client, headers=OPS) == []


def test_move_video_between_folders(client):
    first, second = make_folder(client, "First"), make_folder(client, "Second")
    video_id = make_video(client, folder_id=first["id"]).json()["video"]["id"]

    moved = client.patch(f"/v1/videos/{video_id}", json={"folder_id": second["id"]}, headers=ALICE)
    assert moved.status_code == 200
    assert moved.json()["folder_id"] == second["id"]
    assert listed(client, f"?folder_id={first['id']}") == []

    out = client.patch(f"/v1/videos/{video_id}", json={"folder_id": None}, headers=ALICE)
    assert out.json()["folder_id"] is None
    assert client.patch(f"/v1/videos/{video_id}", json={}, headers=ALICE).status_code == 422


def test_move_checks_owners(client):
    video_id = make_video(client).json()["video"]["id"]
    bobs = make_folder(client, headers=BOB)
    move = {"folder_id": bobs["id"]}

    assert client.patch(f"/v1/videos/{video_id}", json=move, headers=ALICE).status_code == 404
    assert client.patch(f"/v1/videos/{video_id}", json=move, headers=BOB).status_code == 404
    assert client.patch(f"/v1/videos/{video_id}", json=move, headers=OPS).status_code == 422

    alices = make_folder(client)
    as_admin = client.patch(f"/v1/videos/{video_id}", json={"folder_id": alices["id"]}, headers=OPS)
    assert as_admin.json()["folder_id"] == alices["id"]


def test_delete_folder_keeps_its_videos(client):
    folder = make_folder(client)
    video_id = make_video(client, folder_id=folder["id"]).json()["video"]["id"]

    assert client.delete(f"/v1/folders/{folder['id']}", headers=ALICE).status_code == 204
    assert client.get("/v1/folders", headers=ALICE).json()["items"] == []
    video = client.get(f"/v1/videos/{video_id}", headers=ALICE).json()
    assert video["folder_id"] is None
