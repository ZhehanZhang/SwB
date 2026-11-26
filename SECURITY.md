# Security policy

## Supported versions

Security fixes go into the latest release. Older releases don't receive backports. If you run from
source, use the latest commit on the default branch and include the affected commit SHA in your report.

## What to report

Report vulnerabilities in switchboard itself, including its CLI, MCP and web server, bundled blocks,
and the deployment files in this repository. This includes bugs that expose credentials or private data
or let users act outside their permissions.

Issues solely in third-party services or dependencies belong with their maintainers. Report them
here if switchboard's use of that service or dependency creates a vulnerability in switchboard.
Only test on instances you own or have explicit permission to test.

## Report privately

Go to the repository's [GitHub Security Advisories](https://github.com/ZhehanZhang/switchboard/security/advisories)
and choose **Report a vulnerability**. Don't open a public issue or pull request for a suspected vulnerability.

Include the affected version or commit, the relevant block or configuration, the observed impact,
and enough detail for maintainers to verify the report. Don't send credentials, private messages,
or other people's data. Keep the details private while maintainers investigate and coordinate disclosure.
