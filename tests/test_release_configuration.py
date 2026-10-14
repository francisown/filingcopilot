from pathlib import Path

from filingcopilot.settings import Settings


def test_release_configuration_requires_distinct_review_model_without_warning(tmp_path):
    settings = Settings(
        home=Path(tmp_path),
        api_base="https://provider.invalid/v1",
        api_key="placeholder",
        model="primary",
        review_model="independent-review",
        sec_user_agent="researcher@example.invalid",
        embedding_mode="off",
    )
    assert settings.llm_ready
    assert settings.review_configuration_warnings == ()


def test_same_review_model_is_explicitly_warned(tmp_path):
    settings = Settings(home=Path(tmp_path), model="same", review_model="same", embedding_mode="off")
    assert settings.review_configuration_warnings
