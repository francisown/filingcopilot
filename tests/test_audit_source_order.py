from filingcopilot.company_page import audit_source_order


def test_current_opinion_precedes_old_opinion_and_general_responsibility():
    filing={'period_end':'2025-12-31'}
    current={'text':'We have audited the financial statements for 2025. In our opinion they present fairly.'}
    old={'text':'In our opinion the 2024 financial statements present fairly.'}
    responsibility={'text':'Our responsibility is to express an opinion on the 2025 financial statements.'}
    assert audit_source_order(current,filing)>audit_source_order(old,filing)>audit_source_order(responsibility,filing)


def test_adverse_or_disclaimer_text_is_not_filtered_out():
    filing={'period_end':'2025-12-31'}
    disclaimer={'text':'We do not express an opinion on the 2025 financial statements.'}
    positive={'text':'In our opinion the 2025 financial statements present fairly.'}
    assert audit_source_order(disclaimer,filing)==audit_source_order(positive,filing)
