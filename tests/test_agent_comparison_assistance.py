import csv
from copy import deepcopy

import pytest

from scripts.assist_agent_comparison import (
    FIELDS,
    assistance_rows,
    import_human_csv,
    mechanical_hints,
)
from scripts.export_agent_comparison import export, score


def example():
    manifest={'cases':[{'id':'case','question':'Which share basis?',
        'filing':{'id':'f','period_end':'2025-12-31','source_url':'https://example.org/report'},
        'facts':[],'passages':[{'id':'p_3456789012345','text':'FY2025, CNY 5.50 per ordinary share.'}]}]}
    rows=[{'id':'case','arm':'one','answer':'- FY2025, EPS为5.50人民币/普通股。[p_3456789012345]'},
          {'id':'case','arm':'two','answer':'The report states earnings per ordinary share. [p_3456789012345]'}]
    return export(rows,manifest)


def write_csv(path, rows):
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def test_literal_bilingual_hints_do_not_infer_currency_or_support():
    hints=mechanical_hints('截至2025-12-31，FY2025收入为12.38亿美元，普通股EPS为5.50人民币，ADS标价$6。[p_9382736]')
    assert hints['contains_number'] and '12.38' in hints['number_tokens'] and '5.50' in hints['number_tokens']
    assert '9382736' not in hints['number_tokens']
    assert '2025-12-31' in hints['period_tokens'] and 'FY2025' in hints['period_tokens']
    assert '人民币' in hints['currency_tokens'] and '美元' in hints['currency_tokens']
    assert '普通股' in hints['share_tokens'] and 'ADS' in hints['share_tokens']
    assert 'label' not in hints and 'not labels' in hints['scope']
    dollar=mechanical_hints('Price $6.00')
    assert dollar['currency_symbols']==['$'] and dollar['currency_tokens']==[]


def test_hint_csv_preserves_frozen_inputs_and_blank_labels():
    worksheet,key=example()
    original=deepcopy(worksheet)
    rows=assistance_rows(worksheet)
    assert worksheet==original and all(row['label']==row['note']=='' for row in rows)
    assert all(row['source_digest'] for row in rows)
    assert all(s['citation_support_rate'] is None for s in score(worksheet,key)['arms'].values())
    worksheet['items'][0]['sentence']='=HYPERLINK("https://example.org","claim")'
    assert assistance_rows(worksheet)[0]['sentence'].startswith("'=")


def test_blank_csv_cannot_create_human_provenance(tmp_path):
    worksheet,_=example()
    path=tmp_path/'blank.csv'
    write_csv(path,assistance_rows(worksheet))
    with pytest.raises(ValueError,match='No human labels'):
        import_human_csv(worksheet,path,'synthetic-test-only',True)
    assert worksheet['human_reviewer'] is None


def test_strict_human_import_allows_partial_and_does_not_modify_original(tmp_path):
    worksheet,key=example()
    original=deepcopy(worksheet)
    rows=assistance_rows(worksheet)
    rows[0]['label']='support'
    path=tmp_path/'fixture-human.csv'
    write_csv(path,rows)
    with pytest.raises(ValueError,match='real human'):
        import_human_csv(worksheet,path,'synthetic-test-only',False)
    imported=import_human_csv(worksheet,path,'synthetic-test-only',True)
    assert worksheet==original
    report=score(imported,key)
    assert report['integrity'] and report['status']=='partially_human_scored'
    assert sum(s['reviewed_subset']['annotated_units'] for s in report['arms'].values())==1
    assert all(s['citation_support_rate'] is None for s in report['arms'].values())
    with pytest.raises(ValueError,match='someone else'):
        import_human_csv(imported,path,'another-synthetic-reviewer',True)


@pytest.mark.parametrize('change', ['sentence','source_digest','duplicate','unknown_label'])
def test_import_rejects_changed_statements_duplicates_and_unknown_labels(tmp_path,change):
    worksheet,_=example()
    rows=assistance_rows(worksheet)
    rows[0]['label']='support'
    if change in ('sentence','source_digest'):
        rows[0][change]='changed'
    elif change=='duplicate':
        rows.append(deepcopy(rows[0]))
    else:
        rows[0]['label']='automatic_support'
    path=tmp_path/'bad.csv'
    write_csv(path,rows)
    with pytest.raises(ValueError):
        import_human_csv(worksheet,path,'synthetic-test-only',True)


def test_spreadsheet_roundtrip_and_helper_edits_never_determine_human_score(tmp_path):
    worksheet,key=example()
    rows=assistance_rows(worksheet)
    row=next(row for row in rows if row['sentence'].startswith("'-"))
    row['sentence']=row['sentence'][1:]  # Excel literal-text prefix removed.
    row['contains_number']='FALSE'  # Display hints are not scoring inputs.
    row['currency_tokens']='edited helper, not evidence'
    row['label']='support'
    path=tmp_path/'roundtrip.csv'
    write_csv(path,rows)
    imported=import_human_csv(worksheet,path,'synthetic-test-only',True)
    assert score(imported,key)['integrity']
    assert score(imported,key)['status']=='partially_human_scored'
