import pytest
from core.technology import normalize_ep_technology, resolve_cell_family, task_family, technology_conflict, annotate_rows, build_family_index

@pytest.mark.parametrize('raw,family', [(' LTE ', '4G'), (' nr ', '5G'), ('5G_NRDUCELL','5G'), ('NR Cell','5G'), ('gsm',None), ('',None), (None,None)])
def test_normalization(raw, family):
    assert normalize_ep_technology(raw) == family


def test_ep_precedence_and_controlled_legacy():
    cell = {'id':'5G-X', 'tech':'LTE','frequency':'3500','ep':{'technology':'NR'}}
    assert resolve_cell_family(cell, '5G_NRCELL').family == '4G'
    assert resolve_cell_family(cell).source == 'ep'
    assert technology_conflict(cell, '5G_NRDUCELL')
    assert resolve_cell_family({'id':'LTE-X','ep':{'technology':'NR'}}).family == '5G'
    assert resolve_cell_family({'id':'LTE-X','frequency':'1800'},'5G_NRDUCELL').source == 'measurement'
    assert resolve_cell_family('LTE-X').source == 'legacy_id'
    assert resolve_cell_family({'frequency':'3500'}).source == 'legacy_frequency'
    assert resolve_cell_family('LTE-X',allow_legacy=False).family is None
    assert task_family('5G_NRCELL') == task_family('5G_NRDUCELL') == '5G'


def test_batch_uses_site_and_cell_without_mutating_measurement():
    config = {'sites':[{'id':'A','cells':[{'id':'C','tech':'4G'}]}, {'id':'B','cells':[{'id':'C','tech':'5G'}]}]}
    rows = [{'site_id':sid,'cell_id':'C','technology':'5G_NRCELL'} for sid in ['A','B']]
    result = annotate_rows(rows, build_family_index(config))
    assert [r['family'] for r in result] == ['4G','5G']
    assert all(r['technology']=='5G_NRCELL' for r in result)
    assert all('family' not in r for r in rows)
