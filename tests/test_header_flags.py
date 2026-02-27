# -*- coding: utf-8 -*-

import header_flags as HF


def test_parse_template_header_cell_basic():
    assert HF.parse_template_header_cell('{t=temp_1}') == {'bbox_id': 'temp_1', 'mods': set()}
    assert HF.parse_template_header_cell('{template_bbox=temp_2 @back}') == {'bbox_id': 'temp_2', 'mods': {'@back'}}
    assert HF.parse_template_header_cell('{label_id @page}') == {'bbox_id': 'label_id', 'mods': {'@page'}}
    assert HF.parse_template_header_cell('{label_id @page @back}') == {'bbox_id': 'label_id', 'mods': {'@page', '@back'}}
    assert HF.parse_template_header_cell('not_a_decl') is None


def test_extract_template_columns_and_normalization():
    headers = ['a', '{temp_2 @back}', 'b', '{label_1 @page}', '{temp_3}', 'c']
    norm, cols = HF.extract_template_columns(headers)

    assert norm[1].startswith('__dm_tcol__temp_2')
    assert norm[3].startswith('__dm_tcol__label_1')
    assert norm[4].startswith('__dm_tcol__temp_3')

    by_bbox = {c['bbox_id']: c for c in cols}
    assert by_bbox['temp_2']['mods'] == ['@back']
    assert by_bbox['label_1']['mods'] == ['@page']
    assert by_bbox['temp_3']['mods'] == []
