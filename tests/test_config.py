from backend.config import load_config
from backend.jury.provider import create_jury_provider


def test_forces_demo_mode_when_no_api_key_is_present() -> None:
    config = load_config({})
    assert config.demo is True
    assert config.api_key is None
    assert create_jury_provider(config).name == "demo"


def test_supports_a_real_openai_compatible_endpoint_only_when_configured() -> None:
    config = load_config(
        {
            "OPENAI_API_KEY": "sk-test",
            "DEMO_MODE": "false",
            "OPENAI_MODEL": "deployment-name",
            "OPENAI_BASE_URL": "https://example.openai.azure.com/openai/v1/",
        }
    )
    assert config.demo is False
    assert config.model == "deployment-name"
    assert config.base_url == "https://example.openai.azure.com/openai/v1"
    assert create_jury_provider(config).name == "openai"


def test_keeps_demo_mode_on_when_the_flag_is_set_even_with_a_key() -> None:
    config = load_config({"OPENAI_API_KEY": "sk-test", "DEMO_MODE": "true"})
    assert config.demo is True
    assert create_jury_provider(config).name == "demo"


def test_clamps_fractional_jury_concurrency_to_one() -> None:
    assert load_config({"JURY_MAX_CONCURRENCY": "0.5"}).max_concurrency == 1
    assert load_config({"JURY_MAX_CONCURRENCY": "3.9"}).max_concurrency == 3


def test_lets_an_empty_temperature_omit_the_parameter_and_rejects_invalid_values() -> None:
    assert load_config({}).temperature == 0.4
    assert load_config({"JURY_TEMPERATURE": ""}).temperature is None
    assert load_config({"JURY_TEMPERATURE": "0.9"}).temperature == 0.9
    assert load_config({"JURY_TEMPERATURE": "hot"}).temperature == 0.4
    assert load_config({"JURY_TEMPERATURE": "9"}).temperature == 0.4


def test_join_short_url_parsing() -> None:
    from backend.config import load_config

    assert load_config({}).join_short_url is None
    assert load_config({"JOIN_SHORT_URL": "  https://short.example/join/ "}).join_short_url == (
        "https://short.example/join"
    )
    for bad in (
        "",
        "bit.ly/x",
        "ftp://bit.ly/x",
        "https://",
        "https://bit ly/x",
        "javascript:alert(1)",
    ):
        assert load_config({"JOIN_SHORT_URL": bad}).join_short_url is None, bad
