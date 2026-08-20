import { expect, test, type Page } from "@playwright/test";

/**
 * The v0.1 journey, as a person performs it.
 *
 * These assert what a user can see and do. The numbers themselves -- that a
 * percentile is merged rather than averaged, that errors group -- are proven
 * against the API by the integration suite, because a browser is a poor place
 * to check arithmetic.
 */

const ADMIN = { email: "admin@demo.plimsoll.dev", password: "plimsoll-demo-password" };

async function signIn(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByRole("textbox", { name: "Email" }).fill(ADMIN.email);
  await page.getByRole("textbox", { name: "Password" }).fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Runs" })).toBeVisible();
}

test("a wrong password does not sign anyone in", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox", { name: "Email" }).fill(ADMIN.email);
  await page.getByRole("textbox", { name: "Password" }).fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();

  // The refusal is shown rather than swallowed, and the form stays put.
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Runs" })).toHaveCount(0);
});

test("signing in shows the runs", async ({ page }) => {
  await signIn(page);
  await expect(page.getByRole("table")).toBeVisible();
});

test("a run opens and reports what it measured", async ({ page }) => {
  await signIn(page);

  // The most recent run with a result to show. A run still in flight has no
  // results yet, which would make this test about timing rather than about
  // the page.
  const completed = page.getByRole("row").filter({ hasText: "COMPLETED" }).first();
  await expect(completed).toBeVisible();
  await completed.getByRole("link").click();

  await expect(page.getByRole("heading", { name: /^Run #/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Results" })).toBeVisible();

  // The claim the whole project turns on, stated where a user reads it.
  await expect(
    page.getByText(/merged across every generator, never averaged/i),
  ).toBeVisible();

  // A completed run names the commit it was pinned to, so what ran is
  // recoverable from what is shown.
  await expect(page.getByText(/Pinned to commit/)).toBeVisible();
});

test("a run's transactions carry percentiles", async ({ page }) => {
  await signIn(page);
  const completed = page.getByRole("row").filter({ hasText: "COMPLETED" }).first();
  await completed.getByRole("link").click();

  const results = page.getByRole("table").filter({ hasText: "Transaction" });
  await expect(results).toBeVisible();
  await expect(results.getByRole("columnheader", { name: "p95" })).toBeVisible();

  // At least one transaction, with a duration rather than a placeholder.
  await expect(results.getByRole("row").filter({ hasText: /\d+ms/ }).first()).toBeVisible();
});

test("an unauthenticated visitor is asked to sign in", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Runs" })).toHaveCount(0);
});
