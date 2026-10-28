"""Fetch a paper's arXiv HTML, retain its structure, and sanitize before rendering."""
import asyncio
import hashlib
import re
from urllib.parse import urljoin, urlsplit

import httpx
import nh3
from bs4 import BeautifulSoup

MATH_TAGS = set('math semantics annotation mrow mi mn mo mtext mspace ms mfrac msqrt mroot mstyle merror mpadded mphantom mfenced menclose msub msup msubsup munder mover munderover mmultiscripts mprescripts none mtable mtr mtd maligngroup malignmark'.split())
TAGS = set('article section div span p h1 h2 h3 h4 h5 h6 a figure figcaption img table caption colgroup col thead tbody tfoot tr td th ul ol li dl dt dd blockquote pre code em strong b i u s sub sup small br hr abbr cite q time mark'.split()) | MATH_TAGS
ATTRS = {'*': {'id', 'class'}, 'a': {'href', 'title'}, 'img': {'src', 'alt', 'width', 'height'}, 'td': {'colspan', 'rowspan'}, 'th': {'colspan', 'rowspan', 'scope'}}
for tag in MATH_TAGS:
    ATTRS[tag] = {'id', 'class', 'display', 'displaystyle', 'mathvariant', 'stretchy', 'fence', 'separator', 'accent', 'accentunder', 'columnalign', 'rowalign', 'columnspacing', 'rowspacing', 'columnspan', 'rowspan', 'linethickness', 'notation', 'encoding', 'scriptlevel'}


async def fetch_bounded(client, url, limit):
    async with client.stream('GET', url) as response:
        response.raise_for_status()
        data = bytearray()
        async for part in response.aiter_bytes():
            data.extend(part)
            if len(data) > limit:
                raise ValueError('Paper resource is too large.')
        return bytes(data)


def prepare_html(raw, paper_id, html_url):
    soup = BeautifulSoup(raw, 'html.parser')
    article = soup.select_one('article.ltx_document')
    if article is None:
        raise ValueError('arXiv has no HTML version of this paper.')
    for obj in article.find_all('object'):
        if obj.get('type') == 'image/svg+xml' and obj.get('data'):
            image = soup.new_tag('img', src=obj['data'], alt='Original paper figure')
            image['class'] = obj.get('class', [])
            obj.replace_with(image)
        else:
            obj.decompose()
    for bad in article.find_all(['script', 'style', 'iframe', 'form', 'input', 'button', 'link', 'svg', 'audio', 'video', 'embed']):
        bad.decompose()
    assets = {}
    for image in article.find_all('img'):
        remote = urljoin(html_url, image.get('src', ''))
        parsed = urlsplit(remote)
        extension = parsed.path.rsplit('.', 1)[-1].lower()
        # arXiv resources only; no redirects, credentials, query strings, or arbitrary fetches.
        identifier = re.sub(r'v\d+$', '', urlsplit(html_url).path.removeprefix('/html/'))
        paper_path = r'^/html/' + re.escape(identifier) + r'(?:v\d+)?/'
        if parsed.scheme != 'https' or parsed.netloc != 'arxiv.org' or not re.match(paper_path, parsed.path) or extension not in ('png', 'jpg', 'jpeg', 'gif', 'webp', 'svg') or parsed.query or parsed.fragment or len(assets) >= 100:
            image.replace_with(soup.new_string('[Figure unavailable — see original PDF]'))
            continue
        name = hashlib.sha256(remote.encode()).hexdigest()[:24] + '.' + extension
        assets[name] = remote
        image['src'] = f'/api/papers/{paper_id}/assets/{name}'
        image['alt'] = image.get('alt') or 'Original paper figure'
    for link in article.find_all('a'):
        href = link.get('href', '')
        if href.startswith('#'):
            continue
        remote = urljoin(html_url, href)
        if urlsplit(remote).scheme not in ('https', 'http', 'mailto'):
            link.attrs.pop('href', None)
        else:
            link['href'] = remote
    clean = nh3.clean(str(article), tags=TAGS, attributes=ATTRS, url_schemes={'https', 'http', 'mailto'}, strip_comments=True,
                     clean_content_tags={'script', 'style', 'iframe', 'object', 'embed'})
    # Assign stable passage anchors after sanitization; annotations reference exact text nodes.
    document = BeautifulSoup(clean, 'html.parser')
    passages = []
    for index, paragraph in enumerate(document.select('p')):
        text = paragraph.get_text()
        if not text.strip():
            continue
        anchor = f'passage-{index}'
        paragraph['data-anchor'] = anchor
        paragraph['class'] = paragraph.get('class', []) + ['reader-block']
        passages.append({'anchor': anchor, 'text': text})
    contents = []
    for index, heading in enumerate(document.select('h1,h2,h3,h4,h5,h6')):
        heading['id'] = heading.get('id') or f'reader-heading-{index}'
        contents.append({'id': heading['id'], 'title': heading.get_text(' ', strip=True)})
    return {'html': str(document), 'passages': passages, 'contents': contents, 'url': html_url}, assets


async def import_reader(paper, store):
    from .papers import arxiv_id
    identifier = arxiv_id(paper['source'])
    if not re.search(r'v\d+$', identifier):
        first_page = (paper.get('pages') or [{}])[0].get('text', '')
        version = re.search(re.escape(identifier) + r'(v\d+)', first_page)
        if version:
            identifier += version.group(1)
    url = f'https://arxiv.org/html/{identifier}'
    async with httpx.AsyncClient(timeout=40, follow_redirects=False) as client:
        raw = await fetch_bounded(client, url, 10 * 1024 * 1024)
        reader, assets = prepare_html(raw, paper['id'], url)
        directory = store.root / f"{paper['id']}-assets"
        directory.mkdir(exist_ok=True)
        gate = asyncio.Semaphore(4)
        total = 0
        async def asset(name, remote):
            nonlocal total
            async with gate:
                try:
                    data = await fetch_bounded(client, remote, 5 * 1024 * 1024)
                    total += len(data)
                    if total > 40 * 1024 * 1024:
                        return name
                    (directory / name).write_bytes(data)
                    return None
                except (httpx.HTTPError, ValueError):
                    return name
        missing = [name for name in await asyncio.gather(*(asset(name, remote) for name, remote in assets.items())) if name]
        if missing:
            doc = BeautifulSoup(reader['html'], 'html.parser')
            for image in doc.find_all('img'):
                if image['src'].rsplit('/', 1)[-1] in missing:
                    image.replace_with(doc.new_string('[Figure unavailable — see original PDF]'))
            reader['html'] = str(doc)
        return reader
