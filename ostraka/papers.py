import hashlib
import io
import re
from urllib.parse import urlsplit
import httpx
from pypdf import PdfReader

MAX_BYTES = 30 * 1024 * 1024
MAX_PAGES = 200
MAX_CHARS = 600000


def arxiv_id(value):
    value = value.strip()
    if "://" in value:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.hostname not in ("arxiv.org", "www.arxiv.org") or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.query or parsed.fragment:
            raise ValueError("Use an https://arxiv.org/abs/… or /pdf/… link, or an arXiv ID.")
        match = re.fullmatch(r"/(?:abs|pdf)/(.+?)(?:\.pdf)?", parsed.path)
        if not match:
            raise ValueError("Use an arXiv abstract or PDF link.")
        value = match.group(1)
    if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.-]+/\d{7})(?:v\d+)?", value):
        raise ValueError("That does not look like an arXiv paper ID.")
    return value


async def download_arxiv(value):
    identifier = arxiv_id(value)
    async with httpx.AsyncClient(timeout=90, follow_redirects=False) as client:
        # Redirects are explicitly rejected so paper input cannot become arbitrary URL fetching.
        async with client.stream("GET", f"https://arxiv.org/pdf/{identifier}", headers={"User-Agent": "Ostraka/0.1 local research reader"}) as response:
            if response.status_code != 200:
                raise ValueError(f"arXiv returned HTTP {response.status_code}. Retry later or upload the PDF.")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise ValueError("PDF exceeds the 30 MB limit.")
    return bytes(content), f"https://arxiv.org/abs/{identifier}"


def extract_pdf(content, filename):
    if len(content) > MAX_BYTES or not content.lstrip().startswith(b"%PDF-"):
        raise ValueError("Upload a valid PDF under 30 MB.")
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise ValueError("Upload an unencrypted PDF.")
        if len(reader.pages) > MAX_PAGES:
            raise ValueError("The current limit is 200 pages per paper.")
        pages = []
        total = 0
        for index, page in enumerate(reader.pages):
            text = (page.extract_text() or "").replace("\x00", "").strip()
            total += len(text)
            if total > MAX_CHARS:
                raise ValueError("The paper exceeds the 600,000-character processing limit.")
            pages.append({"number": index + 1, "text": text})
        if total < 100:
            raise ValueError("No usable text found. Scanned PDFs need OCR before importing into Ostraka.")
        metadata = reader.metadata
        title = str(metadata.title or "").strip() if metadata else ""
        if not title or title.lower() in ("untitled", "none") or title.endswith(".tex"):
            lines = [x.strip() for x in pages[0]["text"].splitlines() if len(x.strip()) > 15]
            title = lines[0][:200] if lines else filename.rsplit(".", 1)[0]
        return {"title": title[:250], "pages": pages, "sha256": hashlib.sha256(content).hexdigest()}
    except ValueError:
        raise
    except Exception:
        raise ValueError("Could not read this PDF. Try re-exporting it as a text-based PDF.") from None


def chunks(pages, limit=14000):
    """Keep every character, associate exact source pages, split at paragraph boundaries."""
    result = []
    text = ""
    numbers = []
    for page in pages:
        remaining = page["text"]
        while remaining:
            room = limit - len(text)
            if room == 0:
                result.append({"text": text, "pages": numbers})
                text, numbers = "", []
                room = limit
            cut = min(room, len(remaining))
            if cut < len(remaining) and cut > 1000:
                boundary = remaining.rfind("\n", 0, cut)
                if boundary > cut // 2:
                    cut = boundary + 1
            if page["number"] not in numbers:
                numbers.append(page["number"])
            text += remaining[:cut]
            remaining = remaining[cut:]
            if len(text) >= limit or remaining:
                result.append({"text": text, "pages": numbers})
                text, numbers = "", []
    if text:
        result.append({"text": text, "pages": numbers})
    return result
