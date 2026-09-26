# Dashboard (Next.js on Vercel)

A single page, reading live from Atlas every 10 s through `app/api/data/route.js`:

- **Headline tiles:** baseline fmax, best fmax, improvement, harness versions, trials
- **Evolution chart:** fmax per trial colored by harness version, the running best, dashed markers where each evolved version starts, clock-target changes, and rejected trials as grey ticks; hover for details
- **Harness evolution:** each version's status, trigger, verdict, rationale and config diff
- **Latest trials:** the 50 most recent trials

## Local
```bash
npm install
cp .env.local.example .env.local   # read-only `dashboard` Atlas user
npm run dev                        # http://localhost:3000
```

## Vercel
1. Import the repo with Root Directory `dashboard`. `vercel.json` sets the Next.js framework.
2. Set these environment variables:
   - `MONGODB_URI`: the read-only `dashboard` user
   - `MONGODB_DB`: `chipharness`
3. In Atlas, add Network Access `0.0.0.0/0`, since Vercel has no fixed egress IPs. Only the read-only user is exposed to Vercel.
