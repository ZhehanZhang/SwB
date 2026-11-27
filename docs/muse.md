# Connect Muse

SwB pairs with [Meta Muse](https://muse.ai/) as a text device named SwB. Read [the terms for personal use](https://gadgets.muse.ai/sdk-terms).

## Connect SwB

Enable `muse: {use: muse}` under `blocks` in `config/switchboard.yaml`. Restart SwB. Run `swb connect muse` or call `connect(what="muse")` from the owner account.

A bridge token lets the device connect to SwB. Use a password manager to generate 64 random letters and digits. Save the token through the claim link.

## Pair the device

Use a dedicated account on a Linux device with Bluetooth Low Energy. Give the device network access to Muse and the SwB endpoint at `/inbox/muse/bridge`.

On Ubuntu 24.04, install `bluez`, `python3-dbus`, `python3-gi`, and `python3-venv`.

Get a [personal Meta token](https://gadgets.muse.ai/settings/sdk-tokens). Run these commands from the SwB checkout that matches your server release.

```sh
/usr/bin/python3 -m venv --system-site-packages .venv-muse
.venv-muse/bin/pip install . 'musegadget @ git+https://github.com/facebookincubator/muse-gadget-sdk.git@a5f809f621c4cbdbd37336fe17e7cdb74cebf3fe#subdirectory=linux'
.venv-muse/bin/swb muse-bridge --url https://your-swb-host.example --setup
```

Enter the bridge token and the Meta token at the hidden prompts. In the Muse app, turn on **Settings → Devices → Developer mode**. Choose **Add Device**. Select the Bluetooth name that SwB prints.

The Meta token and pairing data stay on the device.

## Send a message

Keep the bridge running. After a restart, run `swb muse-bridge --url https://your-swb-host.example`. Run `swb connect muse` for a test message.

Use `send(to="muse", task="…")` to wake Muse or set a route to `muse:gadget:muse`. Find replies in the sender's inbox or through `result` on the original conversation. Use `agent:muse` for mailbox delivery.

SwB queues routed messages while the device is offline. Matching text from the same sender and project shares one conversation for ten minutes. Each wake uses a separate Muse chat.

Check the Muse app before you resend a message with uncertain delivery. The device keeps each pending reply in its private folder until SwB receives it.

To replace the bridge token, run `swb connect muse --choose replace=true`. Start the device with `--setup`.
