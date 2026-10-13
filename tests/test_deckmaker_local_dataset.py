import threading
from types import SimpleNamespace

import pytest
from lxml import etree

import dataset as DATASET
from deckmaker_service import DeckMakerService
from deckmaker_types import AppRequest


def service_for(request: AppRequest) -> DeckMakerService:
    service = object.__new__(DeckMakerService)
    service._lock = threading.RLock()
    service._request = request
    service._emit = lambda *_args, **_kwargs: None
    service._preview_monitor_wake = threading.Event()
    service._preview_dataset_check_requested = False
    service._local_dataset_revision = ""
    return service


def test_local_dataset_path_only_exposes_active_adjacent_csv(tmp_path):
    template = tmp_path / "cards.svg"
    dataset = template.with_suffix(".csv")
    template.write_text("<svg/>", encoding="utf-8")
    dataset.write_text("name\nCard\n", encoding="utf-8")
    service = service_for(AppRequest(template=str(template), dataset_source_mode="local_csv"))

    assert service.local_dataset_path() == str(dataset)


def test_create_starter_csv_uses_template_bbox_and_fields(tmp_path):
    template = tmp_path / "cards.svg"
    template.write_text("<svg/>", encoding="utf-8")
    root = etree.fromstring(b"""<svg xmlns='http://www.w3.org/2000/svg'
        xmlns:inkscape='http://www.inkscape.org/namespaces/inkscape'>
        <g id='card'><rect id='frame' inkscape:label='bbox card'/>
        <text id='text1' inkscape:label='title'/><rect id='art'/></g></svg>""")
    service = service_for(AppRequest(template=str(template)))
    service._preview_effect = lambda _request: SimpleNamespace(
        document=SimpleNamespace(getroot=lambda: root),
    )
    service.update = lambda _values: None
    service._set_status = lambda _value: None
    service.preview = lambda **_kwargs: True

    assert service.create_starter_csv()

    content = template.with_suffix(".csv").read_text(encoding="utf-8")
    assert "## PnPInk starter dataset" in content
    assert "frame,title,art" in content
    assert content.rstrip().endswith("{},,")


def test_local_dataset_table_uses_spreadsheet_coordinates_and_source_rows(tmp_path):
    template = tmp_path / "cards.svg"
    dataset = template.with_suffix(".csv")
    dataset.write_text('name,text\nCard,"Hello, world"\nSecond,Value\n', encoding="utf-8")
    service = service_for(AppRequest(template=str(template), dataset_source_mode="local_csv"))

    table = service.local_dataset_table()

    assert [column["title"] for column in table["columns"][:3]] == ["A", "B", "C"]
    assert len(table["columns"]) == 26
    assert len(table["rows"]) == 100
    assert table["rows"][0]["__csv_row__"] == 1
    assert table["rows"][0]["__kind__"] == "dataset-header"
    assert table["rows"][0]["column_0"] == "name"
    assert table["rows"][1]["column_1"] == "Hello, world"


def test_local_dataset_table_classifies_directives_comments_and_markers(tmp_path):
    template = tmp_path / "cards.svg"
    dataset = template.with_suffix(".csv")
    dataset.write_text(
        '# ordinary note\n# :icon(name) = @{icon://${name}}\n## disabled\n'
        '{{t=card}},name\n,data\n#### hidden\nignored\n####\n',
        encoding="utf-8",
    )
    service = service_for(AppRequest(template=str(template), dataset_source_mode="local_csv"))

    kinds = [row["__kind__"] for row in service.local_dataset_table()["rows"][:8]]

    assert kinds == [
        "comment", "directive", "comment", "dataset-header", "data",
        "comment-marker", "comment-block", "comment-marker",
    ]


