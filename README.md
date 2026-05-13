This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.

1. **FastAPI backend** (port 8000)

```bash
cd c:\Users\gopik\Downloads\agri_credit_pipeline
uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload
```

2. **Assessment jobs (pick one)**

- **Inbuilt (no worker process):** In `frontend/.env.local` set `PIPELINE_API_URL=http://127.0.0.1:8000` and, if the API uses `API_SERVICE_KEY`, set `PIPELINE_API_SERVICE_KEY` to the same value. The dashboard enqueue route calls `POST /v1/jobs/assess`, which inserts a MongoDB job and runs the pipeline inside uvicorn after the response returns.
- **Separate worker:** Leave `PIPELINE_API_URL` unset and run `python worker.py` in another terminal; it polls the `jobs` collection for `QUEUED` rows.

3. **Next.js frontend** (port 3000)

```bash
cd c:\Users\gopik\Downloads\agri_credit_pipeline\frontend
npm run dev
```

4. **Public tunnel for webhooks** (→ localhost:3000)

```bash
npx cloudflared tunnel --url http://localhost:3000
```
