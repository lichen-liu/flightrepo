import re
from urllib.parse import urljoin

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.web_assets import WebAssets


def test_versioned_page_keeps_styles_modules_and_fonts_in_one_release(tmp_path):
    (tmp_path / "index.html").write_text(
        '<link href="/assets/app.css"><script src="/assets/app.mjs"></script>'
    )
    for name in ("app.css", "app.mjs", "map.mjs"):
        (tmp_path / name).write_text(name)
    assets = WebAssets(tmp_path)
    app = FastAPI()
    app.mount("/assets", assets)
    with TestClient(app) as client:
        html, revision = assets.page()
        assert f"/assets/v-{revision}/app.css" in html
        module = re.search(r'src="([^"]+)"', html)[1]
        assert client.get(module).text == "app.mjs"
        assert client.get(urljoin(module, "./map.mjs")).text == "map.mjs"
        old_css = f"/assets/v-{revision}/app.css"
        assert client.get(old_css).text == "app.css"
        # Changing a dependency also changes the entry stylesheet URL. Even a
        # browser/CDN keeping the old release fresh for hours must fetch it anew.
        (tmp_path / "map.mjs").write_text("updated camera")
        new_html, new_revision = assets.page()
        assert new_revision != revision and new_html != html
        assert client.get(f"/assets/v-{new_revision}/app.css").text == "app.css"
        assert client.get(old_css).status_code == 404
        assert client.get("/assets/app.css").text == "app.css"
        assert client.get(f"/assets/v-{new_revision}/%2e%2e/%2e%2e/.env").status_code == 404
