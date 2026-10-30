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
        assert client.get(old_css).text == "app.css"
        assert client.get(urljoin(module, "./map.mjs")).text == "map.mjs"
        assert client.get(old_css, headers={"If-None-Match": f'"{revision}"'}).status_code == 304
        assert "immutable" in client.get(old_css).headers["cache-control"]
        assert client.head(old_css).content == b""
        assert client.post(old_css).status_code == 405
        assert client.get("/assets/app.css").text == "app.css"
        assert client.get(f"/assets/v-{new_revision}/%2e%2e/%2e%2e/.env").status_code == 404


def test_asset_history_is_bounded_and_retains_original_bytes(tmp_path):
    (tmp_path / "index.html").write_text("/assets/app.css")
    css = tmp_path / "app.css"
    assets = WebAssets(tmp_path)
    app = FastAPI()
    app.mount("/assets", assets)
    with TestClient(app) as client:
        versions = []
        for content in ("first", "second", "third"):
            css.write_text(content)
            versions.append(assets.page()[1])
        assert client.get(f"/assets/v-{versions[0]}/app.css").status_code == 404
        assert client.get(f"/assets/v-{versions[1]}/app.css").text == "second"
        assert client.get(f"/assets/v-{versions[2]}/app.css").text == "third"
        assert client.get("/assets/v-not-a-version/app.css").status_code == 404


def test_known_immutable_assets_skip_filesystem_scans(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("/assets/app.css")
    (tmp_path / "app.css").write_text("original styles")
    assets = WebAssets(tmp_path)
    _, version = assets.page()
    app = FastAPI()
    app.mount("/assets", assets)

    def unexpected_scan():
        raise AssertionError("known immutable assets must not scan the filesystem")

    monkeypatch.setattr(assets, "release", unexpected_scan)
    with TestClient(app) as client:
        url = f"/assets/v-{version}/app.css"
        assert client.get(url).text == "original styles"
        assert client.head(url).status_code == 200
        assert client.get(url, headers={"If-None-Match": f'"{version}"'}).status_code == 304
        assert client.get(f"/assets/v-{version}/missing.css").status_code == 404


def test_cold_worker_discovers_a_version_from_another_worker(tmp_path):
    (tmp_path / "index.html").write_text("/assets/app.css")
    (tmp_path / "app.css").write_text("shared styles")
    _, version = WebAssets(tmp_path).page()
    assets = WebAssets(tmp_path)
    app = FastAPI()
    app.mount("/assets", assets)
    with TestClient(app) as client:
        assert client.get(f"/assets/v-{version}/app.css").text == "shared styles"
