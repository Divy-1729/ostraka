import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool

from . import providers
from .models import Annotation, Chapter, Deck, PresentationRequest
from .papers import MAX_BYTES, chunks, download_arxiv, extract_pdf
from .storage import Store, now
from .reader import import_reader

STATIC = Path(__file__).parent / "static"


def create_app(data_dir=None):
    @asynccontextmanager
    async def lifespan(app):
        yield
        for task in list(app.state.tasks):
            task.cancel()
        await asyncio.gather(*app.state.tasks, return_exceptions=True)

    app = FastAPI(title="Ostraka", lifespan=lifespan)
    app.state.store = Store(data_dir or os.getenv("OSTRAKA_DATA_DIR", ".ostraka"))
    app.state.tasks = set()
    app.state.semaphore = asyncio.Semaphore(1)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_guard(request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("X-Ostraka") != "1":
                return JSONResponse({"detail": "Missing same-origin request header."}, status_code=403)
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "Cross-origin requests are not allowed."}, status_code=403)
            try:
                if int(request.headers.get("content-length", "0")) > MAX_BYTES + 1024 * 1024:
                    return JSONResponse({"detail": "Upload exceeds the 30 MB limit."}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "Invalid Content-Length."}, status_code=400)
            # Bound even chunked request bodies before multipart parsing.
            body = bytearray()
            async for part in request.stream():
                body.extend(part)
                if len(body) > MAX_BYTES + 1024 * 1024:
                    return JSONResponse({"detail": "Upload exceeds the 30 MB limit."}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        return response

    @app.exception_handler(providers.GenerationError)
    async def generation_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def paper_or_404(paper_id):
        paper = app.state.store.get("papers", paper_id)
        if paper is None:
            raise HTTPException(404, "Paper not found.")
        return paper

    def active_job(paper_id):
        return next((job for job in app.state.store.list("jobs", paper_id) if job["status"] in ("queued", "running")), None)

    def save_job(job, **fields):
        job.update(**fields, updated_at=now())
        app.state.store.save("jobs", job, job["paper_id"])

    async def build_guide(paper, provider, job):
        sections = chunks(paper["pages"])
        existing = paper.get("guide", [])
        # Completed sections survive a failed request; only reuse with the same model.
        if paper.get("guide_provider") != provider.public():
            existing = []
        paper["guide"] = existing
        paper["guide_provider"] = provider.public()
        for index, section in enumerate(sections):
            if index < len(existing):
                continue
            save_job(job, message=f"Explaining reading section {index + 1} of {len(sections)}", progress=round(index / len(sections) * 85))
            prompt = f"""Create a visual reading chapter for excerpt {index + 1}/{len(sections)} of paper {paper['title']!r}.
Source PDF pages: {section['pages']}. Cover the source's actual section headings in order. The excerpt may start or end mid-section; acknowledge this instead of inventing missing content. Give a clear title, an explanatory summary, 3–5 takeaways, definitions of unfamiliar terms, and a diagram with 2–6 short labeled nodes and explanatory details. Flow nodes are sequential; compare nodes are alternatives; cycle nodes loop. Add a specific caveat about evidence or scope. If this is references/appendix, describe that purpose; do not invent findings. All strings are plain text.
UNTRUSTED PAPER EXCERPT:\n{section['text']}\nEND EXCERPT"""
            chapter = await providers.generate(provider, Chapter, prompt)
            chapter.update(pages=section["pages"], index=index)
            paper["guide"].append(chapter)
            app.state.store.save("papers", paper)
        paper["guide_complete"] = True
        paper["updated_at"] = now()
        app.state.store.save("papers", paper)

    async def run_job(job, provider, options):
        try:
            async with app.state.semaphore:
                save_job(job, status="running", message="Reading the paper", progress=1)
                paper = paper_or_404(job["paper_id"])
                if not paper.get("guide_complete") or job["kind"] == "guide":
                    await build_guide(paper, provider, job)
                if job["kind"] == "deck":
                    save_job(job, message="Building your presentation and speaker notes", progress=90)
                    # Include every chapter with compact evidence, avoiding whole-paper context truncation.
                    evidence = [{"title": x["title"], "summary": x["summary"], "takeaways": x["takeaways"], "pages": x["pages"], "caveat": x["caveat"]} for x in paper["guide"]]
                    prompt = f"""Create a {options['minutes']}-minute presentation about {paper['title']!r} for audience: {options['audience']!r}.
Use 5–20 slides paced for this duration (about one main idea per minute). Start with the problem and why it matters, explain prerequisites before the method, then method, evidence, limitations, and practical takeaways. Include conceptual diagrams where useful, no invented chart data. Each slide needs a short main message, 1–4 concise bullets, detailed speaker notes, and exact relevant PDF page numbers from the evidence. Evidence summaries are generated from the entire paper; do not overstate them. Title slide must also cite source pages. Teach prerequisite concepts on dedicated slides when needed. At least two slides should have explanatory diagrams.
UNTRUSTED EVIDENCE:\n{json.dumps(evidence)}\nEND EVIDENCE"""
                    deck = await providers.generate(provider, Deck, prompt)
                    count = len(paper["pages"])
                    if any(any(p < 1 or p > count for p in s["pages"]) for s in deck["slides"]):
                        raise providers.GenerationError("The model supplied an invalid source page. Retry presentation generation.")
                    deck.update(minutes=options["minutes"], audience=options["audience"], generated_at=now(), provider=provider.public())
                    paper["deck"] = deck
                    app.state.store.save("papers", paper)
                save_job(job, status="complete", progress=100, message="Your artifact is ready")
        except asyncio.CancelledError:
            save_job(job, status="failed", message="Generation stopped when the server shut down. Retry to resume.")
            raise
        except providers.GenerationError as exc:
            save_job(job, status="failed", message=str(exc))
        except Exception:
            save_job(job, status="failed", message="Generation could not finish. Your paper and completed sections are saved. Retry or check your server configuration.")

    def start_job(paper_id, kind, options):
        paper_or_404(paper_id)
        provider = providers.configuration()
        if active_job(paper_id):
            raise HTTPException(409, "This paper already has a generation in progress.")
        job = {"id": uuid.uuid4().hex, "paper_id": paper_id, "kind": kind, "status": "queued", "progress": 0, "message": "Queued", "created_at": now()}
        app.state.store.save("jobs", job, paper_id)
        task = asyncio.create_task(run_job(job, provider, options))
        app.state.tasks.add(task)
        task.add_done_callback(app.state.tasks.discard)
        return job

    async def import_pdf(content, filename, source=""):
        paper = await run_in_threadpool(extract_pdf, content, filename)
        for existing in app.state.store.list("papers"):
            if existing["sha256"] == paper["sha256"]:
                if source and not active_job(existing["id"]):
                    existing["source"] = source
                    if not existing.get("reader"):
                        try:
                            existing["reader"] = await import_reader(existing, app.state.store)
                        except Exception:
                            pass
                    app.state.store.save("papers", existing)
                return {"paper": existing, "duplicate": True}
        paper.update(id=uuid.uuid4().hex, source=source, created_at=now(), guide=[], guide_complete=False, deck=None)
        if source:
            try:
                paper["reader"] = await import_reader(paper, app.state.store)
            except Exception:
                paper["reader_unavailable"] = True
        path = app.state.store.root / f"{paper['id']}.pdf"
        path.write_bytes(content)
        app.state.store.save("papers", paper)
        job = None
        try:
            job = start_job(paper["id"], "guide", {})
        except providers.GenerationError:
            pass
        return {"paper": paper, "job": job, "duplicate": False}

    @app.get("/api/config")
    def config():
        try:
            return providers.configuration().public()
        except providers.GenerationError as exc:
            return {"configured": False, "message": str(exc)}

    @app.get("/api/papers")
    def library():
        return [{"id": p["id"], "title": p["title"], "page_count": len(p["pages"]), "created_at": p["created_at"], "guide_complete": p.get("guide_complete", False), "has_deck": bool(p.get("deck"))} for p in app.state.store.list("papers")][::-1]

    @app.post("/api/papers/upload")
    async def upload(file: UploadFile = File(...)):
        content = await file.read(MAX_BYTES + 1)
        try:
            return await import_pdf(content, file.filename or "Paper.pdf")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        finally:
            await file.close()

    class ArxivRequest(BaseModel):
        url: str = Field(min_length=1, max_length=500)

    @app.post("/api/papers/arxiv")
    async def arxiv(body: ArxivRequest):
        try:
            content, source = await download_arxiv(body.url)
            return await import_pdf(content, "arxiv.pdf", source)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except Exception:
            raise HTTPException(400, "Could not reach arXiv. Retry or upload the PDF.") from None

    @app.get("/api/papers/{paper_id}")
    def read_paper(paper_id: str):
        paper = paper_or_404(paper_id)
        return {**paper, "annotations": app.state.store.list("annotations", paper_id), "jobs": app.state.store.list("jobs", paper_id)}

    @app.post("/api/papers/{paper_id}/reader")
    async def restore_reader(paper_id: str):
        paper = paper_or_404(paper_id)
        if not paper.get("source"):
            raise HTTPException(400, "This is a PDF upload. Use the original PDF view for its exact layout.")
        if active_job(paper_id):
            raise HTTPException(409, "Wait for generation to finish before restoring the formatted reader.")
        try:
            paper["reader"] = await import_reader(paper, app.state.store)
        except Exception:
            raise HTTPException(400, "No HTML version could be imported. The original PDF view preserves its layout.") from None
        app.state.store.save("papers", paper)
        return {"ok": True}

    @app.get("/api/papers/{paper_id}/assets/{name}")
    def reader_asset(paper_id: str, name: str):
        import re
        paper_or_404(paper_id)
        if not re.fullmatch(r"[a-f0-9]{24}\.(png|jpg|jpeg|gif|webp|svg)", name):
            raise HTTPException(404, "Figure not found.")
        path = app.state.store.root / f"{paper_id}-assets" / name
        if not path.is_file():
            raise HTTPException(404, "Figure not found.")
        return FileResponse(path, headers={"Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'"})

    @app.get("/api/papers/{paper_id}/pdf")
    def original_pdf(paper_id: str):
        paper_or_404(paper_id)
        return FileResponse(app.state.store.root / f"{paper_id}.pdf", media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="paper.pdf"', "Content-Security-Policy": "default-src 'none'; frame-ancestors 'self'"})

    @app.delete("/api/papers/{paper_id}")
    def delete_paper(paper_id: str):
        paper_or_404(paper_id)
        if active_job(paper_id):
            raise HTTPException(409, "Wait for generation to finish before deleting this paper.")
        app.state.store.delete_paper(paper_id)
        return {"ok": True}

    @app.post("/api/papers/{paper_id}/guide")
    async def guide(paper_id: str):
        return start_job(paper_id, "guide", {})

    @app.post("/api/papers/{paper_id}/deck")
    async def deck(paper_id: str, body: PresentationRequest):
        return start_job(paper_id, "deck", body.model_dump())

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        job = app.state.store.get("jobs", job_id)
        if not job:
            raise HTTPException(404, "Job not found.")
        return job

    @app.post("/api/papers/{paper_id}/annotations")
    def annotate(paper_id: str, body: Annotation):
        paper = paper_or_404(paper_id)
        if body.page > len(paper["pages"]):
            raise HTTPException(400, "Invalid page.")
        if body.anchor:
            passage = next((p for p in paper.get("reader", {}).get("passages", []) if p["anchor"] == body.anchor), None)
            if not passage:
                raise HTTPException(400, "Paper passage not found.")
            text = passage["text"]
        else:
            text = paper["pages"][body.page - 1]["text"]
        if body.end > len(text) or body.start >= body.end or text[body.start:body.end] != body.quote:
            raise HTTPException(400, "Highlight does not match the source text. Select it again.")
        annotation = {**body.model_dump(), "id": uuid.uuid4().hex, "created_at": now()}
        app.state.store.save("annotations", annotation, paper_id)
        return annotation

    class NoteUpdate(BaseModel):
        note: str = Field(max_length=15000)

    @app.patch("/api/papers/{paper_id}/annotations/{annotation_id}")
    def update_note(paper_id: str, annotation_id: str, body: NoteUpdate):
        paper_or_404(paper_id)
        existing = next((x for x in app.state.store.list("annotations", paper_id) if x["id"] == annotation_id), None)
        if existing is None:
            raise HTTPException(404, "Note not found.")
        existing["note"] = body.note
        app.state.store.save("annotations", existing, paper_id)
        return existing

    @app.delete("/api/papers/{paper_id}/annotations/{annotation_id}")
    def delete_note(paper_id: str, annotation_id: str):
        paper_or_404(paper_id)
        app.state.store.delete_annotation(annotation_id, paper_id)
        return {"ok": True}

    @app.get("/api/papers/{paper_id}/export")
    def export(paper_id: str):
        paper = paper_or_404(paper_id)
        paper["annotations"] = app.state.store.list("annotations", paper_id)
        return JSONResponse(paper, headers={"Content-Disposition": 'attachment; filename="ostraka-paper.json"'})

    @app.get("/api/papers/{paper_id}/notes.md")
    def notes_export(paper_id: str):
        paper = paper_or_404(paper_id)
        lines = [f"# {paper['title']}", "", paper["source"], ""]
        for note in app.state.store.list("annotations", paper_id):
            lines.extend([("## Paper passage" if note.get("anchor") else f"## PDF page {note['page']}"), "", "> " + note["quote"].replace("\n", "\n> "), "", note["note"], ""])
        return Response("\n".join(lines), media_type="text/markdown", headers={"Content-Disposition": 'attachment; filename="ostraka-notes.md"'})

    @app.get("/api/papers/{paper_id}/deck.html")
    def deck_export(paper_id: str):
        paper = paper_or_404(paper_id)
        if not paper.get("deck"):
            raise HTTPException(404, "Generate a presentation first.")
        data = json.dumps({"deck": paper["deck"], "source": paper["source"], "paper_title": paper["title"]}).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        html = (STATIC / "deck.html").read_text().replace("/*DECK_CSS*/", (STATIC / "deck.css").read_text()).replace("/*DECK_DATA*/", data).replace("/*DECK_JS*/", (STATIC / "deck.js").read_text())
        response = HTMLResponse(html, headers={"Content-Disposition": 'attachment; filename="ostraka-presentation.html"'})
        return response

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()
