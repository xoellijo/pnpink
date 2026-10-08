from pathlib import Path

import prefs


def test_user_preferences_override_bundled_defaults(monkeypatch, tmp_path: Path):
    bundled = tmp_path / "bundled.ini"
    user = tmp_path / "user" / "preferences.ini"
    bundled.write_text(
        "[prefs]\n"
        "gdrive_api_key = public-key\n"
        "deckmaker_web_browser = system\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(prefs, "_DEFAULT_INI_PATH", bundled)
    monkeypatch.setattr(prefs, "_INI_PATH", user)
    prefs.reload()

    assert prefs.get_gdrive_api_key() == "public-key"
    assert prefs.get_deckmaker_web_browser() == "system"

    prefs.set_deckmaker_web_browser("chrome")
    prefs.reload()

    assert prefs.get_gdrive_api_key() == "public-key"
    assert prefs.get_deckmaker_web_browser() == "chrome"
    assert "gdrive_api_key" not in user.read_text(encoding="utf-8")
    assert "deckmaker_web_browser = chrome" in user.read_text(encoding="utf-8")
