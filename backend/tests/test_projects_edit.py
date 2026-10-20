from test_workflow import as_user, box, save

CLASSES = [
    {"id": "louse", "name": "Piojo", "key": "l", "digit": "1", "color": "#3fb950"},
    {"id": "fly", "name": "Mosca", "digit": "2", "color": "#b388ff"},
    {"id": "not_louse", "name": "Ni piojo ni mosca", "key": "n", "color": "#f85149", "negative": True},
]


def test_create_project_makes_creator_admin(client, world):
    c = as_user("intruso")
    r = c.post("/api/projects", json={"name": "Moscas en lana", "description": "otro estudio"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["role"] == "admin" and p["slug"] == "moscas-en-lana" and p["config"]["description"] == "otro estudio"
    assert [x["id"] for x in p["config"]["classes"]] == ["louse", "fly", "not_louse"]
    assert [x["id"] for x in c.get("/api/projects").json()] == [p["id"]]  # solo ve el suyo
    assert c.post("/api/projects", json={"name": "moscas EN lana"}).status_code == 409
    assert c.post("/api/projects", json={"name": "  "}).status_code == 422
    assert c.post("/api/projects", json={"name": "Moscas en lana 2"}).json()["slug"] == "moscas-en-lana-2"


def test_update_name_description_requires_project_admin(client, world):
    pid = world["project"].id
    for who, code in (("ana", 403), ("rev", 403), ("lector", 403), ("intruso", 404)):
        assert as_user(who).patch(f"/api/projects/{pid}", json={"name": "X"}).status_code == code
    adm = as_user("adm")
    r = adm.patch(f"/api/projects/{pid}", json={"name": "Piojos en lana", "description": "  hola "})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Piojos en lana" and r.json()["config"]["description"] == "hola"
    assert r.json()["config"]["classes"][0]["id"] == "louse"  # no se tocaron las clases
    assert adm.patch(f"/api/projects/{pid}", json={"name": "otro"}).status_code == 409  # otro proyecto lo usa


def test_edit_classes_keeps_ids_and_protects_used_ones(client, world):
    pid, f = world["project"].id, world["frames"][0].id
    adm = as_user("adm")
    save(adm, f, [box(label="louse"), box((50, 30, 15, 15), label="fly")])
    renamed = [dict(c) for c in CLASSES]
    renamed[0]["name"] = "Piojo adulto"
    renamed.append({"name": "Ácaro rojo", "digit": "3", "color": "#ffaa00"})
    r = adm.patch(f"/api/projects/{pid}", json={"classes": renamed})
    assert r.status_code == 200, r.text
    got = r.json()["config"]["classes"]
    assert [c["id"] for c in got] == ["louse", "fly", "not_louse", "acaro_rojo"]  # id nuevo desde el nombre
    assert got[0]["name"] == "Piojo adulto"
    # quitar una clase con cajas se rechaza; una sin cajas se puede quitar
    assert (
        adm.patch(f"/api/projects/{pid}", json={"classes": [c for c in renamed if c.get("id") != "fly"]}).status_code
        == 409
    )
    assert adm.patch(f"/api/projects/{pid}", json={"classes": renamed[:3]}).status_code == 200


def test_edit_classes_validation(client, world):
    pid = world["project"].id
    adm = as_user("adm")
    dup = [CLASSES[0], {**CLASSES[1], "name": "piojo"}]
    assert adm.patch(f"/api/projects/{pid}", json={"classes": dup}).status_code == 422  # nombre repetido
    same_key = [CLASSES[0], {**CLASSES[1], "digit": "1"}]
    assert adm.patch(f"/api/projects/{pid}", json={"classes": same_key}).status_code == 422  # atajo repetido
    assert adm.patch(f"/api/projects/{pid}", json={"classes": [CLASSES[2]]}).status_code == 422  # solo negativas
    assert adm.patch(f"/api/projects/{pid}", json={"classes": [{**CLASSES[0], "color": "verde"}]}).status_code == 422
    assert adm.patch(f"/api/projects/{pid}", json={"classes": [{**CLASSES[0], "id": "zzz"}]}).status_code == 422


def test_project_list_has_cover(client, world):
    p = as_user("ana").get("/api/projects").json()[0]
    assert p["cover_frame_id"] == world["frames"][0].id
