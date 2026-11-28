# Release SwB

## Prepare the release

Build the source archive and wheel. Check both files and install the wheel in a clean environment.

```sh
npm ci --prefix web
npm run build --prefix web
uv build
uvx twine check dist/*
scripts/smoke-fresh-install.sh
```

Use [the English release notes](docs/release-notes/1.0.0.md) for the GitHub Release draft.

## Repository owner actions

| Where | Action |
| --- | --- |
| GitHub Environments | Create `pypi`. Allow release tags that match `v*`. |
| GitHub Environments | Create `testpypi`. Allow the branch that runs the test upload. |
| [PyPI Trusted Publishing](https://pypi.org/manage/account/publishing/) | Add a publisher for `swb.one`. Set owner `ZhehanZhang`, repository `SwB`, workflow `release.yml`, and environment `pypi`. Use a pending publisher for the first upload. |
| [TestPyPI Trusted Publishing](https://test.pypi.org/manage/account/publishing/) | Add a publisher for `swb.one`. Set owner `ZhehanZhang`, repository `SwB`, workflow `release.yml`, and environment `testpypi`. Use a pending publisher for the first upload. |
| GitHub Actions | Run the PyPI workflow by hand to upload to TestPyPI. |
| GitHub Settings | Enable private vulnerability reporting in Advanced Security after the repository goes public. |
| Code of Conduct | Set a public contact address in `CODE_OF_CONDUCT.md`. |
| GitHub pull request | Merge the approved release changes into `main`. |
| GitHub Packages | Change `ghcr.io/zhehanzhang/swb` visibility to public when approved. |
| GitHub Settings | Change the repository visibility to public after the privacy review and approval. |

## Tag the merge commit

Verify the merge commit on `main`. Replace `<main-merge-commit>` with its full commit ID.

```sh
git fetch --no-tags origin main
git tag -a v1.0.0 <main-merge-commit> -m "SwB 1.0.0"
git push origin refs/tags/v1.0.0
```

The tag starts the PyPI workflow.

## Publish the GitHub Release

Select `v1.0.0` in the GitHub Release draft and publish the release.

The release starts the PyPI and container workflows. PyPI keeps the files uploaded by the tag run. The container workflow publishes `1.0.0`, `1.0`, and `latest` for AMD64 and ARM64.

Run the container workflow by hand on the private repository to publish a private candidate. Verify package visibility in GitHub Packages.
