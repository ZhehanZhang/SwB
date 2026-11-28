Help me connect a webhook to SwB. A webhook sends an event from one service to another over HTTPS.

Use a SwB client with owner access. Call `connect(what="webhook")` to check setup.

For incoming events, ask me for a name, the sending service and the event target. Call `targets` for the target address. Choose the verification scheme the sender uses.

Call `webhook_create` with the name, scheme and target. Give me the private claim link from the result. Ask me to sign in to SwB as the owner. Ask me to open the claim link after sign-in. For path verification, give me `openssl rand -hex 16` to generate the secret. Ask me to save the secret on the claim page. For path verification, ask me to replace `<secret>` with the value I saved. Ask me to paste the URL into the provider's webhook settings.

Wait for me to confirm. If the result includes a `visibility_set` call, run it to let the webhook wake the target. Call `webhook_test` with the webhook name. Tell me whether the event woke the target or arrived as context.

For outgoing messages, call `webhook_create` with the name and `direction="out"`. Give me the private claim link from the result. Ask me to sign in to SwB as the owner. Ask me to open the claim link after sign-in. Ask me to save the receiver URL and signing secret on the claim page. Call `webhook_test` after I confirm that the receiver is ready.
