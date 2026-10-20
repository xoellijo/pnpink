import xml.etree.ElementTree as ET
from unittest.mock import Mock, PropertyMock, patch

import inkscape_previewer


class _Process:
    def __init__(self, returncode):
        self.returncode = returncode

    def poll(self):
        return self.returncode


def test_viewer_profile_hides_canvas_chrome():
    root = ET.fromstring(inkscape_previewer._PROFILE_XML)
    window = root.find("./group[@id='window']")
    states = {item.get("id"): item.get("state") for item in window.findall("group")}

    assert all(value == "0" for value in states.values())
    assert root.find("./group[@id='options']/group[@id='autosave']").get("enable") == "0"
    boot = root.find("./group[@id='options']/group[@id='boot']")
    assert boot.get("showsplash") == "0"
    assert boot.get("showwelcome") == "0"


def test_profile_update_preserves_saved_window_geometry():
    root = ET.fromstring(inkscape_previewer._PROFILE_XML)
    geometry = root.find("./group[@id='geometry']")
    geometry.attrib.update({"x": "123", "y": "45", "width": "987", "height": "654", "maximized": "0"})

    inkscape_previewer._configure_profile(root)

    assert geometry.attrib == {
        "id": "geometry",
        "x": "123",
        "y": "45",
        "width": "987",
        "height": "654",
        "maximized": "0",
        "fullscreen": "0",
    }
    for section_id in inkscape_previewer._WINDOW_SECTIONS:
        section = root.find(f"./group[@id='{section_id}']")
        assert section.find("./group[@id='panels']").get("state") == "0"


def test_reload_preserves_the_existing_preview_window():
    assert inkscape_previewer._RELOAD_ACTION == "file-rebase:true"


def test_windows_running_uses_real_window_not_gui_launcher_process():
    previewer = inkscape_previewer.InkscapePreviewer()
    previewer._gui_process = _Process(0)
    previewer._shell_process = _Process(None)
    with patch.object(inkscape_previewer.os, "name", "nt"), patch.object(
        previewer, "_window_handle", return_value=123
    ):
        assert previewer.running


def test_window_geometry_parser_accepts_inkscape_output():
    assert inkscape_previewer._parse_geometry("window-query-geometry\nx:120\ny:-7\nw:960\nh:680\n> ") == {
        "x": 120,
        "y": -7,
        "w": 960,
        "h": 680,
    }


def test_preview_namedview_is_centered_without_changing_geometry(tmp_path):
    svg = tmp_path / "preview.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" '
        'width="210mm" height="297mm" viewBox="0 0 210 297">'
        '<sodipodi:namedview inkscape:zoom="0.5" inkscape:cx="12" inkscape:cy="34"/>'
        '<g id="card" transform="translate(75.5 104.5)" '
        'data-pnpink-cut-bbox="75.5 104.5 59 88"><rect width="59" height="88"/></g>'
        '</svg>',
        encoding="utf-8",
    )

    assert inkscape_previewer._center_initial_namedview(svg, (1100, 720))

    root = ET.parse(svg).getroot()
    namedview = root.find(f"{{{inkscape_previewer._SODIPODI_NS}}}namedview")
    card = root.find("{http://www.w3.org/2000/svg}g")
    assert root.get("viewBox") == "0 0 210 297"
    assert root.get("width") == "210mm"
    assert root.get("height") == "297mm"
    assert card.get("transform") == "translate(75.5 104.5)"
    assert float(namedview.get(f"{{{inkscape_previewer._INKSCAPE_NS}}}zoom")) > 1.0
    assert abs(float(namedview.get(f"{{{inkscape_previewer._INKSCAPE_NS}}}cx")) - 210 * 96 / 25.4 / 2) < 1e-5
    assert abs(float(namedview.get(f"{{{inkscape_previewer._INKSCAPE_NS}}}cy")) - 297 * 96 / 25.4 / 2) < 1e-5


def test_windows_geometry_monitor_does_not_use_inkscape_shell(tmp_path):
    previewer = inkscape_previewer.InkscapePreviewer()
    previewer._geometry_path = tmp_path / "geometry.json"
    previewer._action = Mock(side_effect=AssertionError("shell must not be used"))
    geometry = {"x": 12, "y": 34, "w": 900, "h": 700, "maximized": False}

    with patch.object(inkscape_previewer.os, "name", "nt"), patch.object(
        previewer, "_native_window_geometry", return_value=geometry,
    ):
        previewer._remember_window_geometry()

    assert previewer._action.call_count == 0
    assert previewer._load_window_geometry() == geometry


