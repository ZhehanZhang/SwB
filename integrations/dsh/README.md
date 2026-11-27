# SwB bridge for DeepSeek Harness

Use DeepSeek Harness `0.2.2-rc.5`. Add the bridge to your Web profile.

```sh
dsh plugin --profile web add github:ZhehanZhang/SwB#path:/integrations/dsh
```

Use an absolute path to install from a local SwB checkout.

```sh
dsh plugin --profile web add /path/to/SwB/integrations/dsh
```

Open your `web` profile. Add `--cap dsh` when you connect the SwB companion.
Set the same absolute `SWB_COMPANION_HOME` for both programs when you use a custom folder.
Run one Web profile per companion folder.

The bridge shares the status of open sessions. The session stays under your control.
Review approval requests in DeepSeek Harness.

Run `npm install`, `npm test` and `npm run build` to check changes.
Commit the rebuilt `lib` files so GitHub installs can load the bridge directly.
