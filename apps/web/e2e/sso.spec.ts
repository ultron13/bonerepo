import { expect, test, type Page } from "@playwright/test";

/**
 * Single sign-on, from the browser.
 *
 * The API tests prove the flow is correct. This proves it is reachable: the
 * first version of this shipped with a callback that redirected to a page
 * which did not exist, so every check passed and nobody could actually sign in.
 */

const ADMIN = {
  email: "admin@demo.plimsoll.dev",
  password: "plimsoll-demo-password",
};
const ISSUER = "http://idp-fixture:8082";
const DOMAIN = "sso.example.com";

async function signInWithPassword(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByRole("textbox", { name: "Email" }).fill(ADMIN.email);
  await page.getByRole("textbox", { name: "Password" }).fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Runs" })).toBeVisible();
}

test("an administrator turns on single sign-on and somebody signs in with it", async ({
  page,
  context,
}) => {
  const suffix = Math.random().toString(36).slice(2, 8);
  await signInWithPassword(page);

  await page.getByRole("link", { name: "Single sign-on" }).click();
  await expect(
    page.getByRole("heading", { name: "Single sign-on" }),
  ).toBeVisible();

  await page.getByRole("textbox", { name: "Issuer" }).fill(ISSUER);
  await page
    .getByRole("textbox", { name: "Client ID" })
    .fill(`browser-${suffix}`);
  await page.getByRole("textbox", { name: "Client secret" }).fill("a-secret");
  await page
    .getByRole("textbox", { name: "Allowed email domains" })
    .fill(DOMAIN);
  await page
    .getByRole("textbox", { name: "Administrator group" })
    .fill("plimsoll-admins");
  await page.getByRole("button", { name: "Turn on single sign-on" }).click();

  // The link to hand to the organisation, which is what makes this usable.
  const startUrl = await page.locator("p.select-all").innerText();
  expect(startUrl).toContain("/api/v1/auth/oidc/");

  try {
    // A different browser, because the point is somebody else arriving.
    const guest = await context.browser()!.newContext();
    const guestPage = await guest.newPage();
    const email = `browser-${suffix}@${DOMAIN}`;

    // The provider fixture is told who is about to sign in. A real provider
    // would ask them; the API builds the authorize URL, so there is nowhere in
    // this journey for a test to say it otherwise.
    const told = await guestPage.request.post(
      "http://idp-fixture:8082/control",
      {
        data: { plimsoll_email: email },
      },
    );
    expect(told.ok()).toBeTruthy();

    await guestPage.goto(startUrl);
    await expect(
      guestPage.getByRole("heading", { name: "Runs" }),
    ).toBeVisible();

    // A viewer: they were not put in the administrators group.
    await expect(guestPage.getByRole("link", { name: "People" })).toHaveCount(
      0,
    );
    await guest.close();
  } finally {
    await page.getByRole("button", { name: "Turn off" }).click();
    await expect(page.getByText("No provider is configured")).toBeVisible();
  }
});

test("the sign-in page offers single sign-on without a password", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Single sign-on" }).click();
  await expect(
    page.getByRole("textbox", { name: "Organisation" }),
  ).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Password" })).toHaveCount(0);

  await page.getByRole("button", { name: "Use a password instead" }).click();
  await expect(page.getByRole("textbox", { name: "Password" })).toBeVisible();
});
