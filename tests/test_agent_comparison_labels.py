from scripts.export_agent_comparison import export, score, sentences


def fixture():
    rows=[{'id':'case','arm':'fc','answer':{'answer':[{'text':'Net income increased.','evidence_ids':['e1']}]}},
          {'id':'case','arm':'reference','answer':'Net income increased [e1].'}]
    manifest={'cases':[{'id':'case','question':'Why?','filing':{'id':'f'},'facts':[],
                       'passages':[{'id':'e1','text':'Net income increased.'}]}]}
    return export(rows,manifest)


def test_blank_labels_do_not_become_accuracy_from_id_binding():
    worksheet,key=fixture()
    report=score(worksheet,key)
    assert report['status']=='awaiting_human_sentence_labels'
    assert all(s['citation_support_rate'] is None for s in report['arms'].values())
    assert all(s['automatic_literal_id_binding']==1 for s in report['arms'].values())


def test_human_declaration_and_unchanged_complete_sample_are_required():
    worksheet,key=fixture()
    # Synthetic fixture labels exercise arithmetic, never fill a real worksheet.
    worksheet['human_reviewer']='test-fixture'
    for item in worksheet['items']:
        item['label']='support'
    assert score(worksheet,key)['status']=='awaiting_human_sentence_labels'
    worksheet['human_origin_attestation']=True
    assert score(worksheet,key)['arms']['fc']['citation_support_rate']==1
    response_id = next(iter(worksheet['responses']))
    original = worksheet['responses'][response_id]
    worksheet['responses'][response_id] = 'Changed reference context'
    assert score(worksheet,key)['status'] == 'invalid_worksheet'
    worksheet['responses'][response_id] = original
    worksheet['items'][0]['sentence']='Changed claim'
    assert score(worksheet,key)['status']=='invalid_worksheet'
    assert all(s['citation_support_rate'] is None for s in score(worksheet,key)['arms'].values())


def test_export_splits_claims_without_losing_citations_or_factual_tables():
    structured = {'answer': {'answer': [{'text': '收入增长。利润下降。', 'evidence_ids': ['e1']}]}}
    assert sentences(structured) == [('收入增长。', ['e1']), ('利润下降。', ['e1'])]
    prose = {'answer': '收入为12.38亿元。[e1] 利润下降。[e2]\n| FY2025 | 12.38 | [e1] |\n|---|---|---|'}
    result = sentences(prose)
    assert result[0] == ('收入为12.38亿元。[e1]', ['e1'])
    assert result[1] == ('利润下降。[e2]', ['e2'])
    assert len(result) == 3 and '12.38' in result[2][0]
    inherited = sentences({'answer':'收入增长。利润下降。[e1]'},context=True)
    assert inherited[0][1] == ['e1'] and inherited[0][3] == 'paragraph_context'
    assert inherited[0][2] == '收入增长。利润下降。[e1]'
    assert inherited[1][3] == 'explicit_sentence'


def test_partial_human_subset_is_separate_from_full_sample_acceptance():
    worksheet,key=fixture()
    worksheet['human_reviewer']='synthetic-test-only'
    worksheet['human_origin_attestation']=True
    worksheet['items'][0]['label']='support'
    report=score(worksheet,key)
    assert report['status']=='partially_human_scored'
    assert all(s['citation_support_rate'] is None for s in report['arms'].values())
    reviewed=[s for s in report['arms'].values() if s['reviewed_subset']['annotated_units']]
    assert len(reviewed)==1 and reviewed[0]['reviewed_subset']['citation_support_rate']==1
    assert 'biased' in reviewed[0]['reviewed_subset']['scope']
    worksheet['items'][1]['label']='unsupported'
    assert score(worksheet,key)['status']=='human_scored'


def test_partial_support_requires_attestation_integrity_and_known_labels():
    worksheet,key=fixture()
    worksheet['human_reviewer']='synthetic-test-only'
    worksheet['items'][0]['label']='support'
    report=score(worksheet,key)
    assert all(s['reviewed_subset']['citation_support_rate'] is None for s in report['arms'].values())
    assert all(s['human_labels']=={} for s in report['arms'].values())
    worksheet['human_origin_attestation']=True
    worksheet['items'][0]['label']='automatic_support'
    report=score(worksheet,key)
    assert report['status']=='invalid_worksheet'
    assert all(s['reviewed_subset']['citation_support_rate'] is None for s in report['arms'].values())
    worksheet['items'][0]['label']='support'
    worksheet['items'][0]['evidence'][0]['text']='Tampered source'
    assert score(worksheet,key)['status']=='invalid_worksheet'
