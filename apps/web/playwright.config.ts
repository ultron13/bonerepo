import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end against the stack `make dev` brings up, not a mock.
 *
 * The point of this suite is the seam the integration tests cannot reach: what
 * a person sees. Everything below it is covered by pytest against the real API,
 * so these tests stay few and stay about the journey.
 */
export default defineConfig({
  testDir: "./e2e",
  // A run takes tens of seconds of real load; the assertions wait on real work.
  timeout: 180_000,
  expect: { timeout: 30_000 },
  // Serial: the tests share one deployment, and a run started by one is visible
  // to the others. Parallel here would be testing the fixtures, not the product.
  workers: 1,
  fullyParallel: false,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.PLIMSOLL_WEB_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
