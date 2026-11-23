import { test as base, expect, type APIRequestContext } from "@playwright/test";

/**
 * Base test that forces the UI into English regardless of the runner's browser
 * locale, and signs every test in through the fake PocketID.
 *
 * client_v2 resolves its language with Paraglide, whose configured strategy is
 * localStorage → preferredLanguage → baseLocale (see client_v2/vite.config.ts),
 * so a runner whose browser reports e.g. Swedish would translate every label
 * and button the tests match on. We seed Paraglide's localStorage key before any
 * app code runs; the Playwright config additionally pins the context locale.
 */

export interface Identity {
  sub: string;
  name: string;
  /** Group claims; the admin group makes the user an admin. */
  groups?: string[];
}

const BASE_URL = process.env.SPOOLMAN_BASE_URL ?? "http://localhost:8001";

export const ADMIN: Identity = {
  sub: "e2e-admin",
  name: "Ada Admin",
  groups: ["spoolman_admins"],
};
export const MEMBER: Identity = {
  sub: "e2e-member",
  name: "Mia Member",
  groups: [],
};

/**
 * Sign in the way tests_integration/tests/conftest.py does: walk the OIDC
 * redirects with the page's own cookie jar, so the browser ends up holding the
 * session. The fake provider auto-approves as whoever the extra login_*
 * parameters name. Its compose hostname only resolves inside the network, so
 * the authorize URL is pointed at the port the compose file publishes instead.
 */
export async function signIn(api: APIRequestContext, who: Identity = ADMIN): Promise<void> {
  const step = async (url: string) => {
    const res = await api.get(url, { maxRedirects: 0 });
    expect(res.status(), url).toBe(302);
    return new URL(res.headers()["location"], BASE_URL);
  };
  const authorize = await step("/auth/login");
  authorize.host = "localhost:9000";
  authorize.searchParams.set("login_sub", who.sub);
  authorize.searchParams.set("login_name", who.name);
  if (who.groups) authorize.searchParams.set("login_groups", who.groups.join(","));
  const callback = await step(authorize.toString());
  await step(callback.toString());
}

export const test = base.extend<{ identity: Identity }>({
  identity: [ADMIN, { option: true }],
  page: async ({ page, identity }, use) => {
    await page.addInitScript(() => window.localStorage.setItem("PARAGLIDE_LOCALE", "en"));
    await signIn(page.context().request, identity);
    await use(page);
  },
  // The stock `request` has no cookies, so it signs in for itself. Not tied to
  // `page`, which is not available in beforeAll.
  request: async ({ playwright, baseURL, identity }, use) => {
    const api = await playwright.request.newContext({ baseURL });
    await signIn(api, identity);
    await use(api);
    await api.dispose();
  },
});

export { expect };
