/**
 * Backend connection settings.
 *
 * Defaults target the FastAPI app through the Vite dev proxy (see
 * `vite.config.ts`), using the development API key from `.env.example`.
 * Override any of them in `frontend/.env.local`.
 *
 * The customer id is config rather than a login because this phase has no
 * auth UI: the backend scopes every conversation to the customer in the
 * `X-Customer-Id` header, so a real sign-in slots in here later.
 */
export const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1';
export const API_KEY = import.meta.env.VITE_API_KEY ?? 'dev-public-key';
export const CUSTOMER_ID = import.meta.env.VITE_CUSTOMER_ID ?? '1';

export function authHeaders(): Record<string, string> {
  return {
    'X-API-Key': API_KEY,
    'X-Customer-Id': CUSTOMER_ID,
  };
}
