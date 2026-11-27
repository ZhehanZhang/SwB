# Contributing to SwB

Use [SwB's issue tracker](https://github.com/ZhehanZhang/SwB/issues) for bugs and feature requests.
Report security issues privately as described in [SECURITY.md](SECURITY.md). Please follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

We welcome contributions to SwB. See the [trademark policy](TRADEMARKS.md).

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
   `blocks.<name>` with `use: <type>`. Set `SWITCHBOARD_CONFIG` to that file and
   `SWITCHBOARD_DATA` to a writable local directory, then run `uv run swb blocks show <name>`
   and `uv run swb blocks check <name>`. Add tests under [`tests/`](tests/) for what the
   block does and how it handles failures.
