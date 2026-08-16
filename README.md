# Dashboard Spese

Self-hosted personal expense dashboard. Import your bank's CSV exports, get
your spending categorised automatically, and read it from your phone as an
installable PWA.

Single user, single password, your own server. No account to create, no data
leaving your machine.

```bash
git clone https://github.com/<user>/dashboard-spese.git
cd dashboard-spese
cp .env.example .env      # edit APP_PASSWORD before starting
docker compose up -d
```

Open <http://localhost:8000>.

---

## What it does

- **Generic CSV import.** No per-bank parsers. You map the columns once per
  format and the mapping is saved. Handles signed amounts and separate
  debit/credit columns, `1.234,56` and `1,234.56`, currency symbols, English
  and Italian date formats, multi-section statements with preambles and
  trailing total rows.
- **Idempotent de-duplication.** Re-import overlapping date ranges as often
  as you like — nothing gets duplicated. Uses the bank's own transaction id
  when the export provides one, otherwise a content hash that still keeps two
  identical purchases on the same day as two transactions.
- **Import preview and undo.** See exactly what will be written *before*
  writing it, with rows already present marked. Any import can be reverted in
  one click.
- **Rules-based categorisation.** Rules are created from the gesture you are
  already making: categorise one transaction and the app offers to apply it
  to the other 29 that look like it. Manual choices are never overwritten.
- **Recurring subscription detection.** Finds charges with a stable cadence,
  a stable amount *and* a stable billing day, and tells you what they cost
  per month.
- **Transfer detection.** Equal and opposite amounts across two of your own
  accounts within a few days are the same money moving, not income and
  spending. They stay in balances and are excluded from totals.
- **Filters everywhere.** Period (including custom ranges), multiple
  accounts, multiple categories, income/expense — shared between the summary
  and the transaction list, so the totals always match the rows below them.
- **Works offline.** Already-synced data stays readable without a connection.

## Requirements

Docker and Docker Compose. Nothing else — Python, MySQL and every dependency
live inside the containers.

> **Windows:** Docker Desktop needs the WSL2 backend. On Windows Home,
> Hyper-V does not exist, and Docker reports a misleading
> *"Virtualization support not detected"* when WSL is simply missing. Fix
> with `wsl --install --no-distribution` from an elevated PowerShell, then
> reboot.

## Configuration

Everything lives in `.env` (copied from `.env.example`, never committed):

| Variable | What it does |
|---|---|
| `APP_PASSWORD` | The single password for the dashboard. **While it is left at an example value the app refuses every login** — better locked than open to anyone who read this repository. |
| `COOKIE_SECURE` | `true` only behind HTTPS. On plain `http://localhost` a `secure` cookie is never sent and login would appear broken. |
| `MYSQL_*` | Database credentials. You never type these; only the containers use them. |
| `APP_PORT` | Host port. Default `8000`. |

The session signing secret is **not** configured here: it is generated on
first boot and stored in the database, so sessions survive restarts and
changing your password does not log you out of your phone. To invalidate
every session (lost device), call `POST /api/auth/logout-everywhere`.

## Importing a CSV

1. Export transactions from your bank — look for *"export transactions"* or
   *"download CSV"*, **not** *"account statement"* (that is an unstructured
   PDF and cannot be imported).
2. **Importa → choose the file.** The app shows the file as it really is:
   numbered lines, detected encoding and delimiter, and a guess at which line
   holds the header. The guess is a suggestion — statements with a summary
   preamble often fool it, so check it.
3. Create an import profile describing the columns.
4. **Anteprima.** Nothing is written yet. Rows already in the database are
   marked.
5. **Importa.**

Export as far back as your bank allows, as early as you can: most banks cap
the window, and history that falls outside it is gone for good. Overlapping
exports are safe.

## Deploying to a server

Run the production override, which binds to localhost only, bakes the code
into the image instead of mounting it, and restarts on boot:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

The app now listens on `127.0.0.1:8000` and is **not** reachable from
anywhere else. How you reach it is the next decision, and it matters more
than it looks.

### Recommended: private network, nothing public

This dashboard lists everywhere you spend money. A public login page is a
door strangers will rattle, and hiding it behind an obscure subdomain does
not work — every certificate issued is published in the public
[Certificate Transparency](https://certificate.transparency.dev/) logs, and
new hostnames get scanned within minutes of appearing there.

The alternative is to not have a public door at all. With
[Tailscale](https://tailscale.com) (free for personal use) the server joins a
private network with your own devices, and from the public internet the port
simply does not exist — nothing to scan, nothing to brute-force.

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Install Tailscale on your phone and laptop too, then enable **MagicDNS** and
**HTTPS certificates** in the admin console and publish the app to your
tailnet:

```bash
sudo tailscale serve --bg 8000
```

The dashboard is now at `https://<machine>.<tailnet>.ts.net` — a real
certificate, so the PWA stays installable — reachable only by your own
devices, from anywhere. Check it with `tailscale serve status`.

Firewall: you only need SSH open, and with `tailscale up --ssh` not even
that.

> Traffic is end-to-end encrypted WireGuard between your devices and does not
> pass through Tailscale's servers; they coordinate keys only. If you want no
> third party at all, plain WireGuard achieves the same with more manual
> setup, and [Headscale](https://github.com/juanfont/headscale) is a
> self-hosted control server.

### Alternative: public domain

If you do want it publicly reachable, a working
[Caddyfile](deploy/Caddyfile.example) is included — Caddy obtains and renews
the certificate on its own and sets sane security headers. Expect automated
login attempts from day one; add fail2ban.

### Before either

- [ ] `APP_PASSWORD` changed to something real
- [ ] `COOKIE_SECURE=true` (both routes serve HTTPS)
- [ ] Production override in use
- [ ] [Backups](deploy/backup.sh) scheduled

Backups matter more than usual here: bank history outside the export window
cannot be downloaded again.

## Bank connections (PSD2) — evaluated and dropped

Automatic bank connections via [Enable Banking](https://enablebanking.com)
were investigated and deliberately **not** implemented. The reasoning is
worth stating, because "why not" is a design decision too:

Enable Banking has no self-serve production access. The only route is a quote
request leading to a commercial contract, with a company registration number
as a required field. Their own form states that *unrestricted access in the
production environment is available only to organisations*.

That is not just a cost problem. If this instance needed a contract, then
**everyone who clones this repository would need one too** — which defeats
the point of a project meant to be self-hosted by anyone.

CSV import works for every bank in the world, for free, offline, today. The
architecture keeps the door open: transaction sources sit behind a
`TransactionSource` interface, so a PSD2 module can be added later without
touching anything else.

**Trade Republic** deserves a note: it has no PSD2 API, but its own CSV
export is the best of the ones tested — native transaction ids, MCC codes,
ISO dates. There is an unofficial reverse-engineered library for its private
app API; do not use it. Account bans, no stability guarantees, and in a
public repository it would amount to advising strangers to hand their
credentials to an unsupported method.

## Development

```bash
docker compose up          # logs in the foreground
docker compose exec app python -m pytest tests/ -q
```

Interactive API docs: <http://localhost:8000/docs>

Database schema changes are plain SQL files in `db/migrations/`, applied once
each in order and tracked in a `schema_migrations` table. To add one, drop in
`00N_name.sql` — no migration tool to learn. The SQLAlchemy models in
`app/models.py` are kept in sync by hand; the SQL files are the source of
truth.

The CSV parser (`app/importers/csv_importer.py`) deliberately imports nothing
from the database layer, so it can be tested against a file without a
container.

## Licence

MIT.
