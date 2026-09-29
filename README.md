# ADAPT

Adapt a global brief and US master copy for South Africa, Nigeria and the UK. The AI drafts and flags; a local creative director decides.

**Live: [adapt.fly.dev](https://adapt.fly.dev)** · Built as an interview demo. Kin, the brand in the examples, is fictional.

ADAPT drafts a localised variant per market, scores it against the brand's voice rubric (advisory only), and flags cultural and compliance risks. Every change and every flag cites the brief field or market-snapshot entry that justifies it. A named person approves, edits or rejects each variant. There is no publish, schedule, post or share path anywhere in the product or the API: approved copy leaves only by copy-to-clipboard or a .txt download.

## How a run works

Three separate Claude calls per market, plus one for the whole brief:

| Call | Sees | Job |
| --- | --- | --- |
| Draft | Brief, master copy, voice guide, market snapshot | Write the variant and list every change with its citation |
| Score | Brief, voice guide and rubric, the finished variant only | Score five criteria 0/1/2 with a one-line reason. Never sees the draft's reasoning |
| Flag | Brief, market snapshot, master copy, variant | List what to check, each citing its source, each marked stated, implied or missing |
| Brief check | Brief and master copy | Problems every market inherits |

The code, not the model, decides what's trustworthy:

- A citation that doesn't resolve is dropped and logged as `ungrounded`, never shown as grounded.
- The legal line is attached by code, word for word.
- Any edit to the master that no cited change accounts for counts as an uncited claim.
- Confidence comes from signals, never the model's self-report: a change resting on a snapshot's known gap, an uncited edit, or a rescore that swings a criterion by 2.
- A risk the copy only implies is capped at Low, so clean copy isn't blocked by inference.
- Each step saves its result, so a retry resumes from the step that failed. Markets run independently.
- Brief text is treated as data inside delimiters. A live test brief saying "ignore previous instructions" produced normal output.

## The human decides

- Variants show "AI draft" until a named person approves.
- Flags are acknowledged or dismissed (a reason is required), and both can be undone.
- Approve needs a name and every High flag resolved. The score never blocks approval. When Approve is blocked, the reason sits next to the button.
- Reject needs a reason. Undo returns a variant to Draft.
- Edits are inline. Saving marks scores out of date; rescoring is capped at 3 per variant.
- Every action lands in an append-only decision log (enforced by database triggers).

## Does the scorer work?

Tested, not assumed. See **[docs/scorer-test.md](docs/scorer-test.md)**: consistent across repeats and it caught all three deliberately off-voice ads, but on humour it agreed with a human 1 time in 12, because the rubric's own example encoded a judgement the human didn't share at first.

## Stack

One tool per layer. SvelteKit (static) + Tailwind for the screens, FastAPI for the API and for serving the built site from one process, SQLite on a Fly volume, the Anthropic Claude API (Sonnet 5), Fly.io.

## Run it locally

```bash
# API key and model live in api/.env (git ignores it). Copy the example first.
cp api/.env.example api/.env        # then paste your key after ANTHROPIC_API_KEY=
```

```bash
cd web && npm install && npm run build && cd ..
```

```bash
uv run --project api uvicorn adapt.main:app --port 8000
```

Open http://localhost:8000. Tests: `cd api && uv run pytest` (they use a fake model and cost nothing).

## Config: content lives in files

| File | What it holds |
| --- | --- |
| `config/brands/kin.yaml` | Voice guide and the 5-criterion rubric (0/1/2 descriptions with an example line each) |
| `config/markets/{za,ng,uk}.yaml` | Market snapshots: voice norms, register, references to use and avoid, sensitivities (with severity), compliance (with source and a verified flag), known gaps |
| `config/baseline.yaml` | Manual baseline, hours per market, for the efficiency panel. Empty shows "Manual baseline not yet measured." |

The app validates all config at startup and refuses to boot on duplicate or malformed IDs, naming the file and entry.

**The market snapshots are illustrative.** They were drafted by Claude from general knowledge for this demo, not curated legal or cultural guidance. They say so in each file (`status: illustrative`), in the interface next to every citation, and every compliance entry is `verified: false`.

### Add a market

1. Copy `config/markets/_template.yaml` to `config/markets/<code>.yaml`.
2. Fill it in. Every entry needs a unique ID with the market prefix, e.g. `FR-C1`.
3. Restart. If anything is wrong, startup says exactly what.

The brief form offers every configured market. The seed examples cover ZA, NG and UK only.

### Add a brand

Add `config/brands/<id>.yaml` with the same shape as `kin.yaml`. No code change. (The demo's screens and seeds use Kin.)

### Regenerate the seed examples

The four example campaigns are real pipeline output, reviewed and cached in `fixtures/seed/`. Regenerate after changing a snapshot or prompt (about $0.40):

```bash
uv run --project api python scripts/seed.py
```

Read `fixtures/seed/REVIEW.md` before deploying. A changed fixture replaces its template on the next start; visitors' existing copies are kept.

## Cost

A run costs about $0.10 (10 Sonnet 5 calls); the worst case with every rescore used is about $0.15. Opening the examples costs nothing.

Limits, all counted from the runs table and checked on the server:

| Limit | Default | Env var |
| --- | --- | --- |
| Per browser session, per day | 5 | `ADAPT_RUNS_PER_DAY` |
| Per network (hashed IP), per day | 15 | `ADAPT_RUNS_PER_IP_PER_DAY` |
| Everyone, per day | 15 | `ADAPT_GLOBAL_RUNS_PER_DAY` |

At the defaults, the most ADAPT can spend is about $2.25 a day. If the API account runs out of credit, visitors see "ADAPT's AI budget has run out for now. The example campaigns still work." and new runs pause for an hour.

**The backstop is the Anthropic console, not the app.** Use prepaid credit with auto-reload off, or set a monthly spend limit, so a bug or abuse can never cost more than you chose.

## Deploy (Fly.io)

One app, one machine, one 1GB volume; the machine sleeps when idle.

```bash
fly apps create adapt
```

```bash
fly volumes create adapt_data --region jnb --size 1 --app adapt
```

```bash
fly secrets set ANTHROPIC_API_KEY=<your key> ADAPT_IP_SALT=<any random string> --app adapt
```

```bash
fly deploy
```

Keep it to one machine: SQLite lives on the volume and can't be shared.
