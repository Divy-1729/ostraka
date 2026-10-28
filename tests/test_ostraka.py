import asyncio
import io
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas
from ostraka.app import create_app
from ostraka.models import Chapter, Deck
from ostraka.papers import arxiv_id, chunks, extract_pdf
from ostraka.providers import GenerationError, Provider, configuration, generate
from ostraka.storage import Store

HEADERS = {"X-Ostraka": "1"}


def pdf_bytes():
    output = io.BytesIO()
    doc = canvas.Canvas(output, invariant=1)
    doc.setTitle("A synthetic research paper")
    for page in range(1, 4):
        doc.drawString(50, 780, f"Section {page}: A reproducible experiment")
        doc.drawString(50, 750, "A residual stream accumulates updates across transformer layers.")
        doc.drawString(50, 720, "The source describes a method, an observation, and a limitation.")
        doc.showPage()
    doc.save()
    return output.getvalue()


@pytest.fixture
def chapter():
    return {"title": "How the method works", "summary": "The method intervenes on a representation.",
            "takeaways": ["Compare treatments against a control."], "glossary": {"Control": "A comparison condition."},
            "diagram": {"kind": "flow", "title": "Method", "nodes": [{"label": "Input", "detail": "Representations"}, {"label": "Update", "detail": "Add intervention"}], "caption": "A conceptual flow."},
            "caveat": "This is a synthetic source, not an empirical result."}


@pytest.fixture
def no_keys(monkeypatch):
    monkeypatch.setattr("ostraka.providers.shutil.which", lambda *args: None)
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OSTRAKA_API_KEY", "OSTRAKA_PROVIDER", "OSTRAKA_MODEL", "OSTRAKA_BASE_URL", "OSTRAKA_CODEX_BIN"):
        monkeypatch.delenv(key, raising=False)