def test_windows_geometry_restore_does_not_use_inkscape_shell():
    previewer = inkscape_previewer.InkscapePreviewer()
    previewer._action = Mock(side_effect=AssertionError("shell must not be used"))
    geometry = {"x": 12, "y": 34, "w": 900, "h": 700, "maximized": True}

    with patch.object(inkscape_previewer.os, "name", "nt"), patch.object(
        previewer, "_load_window_geometry", return_value=geometry,
    ), patch.object(previewer, "_apply_native_window_geometry") as apply_geometry:
        previewer._restore_window_geometry()

    apply_geometry.assert_called_once_with(geometry)
    assert previewer._action.call_count == 0


def test_first_preview_uses_visible_default_window_geometry():
    previewer = inkscape_previewer.InkscapePreviewer()
    previewer._action = Mock(side_effect=AssertionError("shell must not be used"))

    with patch.object(inkscape_previewer.os, "name", "nt"), patch.object(
        previewer, "_load_window_geometry", return_value={},
    ), patch.object(previewer, "_apply_native_window_geometry") as apply_geometry:
        previewer._restore_window_geometry()

    apply_geometry.assert_called_once_with(inkscape_previewer._DEFAULT_WINDOW_GEOMETRY)
    assert previewer._action.call_count == 0


def test_preview_source_fields_are_inherited_by_selected_children(tmp_path):
    svg = tmp_path / "preview.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<g id="inserted" data-field="Cost"><path id="visible" data-origid="source-path"/></g>'
        '</svg>',
        encoding="utf-8",
    )
    previewer = inkscape_previewer.InkscapePreviewer()

    previewer._index_fields(svg)

    assert previewer._field_to_ids["Cost"] == ["inserted"]
    assert previewer._id_to_fields["visible"] == ["source-path", "Cost"]


def test_preview_selection_ignores_hidden_template_nodes(tmp_path):
    svg = tmp_path / "preview.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<g id="template" style="display:none"><text id="template-title" data-field="Title"/></g>'
        '<g id="pnpink-output"><g id="card"><text id="card-title" data-field="Title"/></g></g>'
        '</svg>',
        encoding="utf-8",
    )
    previewer = inkscape_previewer.InkscapePreviewer()

    previewer._index_fields(svg)

    assert previewer._field_to_ids["Title"] == ["card-title"]
    assert "template-title" not in previewer._id_to_fields


def test_reload_and_dataset_selection_are_one_inkscape_action(tmp_path):
    svg = tmp_path / "preview.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<g id="card-title" data-field="Title"><text>Example</text></g>'
        '</svg>',
        encoding="utf-8",
    )
    previewer = inkscape_previewer.InkscapePreviewer()
    previewer._runtime_dir = tmp_path / "runtime"
    previewer._live_path = previewer._runtime_dir / "live.svg"

    with patch.object(
        inkscape_previewer.InkscapePreviewer, "running", new_callable=PropertyMock,
        return_value=True,
    ), patch.object(previewer, "_action", return_value="> ") as action:
        previewer.show(str(svg), selected_fields=["Title"])

    action.assert_called_once_with(
        "file-rebase:true;select-clear;select-by-id:card-title", timeout=12.0,
    )


def test_reload_does_not_recenter_existing_preview(tmp_path):
    svg = tmp_path / "preview.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<rect width="100" height="100"/>'
        '</svg>',
        encoding="utf-8",
    )
    previewer = inkscape_previewer.InkscapePreviewer()
    previewer._runtime_dir = tmp_path / "runtime"
    previewer._live_path = previewer._runtime_dir / "live.svg"

    with patch.object(
        inkscape_previewer.InkscapePreviewer, "running", new_callable=PropertyMock,
        return_value=True,
    ), patch.object(previewer, "_action", return_value="> "), patch.object(
        inkscape_previewer, "_center_initial_namedview",
    ) as center:
        previewer.show(str(svg))

    center.assert_not_called()
    assert previewer._live_path.read_bytes() == svg.read_bytes()
