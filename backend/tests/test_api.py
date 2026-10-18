from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from conftest import SEG_END, SEG_START
from paintreel.api.app import create_app


@pytest.fixture(scope="module")
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def _wait(client: TestClient, job_id: str, timeout: float = 600) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.3)
    raise TimeoutError(job_id)


def _upload(client: TestClient, kind: str, path) -> str:
    with open(path, "rb") as f:
        r = client.post("/api/upload", data={"kind": kind}, files={"file": (path.name, f, "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()["path"]


def test_full_flow_through_api(client, synth_files):
    health = client.get("/api/health").json()
    assert health["ffmpeg"]["ok"]
    presets = client.get("/api/presets").json()
    assert [p["name"] for p in presets["presets"]] == ["energetic", "calm", "reveal", "smooth"]
    assert {c["key"] for c in presets["controls"]} == {"pace", "cuts", "transitions", "hold"}
    hold = next(c for c in presets["controls"] if c["key"] == "hold")
    assert hold["defaults"]["calm"] == 3.0

    # step 1: upload + analyze
    video = _upload(client, "video", synth_files["video"])
    song = _upload(client, "song", synth_files["song"])
    vids = client.get("/api/assets", params={"kind": "video"}).json()
    item = next(v for v in vids if v["path"] == video)
    assert item["uploaded"] and item["duration"] == pytest.approx(120, abs=0.5)
    pending = client.get("/api/video/analysis", params={"path": video})
    assert pending.status_code == 200  # null until analyzed (or the analysis if this file was seen before)
    job = client.post("/api/jobs/analyze", json={"video": video}).json()
    assert _wait(client, job["id"])["state"] == "done"
    ana = client.get("/api/video/analysis", params={"path": video}).json()
    assert ana["events"] >= 3 and len(ana["events_list"]) == ana["events"]
    assert len(ana["times"]) == len(ana["progress"]) == len(ana["visual_change"])
    thumb = client.get("/api/video/thumb", params={"path": video, "t": ana["final_time"]})
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    assert client.get("/api/video/frame", params={"path": video, "t": 10}).status_code == 200

    # step 2: the song
    ov = client.post("/api/songs/overview", json={"path": song}).json()
    assert ov["suggested_end"] > ov["suggested_start"] and ov["beats"]
    r = client.get("/api/media", params={"path": song}, headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100

    # step 3: plan preview with simple controls
    params = {"video": video, "song": song, "start": SEG_START, "end": SEG_END, "preset": "calm",
              "controls": {"pace": 0.7, "cuts": 0.6, "hold": 2.2}}
    plan = client.post("/api/plan", json=params).json()
    assert plan["storyboard"] and plan["storyboard"][-1]["style"] == "hold"
    assert plan["edl"]["segments"][-1]["out_end"] == pytest.approx(SEG_END - SEG_START, abs=0.02)

    # step 5: render
    job = client.post("/api/jobs/render", json=params).json()
    assert job["state"] == "queued"
    done = _wait(client, job["id"])
    assert done["state"] == "done", done["error"]
    assert {"reel", "silent", "edl", "cover", "mapping_plot"} <= set(done["outputs"])
    with client.stream("GET", f"/api/jobs/{job['id']}/events") as r:
        body = "".join(r.iter_text())
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert events and events[-1]["state"] == "done"
    url = f"/api/jobs/{job['id']}/files/{done['outputs']['reel']}"
    r = client.get(url, headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    assert client.get(f"/api/jobs/{job['id']}/files/cover.jpg").headers["content-type"] == "image/jpeg"
    edl = client.get(f"/api/jobs/{job['id']}/files/edl.json").json()
    assert edl["meta"]["preset"] == "calm" and edl["meta"]["controls"]["hold"] == 2.2

    # history
    hist = client.get("/api/renders").json()
    assert hist[0]["id"] == job["id"] and hist[0]["params"]["controls"]["pace"] == 0.7

    # re-render an edited EDL directly; a non-chronological one is rejected up front
    edl["segments"][-1]["zoom"] = None
    job2 = client.post("/api/jobs/render-edl", json={"edl": edl}).json()
    assert _wait(client, job2["id"])["state"] == "done"
    edl["segments"][2]["src_start"] = 0.0
    assert client.post("/api/jobs/render-edl", json={"edl": edl}).status_code == 422

    # delete a render and the uploaded files
    assert client.delete(f"/api/renders/{job2['id']}").status_code == 200
    assert client.get(f"/api/jobs/{job2['id']}").status_code == 404
    assert client.delete("/api/assets", params={"path": song}).status_code == 200
    assert all(s["path"] != song for s in client.get("/api/assets", params={"kind": "song"}).json())


def test_cancel_render(client, synth_files):
    params = {"video": str(synth_files["video"]), "song": str(synth_files["song"]), "start": SEG_START,
              "end": SEG_END, "preset": "energetic", "debug": True}
    job = client.post("/api/jobs/render", json=params).json()
    deadline = time.time() + 120
    while client.get(f"/api/jobs/{job['id']}").json()["state"] == "queued" and time.time() < deadline:
        time.sleep(0.05)
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
    final = _wait(client, job["id"])
    assert final["state"] == "cancelled", final
    assert client.delete(f"/api/renders/{job['id']}").status_code == 200


def test_api_rejects_bad_input(client, synth_files):
    song = str(synth_files["song"])
    video = str(synth_files["video"])
    assert client.post("/api/jobs/render", json={"video": "/nope.mp4", "song": song}).status_code == 400
    r = client.post("/api/jobs/render", json={"video": video, "song": song,
                                              "overrides": {"planner": {"speed": {"maxx": 3}}}})
    assert r.status_code == 422
    r = client.post("/api/jobs/render", json={"video": video, "song": song, "controls": {"warp": 1}})
    assert r.status_code == 422
    assert client.get("/api/media", params={"path": "/etc/hosts"}).status_code == 403
    assert client.get("/api/video/info", params={"path": "/etc/hosts"}).status_code == 403
    r = client.post("/api/upload", data={"kind": "video"}, files={"file": ("evil.sh", b"#!/bin/sh", "text/plain")})
    assert r.status_code == 415
    assert client.delete("/api/assets", params={"path": video}).status_code == 403  # not an upload
