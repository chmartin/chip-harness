# Dashboard (Next.js on Vercel)

Views and data reads are specified in `../contract/CONTRACT.md` section 5.

Start:
```bash
npx create-next-app@latest . --ts --app --eslint --no-src-dir
```
Build first against `../contract/mock/*.json`, then switch to Atlas (env `MONGODB_URI`, set in Vercel project settings, never committed).
