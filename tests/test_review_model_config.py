from filingcopilot.settings import Settings


def test_missing_review_model_is_explicitly_warned_without_hiding_legacy_fallback(tmp_path):
    settings = Settings(home=tmp_path, model="research")
    warnings = settings.review_configuration_warnings
    assert warnings and "FC_REVIEW_MODEL" in warnings[0]


def test_same_review_model_is_warned_as_non_independent(tmp_path):
    settings = Settings(home=tmp_path, model="same", review_model="same")
    warnings = settings.review_configuration_warnings
    assert warnings and "相同" in warnings[0]


def test_distinct_review_model_has_no_independence_warning(tmp_path):
    settings = Settings(home=tmp_path, model="research", review_model="review")
    assert settings.review_configuration_warnings == ()