def upload(client):
    response = client.post("/api/papers/upload", files={"file": ("test.pdf", pdf_bytes(), "application/pdf")}, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()["paper"]


def wait_job(client, job):
    for _ in range(150):
        result = client.get("/api/jobs/" + job["id"]).json()
        if result["status"] in ("complete", "failed"):
            return result
        time.sleep(.01)
    pytest.fail("Job did not finish")


@pytest.mark.parametrize("value,expected", [("2312.06681", "2312.06681"), ("https://arxiv.org/abs/2312.06681v4", "2312.06681v4"), ("https://arxiv.org/pdf/2312.06681.pdf", "2312.06681"), ("hep-th/9901001", "hep-th/9901001")])
def test_arxiv_valid(value, expected):
    assert arxiv_id(value) == expected


@pytest.mark.parametrize("value", ["https://evil.com/pdf/2312.06681", "http://arxiv.org/pdf/2312.06681", "https://arxiv.org@localhost/pdf/2312.06681", "https://arxiv.org/pdf/../../secret", "https://arxiv.org/pdf/2312.06681?redirect=evil", "https://arxiv.org:1234/abs/2312.06681", "file:///etc/passwd", "<script>alert(1)</script>"])
def test_arxiv_rejects_arbitrary_fetches(value):
    with pytest.raises(ValueError):
        arxiv_id(value)


def test_pdf_and_chunks_preserve_every_character():
    result = extract_pdf(pdf_bytes(), "test.pdf")
    assert result["title"] == "A synthetic research paper"
    assert len(result["pages"]) == 3
    sections = chunks(result["pages"], limit=110)
    assert "".join(x["text"] for x in sections) == "".join(x["text"] for x in result["pages"])
    assert set(p for x in sections for p in x["pages"]) == {1, 2, 3}
    with pytest.raises(ValueError):
        extract_pdf(b"not a PDF", "bad.pdf")


def test_no_key_reading_and_notes_persist(tmp_path, no_keys):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/").status_code == 200
        assert not client.get("/api/config").json()["configured"]
        paper = upload(client)
        assert len(client.get("/api/papers").json()) == 1
        text = paper["pages"][0]["text"]
        note = {"page": 1, "start": 0, "end": 20, "quote": text[:20], "note": "My observation", "color": "mint"}
        response = client.post(f"/api/papers/{paper['id']}/annotations", json=note, headers=HEADERS)
        assert response.status_code == 200
        note_id = response.json()["id"]
        note["quote"] = "wrong text"
        assert client.post(f"/api/papers/{paper['id']}/annotations", json=note, headers=HEADERS).status_code == 400
        assert client.post(f"/api/papers/{paper['id']}/guide", headers=HEADERS).status_code == 400
        assert client.get(f"/api/papers/{paper['id']}/pdf").content.startswith(b"%PDF-")
    with TestClient(create_app(tmp_path)) as client:
        saved = client.get("/api/papers/" + paper["id"]).json()
        assert saved["annotations"][0]["note"] == "My observation"
        response = client.patch(f"/api/papers/{paper['id']}/annotations/{note_id}", json={"note": "Updated"}, headers=HEADERS)
        assert response.json()["note"] == "Updated"
        assert "Updated" in client.get(f"/api/papers/{paper['id']}/notes.md").text
        assert client.get(f"/api/papers/{paper['id']}/export").json()["annotations"][0]["note"] == "Updated"
        assert upload(client)["id"] == paper["id"]
        assert client.delete(f"/api/papers/{paper['id']}/annotations/{note_id}", headers=HEADERS).status_code == 200
        assert client.delete(f"/api/papers/{paper['id']}", headers=HEADERS).status_code == 200
        assert client.get("/api/papers").json() == []
        assert not (tmp_path / f"{paper['id']}.pdf").exists()


def test_local_access_guards(tmp_path, no_keys):
    with TestClient(create_app(tmp_path)) as client:
        assert client.post("/api/papers/arxiv", json={"url": "2312.06681"}).status_code == 403
        assert client.post("/api/papers/arxiv", json={"url": "2312.06681"}, headers={**HEADERS, "Origin": "https://evil.example"}).status_code == 403
        assert client.get("/api/config", headers={"Host": "evil.example"}).status_code == 400
        assert client.post("/api/papers/upload", content=b"", headers={**HEADERS, "Content-Length": str(32*1024*1024)}).status_code == 413


def test_generation_guide_deck_and_safe_export(tmp_path, no_keys, monkeypatch, chapter):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-fixture-value")
    calls = []
    async def fake(provider, schema, prompt):
        calls.append(schema)
        if schema == Chapter:
            return chapter.copy()
        return {"title": "Team briefing", "slides": [{"title": "Slide " + str(i), "message": "A lesson </script><script>alert('x')</script>", "bullets": ["A useful point"], "diagram": chapter["diagram"], "notes": "Explain the source carefully.", "pages": [1]} for i in range(6)]}
    monkeypatch.setattr("ostraka.app.providers.generate", fake)
    with TestClient(create_app(tmp_path)) as client:
        paper = upload(client)
        detail = client.get("/api/papers/" + paper["id"]).json()
        assert wait_job(client, detail["jobs"][0])["status"] == "complete"
        detail = client.get("/api/papers/" + paper["id"]).json()
        assert detail["guide_complete"]
        assert detail["guide"][0]["pages"] == [1, 2, 3]
        job = client.post(f"/api/papers/{paper['id']}/deck", json={"minutes": 10, "audience": "Teammates"}, headers=HEADERS).json()
        assert wait_job(client, job)["status"] == "complete"
        html = client.get(f"/api/papers/{paper['id']}/deck.html").text
        assert "/*DECK_" not in html
        assert "<script>alert('x')</script>" not in html
        assert "\\u003c/script>" in html
        assert "speaker-notes" in html
        assert "secret-fixture-value" not in client.get("/api/config").text + html
        assert calls.count(Chapter) == 1
        assert calls.count(Deck) == 1


def test_failed_generation_retry(tmp_path, no_keys, monkeypatch, chapter):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")
    async def fail(*args):
        raise GenerationError("Provider quota exhausted.")
    monkeypatch.setattr("ostraka.app.providers.generate", fail)
    with TestClient(create_app(tmp_path)) as client:
        paper = upload(client)
        jobs = client.get('/api/papers/' + paper['id']).json()['jobs']
        result = wait_job(client, jobs[0])
        assert result["status"] == "failed" and "quota" in result["message"]
        async def succeed(*args):
            return chapter.copy()
        monkeypatch.setattr("ostraka.app.providers.generate", succeed)
        job = client.post(f"/api/papers/{paper['id']}/guide", headers=HEADERS).json()
        assert wait_job(client, job)["status"] == "complete"


@pytest.mark.parametrize("name", ["openai", "anthropic", "gemini", "compatible"])
def test_provider_wire_format(name, chapter):
    payload = json.dumps(chapter)
    def handle(request):
        body = json.loads(request.content)
        assert body.get("model", "fixture-model") == "fixture-model"
        if name == "openai":
            assert request.url.path == "/v1/responses" and body["store"] is False
            assert body["text"]["format"]["type"] == "json_object"
            data = {"output": [{"content": [{"type": "output_text", "text": payload}]}]}
        elif name == "anthropic":
            assert request.headers["anthropic-version"] == "2023-06-01"
            assert request.headers["x-api-key"] == "fixture-key"
            data = {"content": [{"type": "text", "text": payload}], "stop_reason": "end_turn"}
        elif name == "gemini":
            assert request.headers["x-goog-api-key"] == "fixture-key"
            assert body["generationConfig"]["responseMimeType"] == "application/json"
            data = {"candidates": [{"content": {"parts": [{"text": payload}]}, "finishReason": "STOP"}]}
        else:
            assert request.url.path == "/v1/chat/completions"
            data = {"choices": [{"message": {"content": payload}, "finish_reason": "stop"}]}
        return httpx.Response(200, json=data)
    result = asyncio.run(generate(Provider(name, "fixture-model", "fixture-key", "http://localhost/v1"), Chapter, "Explain this source", httpx.MockTransport(handle)))
    assert result == chapter


def test_invalid_output_and_error_redaction():
    provider = Provider("openai", "fixture-model", "private-fixture")
    def unauthorized(request):
        return httpx.Response(401, json={"error": "private-fixture"})
    with pytest.raises(GenerationError, match="401") as exc:
        asyncio.run(generate(provider, Chapter, "test", httpx.MockTransport(unauthorized)))
    assert "private-fixture" not in str(exc.value)
    calls = []
    def bad_output(request):
        calls.append(1)
        return httpx.Response(200, json={"output": [{"content": [{"type": "output_text", "text": "not JSON"}]}]})
    with pytest.raises(GenerationError, match="two attempts"):
        asyncio.run(generate(provider, Chapter, "test", httpx.MockTransport(bad_output)))
    assert len(calls) == 2


def test_provider_selection(no_keys, monkeypatch):
    with pytest.raises(GenerationError, match="No model connected"):
        configuration()
    monkeypatch.setenv("GEMINI_API_KEY", "fixture")
    assert configuration().name == "gemini"
    monkeypatch.setenv("OSTRAKA_PROVIDER", "compatible")
    monkeypatch.setenv("OSTRAKA_MODEL", "local-model")
    monkeypatch.setenv("OSTRAKA_BASE_URL", "http://evil.example/v1")
    with pytest.raises(GenerationError, match="HTTPS"):
        configuration()
    monkeypatch.setenv("OSTRAKA_BASE_URL", "http://localhost:11434/v1")
    assert configuration().key == ""


def test_restart_marks_jobs_failed(tmp_path):
    store = Store(tmp_path)
    store.save("jobs", {"id": "job", "status": "running"}, "paper")
    store = Store(tmp_path)
    assert store.get("jobs", "job")["status"] == "failed"


def test_formatted_reader_sanitizes_and_preserves_structure():
    from ostraka.reader import prepare_html
    raw = '''<article class="ltx_document"><h1>Paper</h1><h2 id="S1">Method</h2>
    <p class="ltx_p" onclick="bad()">A <em>formatted</em> passage with <math><mi>x</mi><mo>+</mo><mn>1</mn></math>.</p>
    <script>steal()</script><iframe src="https://evil.test"></iframe>
    <img src="2312.06681v4/figure.png" onerror="bad()"><img src="https://evil.test/track.png">
    <object type="image/svg+xml" data="2312.06681v4/diagram.svg"></object>
    <a href="javascript:bad()">bad</a><table><tr><td>Evidence</td></tr></table></article>'''
    reader, assets = prepare_html(raw, 'paperid', 'https://arxiv.org/html/2312.06681')
    assert '<math>' in reader['html'] and '<table>' in reader['html']
    assert 'reader-block' in reader['html'] and 'data-anchor="passage-0"' in reader['html']
    assert all(x not in reader['html'] for x in ('onclick', 'onerror', '<script', '<iframe', 'javascript:', 'https://evil.test'))
    assert len(assets) == 2
    assert reader['passages'][0]['text'] == 'A formatted passage with x+1.'
    assert reader['contents'][1]['title'] == 'Method'


def test_html_annotations_validate_against_passages(tmp_path, no_keys):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        paper = upload(client)
        paper['reader'] = {'html': '<p>Useful text</p>', 'passages': [{'anchor': 'passage-0', 'text': 'Useful text'}], 'contents': []}
        app.state.store.save('papers', paper)
        data = {'page': 1, 'anchor': 'passage-0', 'start': 0, 'end': 6, 'quote': 'Useful', 'note': 'Remember'}
        result = client.post(f"/api/papers/{paper['id']}/annotations", json=data, headers=HEADERS)
        assert result.status_code == 200
        assert result.json()['anchor'] == 'passage-0'
        data['quote'] = 'Forged'
        assert client.post(f"/api/papers/{paper['id']}/annotations", json=data, headers=HEADERS).status_code == 400
        assert "frame-ancestors 'self'" in client.get(f"/api/papers/{paper['id']}/pdf").headers['content-security-policy']


def test_codex_subscription_configuration(no_keys, monkeypatch):
    import subprocess
    monkeypatch.setenv('OSTRAKA_PROVIDER', 'codex')
    with pytest.raises(GenerationError, match='Install the official Codex CLI'):
        configuration()
    monkeypatch.setattr('ostraka.providers.shutil.which', lambda *args: '/usr/local/bin/codex')
    monkeypatch.setattr('ostraka.providers.subprocess.run', lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, '', 'Logged in using ChatGPT'))
    assert configuration().public()['configured'] is True
    assert configuration().name == 'codex'
    monkeypatch.setattr('ostraka.providers.subprocess.run', lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, '', 'Logged in using an API key'))
    with pytest.raises(GenerationError, match='must be signed in with ChatGPT'):
        configuration()


def test_codex_uses_cli_without_api_fallback(no_keys, monkeypatch, chapter):
    from pathlib import Path
    from ostraka.providers import codex_request
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-be-forwarded')
    monkeypatch.setenv('CODEX_API_KEY', 'must-not-be-forwarded')
    captured = {}
    class Process:
        returncode = 0
        async def communicate(self, content):
            assert b'UNTRUSTED' in content
            Path(captured['args'][captured['args'].index('--output-last-message')+1]).write_text(json.dumps(chapter))
    async def spawn(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return Process()
    monkeypatch.setattr('ostraka.providers.asyncio.create_subprocess_exec', spawn)
    raw = asyncio.run(codex_request(Provider('codex', 'fixture-model', '', '/fake/codex'), 'system', 'UNTRUSTED source'))
    assert json.loads(raw) == chapter
    assert 'OPENAI_API_KEY' not in captured['kwargs']['env']
    assert 'CODEX_API_KEY' not in captured['kwargs']['env']
    assert 'forced_login_method="chatgpt"' in captured['args']
    assert 'read-only' in captured['args'] and 'shell_tool' in captured['args']
    assert '--ignore-user-config' in captured['args']
