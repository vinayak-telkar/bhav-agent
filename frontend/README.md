# Frontend — Bhavcopy Flow Agent

React + Vite (TypeScript). Two screens, both reading from the FastAPI backend
(`../backend`, see `specs/08_fastapi_routes.md` for the route contract):

- **Manage screen** (`src/pages/ManageScreen.tsx`) — add/remove symbols, set size bucket,
  symbol autocomplete, "I bought it" promotion.
- **Digest dashboard** (`src/pages/Dashboard.tsx`) — needs-attention cards, holdings
  table with signal badges, wishlist panel, insight tracker.

## Run it

```bash
npm install
npm run dev
```

Runs on `http://localhost:5173` by default. The backend must be running on
`http://localhost:8000` (see `../backend/README.md` / `../TESTING.md`) — CORS is
already configured for this pairing in `app/main.py`.
