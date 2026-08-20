import { expect, test, type Page } from "@playwright/test";

/**
 * From nothing to a run, without a terminal.
 *
 * Until this path existed, a first-time user had to curl their way to a
 * project, a repository and a test before the interface was any use. One test
 * rather than several: it is one journey, and splitting it would make the
 * later steps depend on state a earlier test happened to leave behind.
 */

const ADMIN = { email: "admin@demo.plimsoll.dev", password: "plimsoll-demo-password" };

async function signIn(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByRole("textbox", { name: "Email" }).fill(ADMIN.email);
  await page.getByRole("textbox", { name: "Password" }).fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Runs" })).toBeVisible();
}

test("a project, a repository, a test and a run, all from the browser", async ({ page }) => {
  const suffix = Math.random().toString(36).slice(2, 8).toUpperCase();
  await signIn(page);

  await page.getByRole("link", { name: "Projects →" }).click();
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();

  await page.getByRole("textbox", { name: "Name" }).fill(`Browser ${suffix}`);
  await page.getByRole("textbox", { name: "Project key" }).fill(`B${suffix}`);
  await page.getByRole("button", { name: "Create project" }).click();

  await page.getByRole("link", { name: `B${suffix}` }).click();

  // Scoped to their own sections: both forms have a "Name" field, and
  // positional locators break the moment a section is reordered.
  const repositories = page.locator("section").filter({ hasText: "Script repositories" });
  const tests = page.locator("section").filter({ hasText: "Performance tests" });

  await repositories.getByRole("textbox", { name: "Name" }).fill(`plans-${suffix}`);
  await repositories
    .getByRole("textbox", { name: "Repository URL" })
    .fill("http://script-fixture/public/plans.git");
  await repositories.getByRole("textbox", { name: "Plan path" }).fill("perf/checkout.jmx");
  await repositories.getByRole("button", { name: "Connect repository" }).click();

  await expect(repositories.getByRole("button", { name: "Verify" })).toBeVisible();
  await repositories.getByRole("button", { name: "Verify" }).click();
  await expect(repositories.getByText(/verifies clean|has findings/)).toBeVisible();

  await tests.getByRole("textbox", { name: "Name" }).fill(`browser-test-${suffix}`);
  await tests.getByRole("spinbutton", { name: "Virtual users" }).fill("2");
  await tests.getByRole("spinbutton", { name: "Duration (s)" }).fill("20");
  await tests.getByRole("spinbutton", { name: "Ramp-up (s)" }).fill("1");
  await tests.getByRole("button", { name: "Create test" }).click();

  await expect(tests.getByText(`browser-test-${suffix}`)).toBeVisible();

  await tests.getByRole("button", { name: "Run" }).first().click();

  // Preflight either starts the run or refuses it and names every failing
  // check. Both are answers; a page that showed neither would not be.
  await expect(
    page.getByRole("heading", { name: /^Run #/ }).or(page.getByText(/cannot run yet/i)),
  ).toBeVisible({ timeout: 60_000 });
});
