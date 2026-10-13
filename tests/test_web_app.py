from pathlib import Path

import web_app


def test_window_geometry_is_saved_and_loaded_atomically(monkeypatch, tmp_path: Path):
    target = tmp_path / "window.json"
    monkeypatch.setattr(web_app, "WINDOW_GEOMETRY_FILE", target)

    assert web_app.save_window_geometry({"x": -1200, "y": 40, "width": 1280, "height": 760})
    assert web_app.load_window_geometry() == {
        "x": -1200, "y": 40, "width": 1280, "height": 760,
    }
    assert not target.with_suffix(".json.tmp").exists()


def test_invalid_window_geometry_is_ignored(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "WINDOW_GEOMETRY_FILE", tmp_path / "window.json")

    assert not web_app.save_window_geometry({"x": 0, "y": 0, "width": 20, "height": 20})
    assert web_app.load_window_geometry() == {}


def test_chromium_app_uses_saved_window_geometry(monkeypatch):
    launched = []
    monkeypatch.setattr(web_app, "_candidate_paths", lambda _browser: ["chrome"])
    monkeypatch.setattr(
        web_app,
        "load_window_geometry",
        lambda: {"x": 30, "y": 50, "width": 1100, "height": 700},
    )
    monkeypatch.setattr(web_app, "_launch", lambda executable, args: launched.append((executable, args)) or True)

    assert web_app.open_web_app("http://127.0.0.1:48753/", "chrome")
    assert launched == [("chrome", [
        "--window-position=30,50",
        "--window-size=1100,700",
        "--app=http://127.0.0.1:48753/?shell=app",
    ])]