def test_local_dataset_table_recognizes_supported_definition_rows(tmp_path):
    template = tmp_path / "cards.svg"
    dataset = template.with_suffix(".csv")
    dataset.write_text(
        "# :icon(name) = @{icon://${name}}\n"
        "# @cards = @{cards.png}.Layout{3x2}\n"
        "# accent=G{fff^20 [0 0 0]}\n"
        "# _DM_reset_to = 0\n",
        encoding="utf-8",
    )
    service = service_for(AppRequest(template=str(template), dataset_source_mode="local_csv"))

    kinds = [row["__kind__"] for row in service.local_dataset_table()["rows"][:4]]

    assert kinds == ["directive", "directive", "directive", "directive"]


def test_local_dataset_table_marks_disabled_columns_until_next_dataset(tmp_path):
    template = tmp_path / "cards.svg"
    dataset = template.with_suffix(".csv")
    dataset.write_text(
        "{{t=card}},name,##notes,###disabled,also-disabled\n"
        ",Card,Note,No,No\n"
        "{{t=token}},name,value\n"
        ",Token,1\n",
        encoding="utf-8",
    )
    service = service_for(AppRequest(template=str(template), dataset_source_mode="local_csv"))

    rows = service.local_dataset_table()["rows"]

    assert rows[0]["__commented_columns__"] == [2, 3, 4]
    assert rows[1]["__commented_columns__"] == [2, 3, 4]
    assert rows[2]["__commented_columns__"] == []
    assert rows[3]["__commented_columns__"] == []


def test_local_dataset_cell_edit_is_atomic_and_revision_checked(tmp_path):
    template = tmp_path / "cards.svg"
    dataset = template.with_suffix(".csv")
    dataset.write_text('name,text\nCard,"Hello, world"\n', encoding="utf-8")
    service = service_for(AppRequest(template=str(template), dataset_source_mode="local_csv"))
    revision = service.local_dataset_table()["revision"]

    result = service.update_local_dataset_cells([
        {"row": 2, "column": 1, "value": "Changed, safely"}
    ], revision)

    assert DATASET.read_csv_matrix(str(dataset))[1][1] == "Changed, safely"
    assert result["revision"] != revision
    with pytest.raises(RuntimeError):
        service.update_local_dataset_cells([
            {"row": 2, "column": 1, "value": "Stale"}
        ], revision)


def test_local_dataset_cell_edits_are_written_as_one_batch(tmp_path):
    template = tmp_path / "cards.svg"
    dataset = template.with_suffix(".csv")
    dataset.write_text("name,value\nCard,1\n", encoding="utf-8")
    service = service_for(AppRequest(template=str(template), dataset_source_mode="local_csv"))
    revision = service.local_dataset_table()["revision"]

    result = service.update_local_dataset_cells([
        {"row": 2, "column": 0, "value": "Changed"},
        {"row": 2, "column": 1, "value": "0"},
    ], revision)

    assert DATASET.read_csv_matrix(str(dataset))[1] == ["Changed", "0"]
    assert result["revision"] != revision
    assert service._preview_dataset_check_requested


def test_google_sheet_csv_replacement_keeps_one_emergency_backup(tmp_path):
    dataset = tmp_path / "cards.csv"
    dataset.write_text("name,value\nOld,1\n", encoding="utf-8")

    backup = DeckMakerService._replace_csv_from_google(
        str(dataset), DATASET.csv_matrix_bytes([["name", "value"], ["New", "2"]])
    )

    assert DATASET.read_csv_matrix(str(dataset)) == [["name", "value"], ["New", "2"]]
    assert DATASET.read_csv_matrix(backup) == [["name", "value"], ["Old", "1"]]

    DeckMakerService._replace_csv_from_google(
        str(dataset), DATASET.csv_matrix_bytes([["name", "value"], ["Newest", "3"]])
    )
    assert DATASET.read_csv_matrix(str(dataset) + ".old") == [
        ["name", "value"], ["New", "2"]
    ]


def test_google_sheet_csv_replacement_creates_missing_csv_without_backup(tmp_path):
    dataset = tmp_path / "cards.csv"

    backup = DeckMakerService._replace_csv_from_google(
        str(dataset), DATASET.csv_matrix_bytes([["name"], ["Created"]])
    )

    assert backup == ""
    assert DATASET.read_csv_matrix(str(dataset)) == [["name"], ["Created"]]
    assert not (tmp_path / "cards.csv.old").exists()


