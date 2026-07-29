from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def test_nginx_accepts_the_api_upload_limit() -> None:
    nginx_config = (REPOSITORY_ROOT / "infra" / "nginx" / "default.conf").read_text(
        encoding="utf-8"
    )

    assert "client_max_body_size 20m;" in nginx_config
