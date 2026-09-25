# Operations and validation

## Deployment

Run the API behind a TLS reverse proxy on a Windows server or VPS. Bind Uvicorn to loopback and expose only HTTPS. Restrict `.env`, `data/`, and the EA token file in the MT5 terminal common-files directory. Back up SQLite from a consistent snapshot and run exactly one Telegram polling worker per token. Monitor `/health`, process restarts, bot polling errors, and `ea_offline` events. To restore, stop the services, restore the database, restart the API and bot, then check EA heartbeats and broker history. Do not replay old signals. A large multi-worker deployment needs a transactional server database and a tested queue.

## Two computers, two GitHub accounts

The repository is public: either account can `git clone https://github.com/marcel513/valetax-trading-platform.git`. Before work, run `git pull --ff-only`. Before publishing, inspect `git status`, `git diff --cached`, and commit history for secrets, then `git commit -m "description"` and `git push origin main`. The owner must invite the second account through **Settings → Collaborators → Add people**; that account must accept. Public grants read/clone, while accepted collaborator access permits push to this repository. Authenticate Git separately on each device.

## Demo validation still required

1. Confirm desktop MT5 shows **Demo**, the expected Valetax server, and actual gold symbol with its contract details for Cent/Standard/ECN.
2. Confirm HTTPS WebRequest and an EA heartbeat in the bot and admin view.
3. Test paused setting, expired subscription, stale signal, spread/lot/stop rejection, broker refusal, and server outage.
4. Use a small Demo lot and valid absolute stop loss. Verify broker retcode and deal, `orders`/`trades` records, private open alert, broker closure, close report, and private result.
5. Restart the server and EA; confirm the UUID does not place a second order. Reconcile any incomplete broker result manually.

The Valetax MetaEditor compiled `ValetaxDemoEA.mq5` with **0 errors and 0 warnings**. A read-only connection to the open Valetax terminal confirmed Demo account mode, broker connectivity, and an account-specific `XAUUSD.vx` symbol that was not initially selected in Market Watch. The EA now searches the full broker catalogue and selects the matching symbol. A temporary Cloudflare Quick Tunnel exposed the local API over HTTPS for this session; its URL is intentionally not committed because it changes and is unsuitable for continuous operation. On 2026-09-25, the EA linked the Valetax Demo account through a one-time Telegram code and sent authenticated heartbeats over HTTPS. The server recorded `XAUUSD.vx`, contract size 100, tick size 0.01, tick value 1, minimum/step volume 0.01, and maximum volume 50. The private bot `/ea` response and HTTPS dashboard both showed the connection. `LocalPause` stayed true, server risk `enabled` stayed false, and the database contained zero orders. Broker order execution and closure reporting are **not yet verified**. A backend cannot independently prove an HTTP client's claim that an MT5 account is Demo; stronger broker-backed attestation or controlled EA distribution is needed for wider deployment.

## Responsibilities and failures

The user opens and funds a broker account directly, installs and logs in to MT5 locally, allows WebRequest, verifies Demo mode and broker rules, and keeps the terminal/VPS available. The platform stores users, subscriptions, preferences, Demo signals, reports, and alerts. It never holds deposits or trading passwords. A stale signal, invalid stop, excess spread, volume mismatch, daily loss, too many positions, or unavailable broker causes a local EA rejection. Server outage and subscription expiry stop **new** entries; existing broker stops remain. Bot delivery failures do not rewrite order state. Broker partial fills and crash windows require manual history reconciliation before trusting dashboard totals.

`PaymentProvider` is a stub. No checkout, Telegram Stars transaction, or automatic subscription renewal is active. Put proprietary SES strategy logic in a private repository or separately deployed service that passes narrowly validated signals to a future authenticated intake endpoint; never add it to this public Git history.
