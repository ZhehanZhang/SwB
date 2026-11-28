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
| GitHub Rulesets | Restrict creation, updates, and deletion of `v*` tags to release managers. |
| [PyPI Trusted Publishing](https://pypi.org/manage/account/publishing/) | Add a publisher for `swb.one`. Set owner `ZhehanZhang`, repository `SwB`, workflow `release.yml`, and environment `pypi`. Use a pending publisher for the first upload. |
| [TestPyPI Trusted Publishing](https://test.pypi.org/manage/account/publishing/) | Add a publisher for `swb.one`. Set owner `ZhehanZhang`, repository `SwB`, workflow `release.yml`, and environment `testpypi`. Use a pending publisher for the first upload. |
| GitHub Actions | Run the PyPI workflow by hand to upload to TestPyPI. |
| GitHub Settings | Enable private vulnerability reporting in Advanced Security after the repository goes public. |
| Code of Conduct | Set a public contact address in `CODE_OF_CONDUCT.md`. |
| GitHub pull request | Merge the approved release changes into `main`. |
| GitHub Packages | Change `ghcr.io/zhehanzhang/swb` visibility to public when approved. |
| GitHub Settings | Change the repository visibility to public after the privacy review and approval. |

## Prepare the Mac app

Build the app from the release source on a Mac. Use a Developer ID Application identity for team `Q64ZL36CJN`. If you have an Xcode archive, [upload it with your Xcode account](https://help.apple.com/xcode/mac/current/en.lproj/dev88332a81e.html) and export the notarized app.

To use `notarytool`, store the credentials in a local Keychain profile. Enter your Apple Account and an app-specific password at the prompts.

```sh
xcrun notarytool store-credentials swb-notary-1.0 --team-id Q64ZL36CJN
```

Set `SIGNING_IDENTITY` to the Developer ID Application identity. Run the commands from the release checkout.

```sh
work="$(mktemp -d)"
app="$work/SwB Companion.app"
bash native/companion-macos/build.sh "$app"
codesign --force --sign "$SIGNING_IDENTITY" --options runtime --timestamp "$app"
codesign --verify --deep --strict "$app"
ditto -c -k --sequesterRsrc --keepParent "$app" "$work/submission.zip"
xcrun notarytool submit "$work/submission.zip" --keychain-profile swb-notary-1.0 --wait
```

Check that Apple reports **Accepted**. Save the submission ID with the release records.

```sh
xcrun stapler staple "$app"
xcrun stapler validate "$app"
spctl --assess --type execute -a -vv "$app"
ditto -c -k --sequesterRsrc --keepParent "$app" "$work/swb-companion-macos.zip"
(cd "$work" && shasum -a 256 swb-companion-macos.zip > swb-companion-macos.zip.sha256)
```

## Tag the merge commit

Verify the merge commit on `main`. Replace `<main-merge-commit>` with its full commit ID.

```sh
git fetch --no-tags origin main
git tag -a v1.0.0 <main-merge-commit> -m "SwB 1.0.0"
git push origin refs/tags/v1.0.0
```

The tag selects the commit for the release.

## Publish the GitHub Release

Make the repository public before you publish the release.

Select `v1.0.0` in the GitHub Release draft. Attach the locally notarized `swb-companion-macos.zip`. Publish the release.

The release starts the PyPI and container workflows. The PyPI workflow checks the app's signature and notarization. It pins the attached archive in the Python packages and checks the public download before it uploads them.

For retries, keep the same archive attached and select **Re-run failed jobs**.

Keep the signed app asset attached to its release. Each Python package checks for those exact bytes.

The container workflow publishes `1.0.0`, `1.0`, and `latest` for AMD64 and ARM64.

Run the container workflow by hand on the private repository to publish a private candidate. Verify package visibility in GitHub Packages.
