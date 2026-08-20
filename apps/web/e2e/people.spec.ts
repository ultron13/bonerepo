import { expect, test, type Page } from "@playwright/test";

/**
 * Adding a colleague, and taking their access away again.
 *
 * The part worth a browser test is the temporary password: it is returned
 * once and never again, so if the interface does not put it in front of the
 * administrator at that moment, the invited account is unreachable and the
 * only fix is to delete it and start over.
 */

const ADMIN = {
  email: "admin@demo.plimsoll.dev",
  password: "plimsoll-demo-password",
};

async function signIn(
  page: Page,
  email: string,
  password: string,
): Promise<void> {
  await page.goto("/");
  await page.getByRole("textbox", { name: "Email" }).fill(email);
  await page.getByRole("textbox", { name: "Password" }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

test("an invited colleague can sign in, and deactivation takes it back", async ({
  page,
}) => {
  const suffix = Math.random().toString(36).slice(2, 8);
  const email = `colleague-${suffix}@example.com`;

  await signIn(page, ADMIN.email, ADMIN.password);
  await expect(page.getByRole("heading", { name: "Runs" })).toBeVisible();
  await page.getByRole("link", { name: "People" }).click();
  await expect(page.getByRole("heading", { name: "People" })).toBeVisible();

  await page.getByRole("textbox", { name: "Email" }).fill(email);
  await page.getByRole("textbox", { name: "Name" }).fill(`Colleague ${suffix}`);
  await page.getByRole("button", { name: "Add person" }).click();

  const password = await page.locator("p.select-all").innerText();
  expect(password.length).toBeGreaterThan(16);
  await expect(page.getByText(email)).toBeVisible();

  // A different browser context, because the point is a second person signing
  // in rather than this session changing identity.
  const guest = await page.context().browser()!.newContext();
  const guestPage = await guest.newPage();
  await signIn(guestPage, email, password);
  await expect(guestPage.getByRole("heading", { name: "Runs" })).toBeVisible();
  // A viewer, so the directory is not theirs to see.
  await expect(guestPage.getByRole("link", { name: "People" })).toHaveCount(0);

  const row = page.locator("li").filter({ hasText: email });
  await row.getByRole("button", { name: "Deactivate" }).click();
  await expect(row.getByRole("button", { name: "Reactivate" })).toBeVisible();
  await guest.close();

  // A fresh browser, because that is what the assertion is about. The access
  // token already in the other one stays valid until it expires -- fifteen
  // minutes, and deliberately so: verifying every request against the
  // database is what stateless tokens exist to avoid. What deactivation ends
  // immediately is the ability to obtain another one, which is why the
  // refresh path refuses and the token families are revoked.
  const returning = await page.context().browser()!.newContext();
  const returningPage = await returning.newPage();
  await signIn(returningPage, email, password);
  await expect(returningPage.getByText(/incorrect|not active/i)).toBeVisible();
  await returning.close();
});
