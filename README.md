# Ostraka

**A place for understanding.** A local paper library with full-text reading, visual study guides, highlights, notes, and presentation generation. Bring your own model key. No hosted account or JavaScript build step required.

## Run it

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git, then:

```sh
git clone https://github.com/Divy-1729/ostraka.git
cd ostraka
export OPENAI_API_KEY="your-key"
uv run --no-editable ostraka
```

Open **http://localhost:8000**. `uv` installs the pinned dependencies automatically, including a suitable Python if needed. Windows PowerShell: use `$env:OPENAI_API_KEY="your-key"` instead of `export`. To choose another port: `uv run --no-editable ostraka --port 8765`.

You can also install with Python 3.10+: `pip install .`, then `ostraka`. Keep the app running in the same terminal in which you set the key. `.env` files are **not loaded automatically**. Do not paste keys into the reader, paper uploads, or this repository.

### Choose a provider

Set one key; the app picks it automatically in the order below. With multiple keys, set `OSTRAKA_PROVIDER` explicitly.

| Provider | Environment | Default model |
| --- | --- | --- |
| OpenAI | `OPENAI_API_KEY` | `gpt-6.1-sol` |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` |
| Google Gemini | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | `gemini-3.8-flash` |
| OpenAI-compatible server | See below | Your model ID |

Override a default using `export OSTRAKA_MODEL="model-id"`. Model availability varies by provider account and changes over time. Restart the app after changing environment settings. Use your provider's exact API model ID, not a ChatGPT/Claude/Gemini app subscription name. A consumer chat subscription does not necessarily include API credits.

For an OpenAI-compatible service (including a local server):

```sh
export OSTRAKA_PROVIDER=compatible
export OSTRAKA_BASE_URL="http://localhost:11434/v1"
export OSTRAKA_MODEL="your-installed-model"
# Set OSTRAKA_API_KEY if your server requires one.
uv run --no-editable ostraka
```

The compatible adapter uses `/chat/completions`. It does not require proprietary JSON mode, but your model must reliably follow a JSON schema supplied in its system prompt. Remote compatible endpoints must use HTTPS. Built-in provider keys are never forwarded to a compatible endpoint.

## Read → understand → present

1. **Add a paper:** upload a text-based PDF, paste an `https://arxiv.org/abs/…` or `/pdf/…` link, or enter an arXiv ID. The tile bar switches between papers. Duplicate PDFs reuse their existing library entry.
2. **Read:** all extracted text is shown in PDF-page order. Open the original PDF to see exact equations, tables, layout, and figures. Find text using the reader's search box.
3. **Make it yours:** select text within one page, then choose **Highlight** or **Note**. Choose a color in the note dialog. Open **Your margins** to edit or delete notes. Everything is saved across browser and server restarts. Export notes as Markdown.
4. **Understand:** import automatically starts a visual guide when a model is configured. The **Visual guide** tab walks through consecutive source excerpts, with explanations, takeaways, definitions, flow/comparison/cycle diagrams, caveats, and clickable source pages. Scroll through the guide or jump using its contents list.
5. **Present:** choose length and audience in **Create presentation**. The tool first completes the guide, then builds a deck using evidence from all its chapters. Download a self-contained HTML deck with diagrams, citations, and speaker notes. Open it offline in a browser. Arrows/Space navigate; **N** toggles notes; **F** toggles full screen; Home/End jump. Browser print prints all slides, so it can also save a PDF.
6. **Export:** download a paper's extracted text, guide, presentation data, and annotations as JSON. This export is for archival/use elsewhere; JSON re-import is not implemented. Back up the data directory for a full restorable library, including original PDFs.

## BYOK flow and data

```text
export provider key → start local server → import paper
                                      ↓
PDF → extract all text → bounded excerpts → provider → validated chapter JSON
                                                      ↓
                               local SQLite ← guide + explanatory diagrams
                                      ↓
                         audience + duration → provider → validated deck JSON
                                                        ↓
                                             standalone HTML presentation
```

- The app binds **127.0.0.1 only**. It is a single-user local app, not an authenticated public hosting service. Do not expose the port through a public tunnel or reverse proxy without adding authentication and deployment hardening.
- Keys are read only from the server's environment, never stored in SQLite, browser storage, generated decks, or public configuration responses. API errors are sanitized. There is no analytics or telemetry.
- Your library lives in **`.ostraka/` in the launch directory**, ignored by Git. Change it with `OSTRAKA_DATA_DIR=/absolute/path/to/library`. Back up this entire folder while the server is stopped. Deleting a paper deletes its PDF, annotations, and artifacts; this action is confirmed in the interface.
- Reading, annotations and existing artifacts work without any model key. Generating new guides/decks requires a configured provider. Importing from arXiv requires internet access.
- Generation sends extracted paper text to your provider. Presentation requests send guide evidence and your audience description. Provider billing and data policies apply. An import can make many calls: one per ~14,000 source characters, up to two schema attempts each, plus a request for a deck. Transient HTTP failures retry up to three times. There is no automatic generation on app launch.
- One generation runs at a time, and papers queue. A partially completed guide is saved after every excerpt. Retrying resumes completed chapters when the provider and model match. Restarting marks unfinished jobs failed; reopen the paper and retry. Rebuilding an existing complete guide with the same model reuses its chapters; change `OSTRAKA_MODEL` to generate a new version.
- Limits: 30 MB, 200 PDF pages, 600,000 extracted characters per paper. Password-protected PDFs and scans without usable text are rejected. OCR, faithful equation extraction, image understanding, and automatic recreation of original paper charts are not included. Generated diagrams are conceptual redrawings.
- Generated explanations can be wrong. Check linked PDF pages, especially numerical results, equations, and causal claims. The tool validates artifact shape and page bounds, not scientific truth.

## Development and verification

```sh
uv sync --locked
uv run pytest
uv run --no-editable ostraka --port 8000
```

The test suite uses synthetic PDFs and mock provider transports: upload, persistence, annotations, arXiv restrictions, job lifecycle, provider formats, invalid output, exported deck escaping, and error paths. It does not spend API credits. To verify your real provider, export your key, add a paper, wait for the guide, then generate and download a presentation. No real-provider run is claimed by the automated tests.

After editing application code, run `uv run --no-editable --reinstall-package ostraka ostraka` to rebuild the installed package.

Plain HTML/CSS/JavaScript frontend; FastAPI server; SQLite persistence; pypdf extraction; HTTP adapters for model APIs. Browser writes require a custom same-origin header; Host and Origin checks protect the local service from ordinary cross-site access. Paper content and model strings are rendered as text, never executable model-generated HTML.

Provider API references: [OpenAI Responses & JSON mode](https://developers.openai.com/api/docs/guides/structured-outputs), [Anthropic Messages](https://platform.claude.com/docs/en/api/overview), [Gemini generateContent](https://ai.google.dev/api/generate-content). Defaults can be overridden without editing code.

## License

MIT for Ostraka's code. Imported papers remain under their authors' respective licenses. Original paper text is not bundled in the repository.
