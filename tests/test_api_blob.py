"""Blob fallback publisher (spec §3.4): correct Vercel Blob REST calls, and a
clean no-op when the token is absent."""


from api import blob


class TestPutJson:
    def test_no_token_is_noop(self, monkeypatch):
        monkeypatch.delenv("BLOB_RW_TOKEN", raising=False)
        calls = []
        monkeypatch.setattr(blob.requests, "put", lambda *a, **k: calls.append(a))
        assert blob.put_json("live.json", {"x": 1}) is False
        assert calls == []

    def test_put_url_and_headers(self, monkeypatch):
        monkeypatch.setenv("BLOB_RW_TOKEN", "vercel_blob_rw_test")
        seen = {}

        class Resp:
            status_code = 200
            def raise_for_status(self):
                pass

        def fake_put(url, data=None, headers=None, timeout=None):
            seen.update(url=url, data=data, headers=headers, timeout=timeout)
            return Resp()

        monkeypatch.setattr(blob.requests, "put", fake_put)
        assert blob.put_json("live.json", {"x": 1}) is True
        assert seen["url"] == "https://blob.vercel-storage.com/live.json"
        assert seen["headers"]["Authorization"] == "Bearer vercel_blob_rw_test"
        assert seen["headers"]["x-add-random-suffix"] == "0"
        assert b'"x": 1' in seen["data"] or b'"x":1' in seen["data"]

    def test_http_failure_returns_false(self, monkeypatch):
        monkeypatch.setenv("BLOB_RW_TOKEN", "t")

        def fake_put(*a, **k):
            raise blob.requests.ConnectionError("nope")

        monkeypatch.setattr(blob.requests, "put", fake_put)
        assert blob.put_json("live.json", {"x": 1}) is False  # logged, never raised


class TestBundles:
    def test_history_bundle_keys(self, monkeypatch):
        monkeypatch.setattr(blob, "_history_sources", {
            k: (lambda k=k: {"generated_at": "t", "k": k})
            for k in blob.HISTORY_KEYS
        })
        bundle = blob.history_bundle()
        assert set(bundle["data"].keys()) == set(blob.HISTORY_KEYS)
        assert "generated_at" in bundle

    def test_history_bundle_survives_one_failure(self, monkeypatch):
        srcs = {k: (lambda k=k: {"k": k}) for k in blob.HISTORY_KEYS}
        def boom():
            raise RuntimeError("query failed")
        srcs["causes"] = boom
        monkeypatch.setattr(blob, "_history_sources", srcs)
        bundle = blob.history_bundle()
        assert bundle["data"]["causes"] is None  # missing slice, not a dead bundle
        assert bundle["data"]["staircase"] == {"k": "staircase"}
