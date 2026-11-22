from pathlib import Path

from PIL import Image

from backend.api import MAX_TEAMS
from backend.personas import INVESTORS, public_investor
from backend.views import moderator_state_view, screen_state_view, team_state_view

from .helpers import make_config, make_state


def test_portraits_are_small_web_assets_exposed_by_the_persona_payload() -> None:
    public_root = Path(__file__).resolve().parents[1] / "public"
    paths = []
    for investor in INVESTORS:
        payload = public_investor(investor)
        path = payload["image"]
        assert path.startswith("/investors/")
        asset = public_root / path.lstrip("/")
        assert asset.is_file()
        assert asset.stat().st_size < 100_000
        with Image.open(asset) as portrait:
            assert portrait.format == "WEBP"
            assert portrait.width == portrait.height == 512
        assert payload["avatar"] == "".join(
            part[0] for part in investor.name.removeprefix("Dr. ").split()
        )
        paths.append(path)
    assert len(set(paths)) == 4


def test_all_three_views_receive_configured_portrait_paths() -> None:
    state = make_state([])
    views = [
        team_state_view(state, None),
        screen_state_view(
            state,
            join_url="http://localhost/team",
            qr_data_url="qr",
            max_teams=MAX_TEAMS,
        ),
        moderator_state_view(state, make_config(), max_teams=MAX_TEAMS),
    ]
    for view in views:
        assert [item["image"] for item in view["investors"]] == [
            investor.image for investor in INVESTORS
        ]
