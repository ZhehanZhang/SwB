import { expect, test } from "@playwright/test"

test("connection and project controls use the owner mode API", async ({ page, context }) => {
  const unsigned = await context.request.get("/api/modes", { headers: { "cf-access-jwt-assertion": "invalid" } })
  expect(unsigned.status()).toBe(401)
  expect(unsigned.headers()["cache-control"]).toBe("no-store")

  const response = page.waitForResponse(value => new URL(value.url()).pathname === "/api/modes" && value.request().method() === "GET")
  await page.goto("/settings")
  expect((await response).headers()["cache-control"]).toBe("no-store")
  const github = page.getByRole("link", { name: /GitHub/ })
  await expect(github).toBeVisible()
  await expect(github).toContainText("Setup pending")
  await page.getByRole("link", { name: /Discord/ }).click()
  const mode = page.getByRole("combobox", { name: "Mode for deployments" })
  await expect(mode).toHaveValue("assist")
  const row = page.getByRole("row").filter({ has: mode })
  await expect(row).toContainText("From channel")
  const community = page.getByRole("row").filter({ has: page.getByRole("combobox", { name: "Mode for community" }) })
  await expect(community.getByRole("cell").nth(1)).toHaveText("community")
  await mode.selectOption("watch")
  await expect(row).toContainText("Override")
  await page.getByRole("button", { name: "Details for deployments" }).click()
  await expect(page.getByText("Pending messages")).toBeVisible()
  await expect(page.getByText("Next report")).toBeVisible()
  await expect(page.getByRole("cell").getByRole("link", { name: "Inbox" })).toHaveAttribute("href", "/inbox")
  await mode.selectOption("triage")
  await expect(page.getByRole("checkbox", { name: "Label", exact: true })).toBeDisabled()
  await expect(page.getByRole("checkbox", { name: "Notify owner" })).toBeEnabled()
  await expect(page.getByRole("group", { name: "Triage actions" }).getByText("Issue actions require GitHub write access.")).toBeVisible()
  await page.getByRole("checkbox", { name: "Notify owner" }).click()
  await expect(page.getByRole("checkbox", { name: "Notify owner" })).not.toBeChecked()

  await page.goto("/projects/operations")
  await page.getByRole("button", { name: "Modes", exact: true }).click()
  await expect(page.getByRole("table", { name: "Project modes" })).toBeVisible()
  await expect(mode).toHaveValue("triage")
  await expect(page.getByRole("combobox", { name: "Mode for community" })).toHaveCount(0)
  await mode.selectOption("inherit")
  await expect(row).toContainText("From channel")
  await expect(mode).toHaveValue("assist")

  await page.goto("/connections/github")
  await expect(page.getByRole("combobox", { name: "Mode for example-org/example-repo" })).toHaveValue("triage")
  await page.goto("/connections/slack")
  await expect(page.getByRole("combobox", { name: "Mode for #planning" })).toHaveValue("watch")
  await page.goto("/connections/telegram")
  await expect(page.getByRole("combobox", { name: "Mode for -1001000" })).toHaveValue("watch")
})

test("claims and login share the console theme and follow the system by default", async ({ page, browser }) => {
  const paths = ["/fixture/token", "/fixture/credentials", "/fixture/login"]
  await page.setExtraHTTPHeaders({ "cf-access-jwt-assertion": "invalid" })
  await page.emulateMedia({ colorScheme: "dark" })
  for (const path of paths) {
    const response = await page.goto(path)
    expect(response?.ok()).toBe(true)
    expect(response?.headers()["content-security-policy"]).toContain("script-src 'self'")
    await expect(page.locator("html")).toHaveCSS("background-color", "rgb(9, 9, 11)")
  }
  await page.evaluate(() => localStorage.setItem("swb-theme", "light"))
  for (const path of paths) {
    await page.goto(path)
    await expect(page.locator("html")).toHaveCSS("background-color", "rgb(255, 255, 255)")
  }
  await page.emulateMedia({ colorScheme: "light" })
  await page.evaluate(() => localStorage.setItem("swb-theme", "dark"))
  for (const path of paths) {
    await page.goto(path)
    await expect(page.locator("html")).toHaveCSS("background-color", "rgb(9, 9, 11)")
  }

  const fallback = await browser.newPage({ baseURL: new URL(page.url()).origin, javaScriptEnabled: false, colorScheme: "dark" })
  try {
    await fallback.goto("/fixture/login")
    await expect(fallback.locator("html")).toHaveCSS("background-color", "rgb(9, 9, 11)")
  } finally {
    await fallback.close()
  }
})
