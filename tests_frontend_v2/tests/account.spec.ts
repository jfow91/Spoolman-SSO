import { test, expect, MEMBER } from "./fixtures";
import { openApp } from "./helpers";

// Every test here is signed in by the fixture; these cover what signing in shows.

test("signs in via fake PocketID and sees name in menu", async ({ page }) => {
  await openApp(page);
  await expect(page.getByRole("button", { name: /Ada Admin/ })).toBeVisible();
});

test("creates key and sees URL form", async ({ page }) => {
  await openApp(page);
  await page.getByRole("button", { name: /Ada Admin/ }).click();
  await page.getByRole("menuitem", { name: "Printer keys" }).click();
  await page.getByPlaceholder("Name").fill("e2e bridge");
  await page.getByRole("button", { name: "Create key" }).click();

  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("Authorization: Bearer <key>");
  const key = (await dialog.getByLabel("Key", { exact: true }).textContent()) ?? "";
  expect(key.length).toBeGreaterThan(8);
  await expect(dialog.getByLabel("URL form", { exact: true })).toHaveText(new RegExp(`/k/${key}/$`));

  await dialog.getByRole("button", { name: "Close" }).click();
  await expect(page.getByText("e2e bridge")).toBeVisible();
  await page.getByRole("button", { name: "Revoke" }).first().click();
});

test.describe("member", () => {
  test.use({ identity: MEMBER });

  test("non-admin has no Users link", async ({ page }) => {
    await openApp(page);
    await page.getByRole("button", { name: /Mia Member/ }).click();
    await expect(page.getByRole("menuitem", { name: "Printer keys" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Users" })).toHaveCount(0);
  });
});