def test_external_local_dataset_change_requests_preview_check(tmp_path):
    template = tmp_path / "cards.svg"
    dataset = template.with_suffix(".csv")
    dataset.write_text("name,value\nCard,1\n", encoding="utf-8")
    service = service_for(AppRequest(template=str(template), dataset_source_mode="local_csv"))
    service.local_dataset_table()

    dataset.write_text("name,value\nCard,2\n", encoding="utf-8")
    service.local_dataset_revision()

    assert service._preview_dataset_check_requested
    assert service._preview_monitor_wake.is_set()


def test_saved_template_requests_preview_refresh(tmp_path):
    template = tmp_path / "cards.svg"
    template.write_text("<svg/>", encoding="utf-8")
    service = service_for(AppRequest(template=str(template), dataset_source_mode="local_csv"))
    service._preview_monitor_stop = threading.Event()
    service._preview_datasets = [{}]
    service._preview_asset_key = "asset-1"
    service._preview_template_revision = service._file_revision(str(template))
    service._sheet_editor_active_until = 0.0
    service._sheet_editor_final_poll = False
    service._busy = False
    service._preview_monitor_wake.set()
    refreshed = []

    template.write_text("<svg><g/></svg>", encoding="utf-8")

    def refresh():
        refreshed.append(True)
        service._preview_monitor_stop.set()
        return True

    service.refresh_previews = refresh
    service._preview_monitor()

    assert refreshed == [True]


def test_google_source_does_not_expose_adjacent_csv(tmp_path):
    template = tmp_path / "cards.svg"
    template.with_suffix(".csv").write_text("name\nCard\n", encoding="utf-8")
    service = service_for(AppRequest(
        template=str(template),
        sheet_id="sheet-id",
        dataset_source_mode="oauth",
    ))

    with pytest.raises(FileNotFoundError):
        service.local_dataset_path()


def test_selecting_another_template_starts_its_initial_preview(tmp_path):
    current = tmp_path / "current.svg"
    target = tmp_path / "target.svg"
    current.write_text("<svg/>", encoding="utf-8")
    target.write_text("<svg/>", encoding="utf-8")
    service = object.__new__(DeckMakerService)
    service._lock = threading.RLock()
    service._busy = False
    service._request = AppRequest(template=str(current), log_level="INFO")
    service._templates = [{"path": str(target)}]
    calls = []
    service.open_request = lambda request: calls.append(("open", request))
    service.preview = lambda **options: calls.append(("preview", options)) or True
    service.state = lambda: {"template": str(target)}

    state = service.select_template(str(target))

    assert state == {"template": str(target)}
    assert calls[0][0] == "open"
    assert calls[0][1].template == str(target)
    assert calls[1] == ("preview", {"force": False, "refresh": True})


def test_local_dataset_rows_and_columns_can_be_inserted_and_deleted(tmp_path):
    template = tmp_path / "cards.svg"
    dataset = template.with_suffix(".csv")
    dataset.write_text("name,value\nCard,1\n", encoding="utf-8")
    service = service_for(AppRequest(template=str(template), dataset_source_mode="local_csv"))
    revision = service.local_dataset_table()["revision"]

    result = service.update_local_dataset_structure("insert-row-after", 1, revision)
    result = service.update_local_dataset_structure(
        "insert-column-before", 1, result["revision"]
    )
    assert DATASET.read_csv_matrix(str(dataset)) == [
        ["name", "", "value"], ["", ""], ["Card", "", "1"]
    ]

    result = service.update_local_dataset_structure(
        "delete-column", 1, result["revision"]
    )
    service.update_local_dataset_structure("delete-row", 2, result["revision"])
    assert DATASET.read_csv_matrix(str(dataset)) == [["name", "value"], ["Card", "1"]]
