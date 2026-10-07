from pathlib import Path

import dataset
import dataset_state
from deckmaker_types import AppRequest
from deckmaker_service import DeckMakerService


def test_google_sheet_uses_the_classic_automatic_mode(tmp_path: Path):
    template = tmp_path / "cards.svg"
    template.write_text("<svg/>", encoding="utf-8")
    service = object.__new__(DeckMakerService)

    assert service._detect_source_mode(str(template), "sheet-id", "auto") == "auto"
    assert service._detect_source_mode(str(template), "sheet-id", "oauth") == "oauth"
    assert service._detect_source_mode(str(template), "sheet-id", "public") == "public"


def test_local_csv_remains_the_default_without_sheet(tmp_path: Path):
    template = tmp_path / "cards.svg"
    template.write_text("<svg/>", encoding="utf-8")
    template.with_suffix(".csv").write_text("id\n1\n", encoding="utf-8")
    service = object.__new__(DeckMakerService)

    assert service._detect_source_mode(str(template), "", "") == "local_csv"


def test_auto_google_access_checks_public_before_oauth(monkeypatch):
    calls = []
    monkeypatch.setattr(dataset, "_fetch_gsheet_matrix_public", lambda *_args: calls.append("public") or None)
    monkeypatch.setattr(dataset, "_fetch_gsheet_matrix_oauth", lambda *_args: calls.append("oauth") or [["ok"]])

    matrix, mode = dataset._fetch_gsheet_matrix(object(), "sheet-id", "0", None, access_hint="")

    assert matrix == [["ok"]]
    assert mode == "oauth"
    assert calls == ["public", "oauth"]


def test_public_google_access_uses_requested_gid(monkeypatch):
    requested = []

    def fetch_text(url, **_kwargs):
        requested.append(url)
        return "name,value\nother-tab,42\n", {}, 200

    monkeypatch.setattr(dataset.NET, "fetch_text", fetch_text)

    matrix = dataset._fetch_gsheet_matrix_public(object(), "sheet-id", "305887467")

    assert matrix[1] == ["other-tab", "42"]
    assert requested == [
        "https://docs.google.com/spreadsheets/d/sheet-id/export?format=csv&gid=305887467"
    ]


def test_public_google_access_falls_back_to_gviz_with_same_gid(monkeypatch):
    requested = []

    def fetch_text(url, **_kwargs):
        requested.append(url)
        if "/export?" in url:
            raise RuntimeError("403")
        return "name,value\nother-tab,42\n", {}, 200

    monkeypatch.setattr(dataset.NET, "fetch_text", fetch_text)

    matrix = dataset._fetch_gsheet_matrix_public(object(), "sheet-id", "305887467")

    assert matrix[1] == ["other-tab", "42"]
    assert requested == [
        "https://docs.google.com/spreadsheets/d/sheet-id/export?format=csv&gid=305887467",
        "https://docs.google.com/spreadsheets/d/sheet-id/gviz/tq?tqx=out:csv&gid=305887467",
    ]


def test_oauth_google_access_ignores_gid_and_uses_template_name(monkeypatch):
    class Effect:
        def _document_path_or_abort(self):
            return "/tmp/Other tab.svg"

    monkeypatch.setattr(dataset._gs, "list_sheet_titles", lambda sheet_id: ["Hoja 1", "Other tab"])

    assert dataset._choose_sheet_and_range(Effect(), "sheet-id", "305887467") == "Other tab"


def test_embed_gid_uses_numeric_selector_without_waiting_for_oauth():
    request = AppRequest(template="cards.svg", sheet_id="sheet-id", sheet_range="305887467", dataset_source_mode="auto")

    assert DeckMakerService._initial_sheet_gid(request) == "305887467"


def test_embed_gid_matches_named_sheet_or_template_name():
    sheets = [
        {"title": "Cards", "sheetId": 17},
        {"title": "Other", "sheetId": 23},
    ]

    named = AppRequest(template="cards.svg", sheet_id="sheet-id", sheet_range="Other!A1:Z99", dataset_source_mode="oauth")
    automatic = AppRequest(template="cards.svg", sheet_id="sheet-id", sheet_range="", dataset_source_mode="oauth")

    assert DeckMakerService._select_sheet_gid(named, sheets) == "23"
    assert DeckMakerService._select_sheet_gid(automatic, sheets) == "17"


def test_resolved_embed_gid_is_persisted_only_for_matching_sheet(monkeypatch, tmp_path: Path):
    state_file = tmp_path / "dataset_state.json"
    template = tmp_path / "cards.svg"
    template.write_text("<svg/>", encoding="utf-8")
    monkeypatch.setattr(dataset_state, "STATE_FILE", str(state_file))
    monkeypatch.setattr(dataset_state, "_LEGACY_STATE_FILES", [])

    dataset_state.set_gsheet_for_svg(str(template), "sheet-a", "Cards", "oauth")
    dataset_state.set_gsheet_gid_for_svg(str(template), "sheet-a", "Cards", "42")
    assert dataset_state.get_gsheet_for_svg(str(template))["sheet_gid"] == "42"

    dataset_state.set_gsheet_for_svg(str(template), "sheet-a", "Other", "oauth")
    assert dataset_state.get_gsheet_for_svg(str(template))["sheet_gid"] == ""
