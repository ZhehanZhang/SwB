# Contributing to SwB

Use [SwB's issue tracker](https://github.com/ZhehanZhang/SwB/issues) for bugs and feature requests.
Report security issues privately as described in [SECURITY.md](SECURITY.md). Please follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

We welcome contributions to SwB.

## Develop locally

Install Python 3.12 or newer and [uv](https://docs.astral.sh/uv/). Clone SwB and set up the environment:

```bash
git clone https://github.com/ZhehanZhang/SwB.git
cd SwB
uv sync --extra test --group dev --locked
uv run pytest -q
uv run ruff check
```

Add a regression test for a bug fix and test new behavior without real credentials or live services.
Use fake values in examples and redact credentials and private data from logs and config you share.

## Commits and pull requests

Use a short imperative subject, such as `Add retry limits to webhook delivery`. Keep each commit
focused and use its body to explain decisions that aren't obvious from the diff. Don't add AI or
co-author trailers.

In the pull request, say what changed, why it matters, and how you checked it. Link the issue if
there is one and call out changes to config or existing installations.

## Proofread translations

Agents translate the swb.one docs and landing page from English into Spanish, French, German, Korean and Japanese, and agents check them. Zhehan reads English and Chinese, so a native reader helps a lot. Proofreading help is welcome.

To report a problem, open an issue with the [translation label](https://github.com/ZhehanZhang/SwB/issues/new?labels=translation). Name the page URL, quote the sentence, and suggest better wording.

To fix it yourself, edit the page in `docs/<locale>/`. The locale codes are `es`, `fr`, `de`, `ko` and `ja`. Each file mirrors the English file with the same path in `docs/en/`. Landing page and interface strings live in the copy modules in `site/lib/`, in `site/lib/ui/<locale>.json`, and in a few components under `site/components/` and `site/app/`. Search the English text to find each one. Keep commands, code, config keys and URLs exactly as they are in English. Keep link paths in your locale, such as `/fr/docs/install`. Run these checks in `site/` and open a pull request against `release/1.0`.

```bash
pnpm install
pnpm check:style
pnpm test:unit
```

## Add a block

Every integration is a block. Keep provider-specific code in its block: the core doesn't name
providers, and blocks don't import each other.

1. Use an existing block in [`src/switchboard/blocks/`](src/switchboard/blocks/) as a starting point.
   Subclass `Block` and declare a `Manifest` with the block's kind, capability levels, actions it
   exposes as core verbs, and any targets, config, secrets, or network access it needs.
2. Implement only the hooks the block needs: routes and authentication for inputs, actions for
   outputs, or both. Send replies back through the core so they reach the right conversation.
3. Register a built-in block in `BLOCKS` in
   [`src/switchboard/blocks/__init__.py`](src/switchboard/blocks/__init__.py). For a separately
   packaged block, register its class in the `switchboard.blocks` entry-point group.
4. Document the block's settings and add it to a local version 2 config under
   `blocks.<name>` with `use: <type>`. Set `SWB_CONFIG` to that file and
   `SWB_DATA_DIR` to a writable local directory, then run `uv run swb blocks show <name>`
   and `uv run swb blocks check <name>`. Add tests under [`tests/`](tests/) for what the
   block does and how it handles failures.
