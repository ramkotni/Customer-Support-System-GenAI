/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API base URL. Default '/api/v1' goes through the Vite dev proxy. */
  readonly VITE_API_BASE?: string;
  /** Value sent as the X-API-Key header. */
  readonly VITE_API_KEY?: string;
  /** Customer this session acts as, sent as X-Customer-Id. */
  readonly VITE_CUSTOMER_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
