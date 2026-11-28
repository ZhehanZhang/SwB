import { expect, test, type Page } from "@playwright/test"

const phone = { width: 390, height: 844 }
const capture = process.env.SWB_INBOX_PHONE_VIDEO === "1"
const conversations = (page: Page) => page.getByRole("region", { name: "Conversations", exact: true })

test.use({ video: capture ? { mode: "on", size: phone } : "off" })

test.describe("Phone inbox", () => {
  test.use({
    viewport: phone,
    isMobile: true,
    hasTouch: true,
  })

  test("Back restores the loaded pages and exact scroll position", async ({ page }) => {
    const inboxRequests: (string | null)[] = []
    page.on("request", (request) => {
      const url = new URL(request.url())
      if (url.pathname === "/api/inbox" && request.method() === "GET") inboxRequests.push(url.searchParams.get("cursor"))
    })
    await page.goto("/inbox?project=operations")
    const list = conversations(page)
    const more = list.getByRole("button", { name: "More", exact: true })
    const older = list.getByRole("link", { name: "Conversation 1", exact: true })
    await expect(more).toBeVisible()
    await expect(older).toHaveCount(0)
    await more.click()
    await expect(older).toBeVisible()
    await expect(more).toHaveCount(0)
    const loadedRows = await list.getByRole("row").count()
    await older.scrollIntoViewIfNeeded()
    const { y, height } = await page.evaluate(() => ({ y: window.scrollY, height: window.innerHeight }))
    expect(y).toBeGreaterThan(height)
    if (capture) await page.waitForTimeout(900)

    await older.click()
    await expect(page).toHaveURL("/inbox/h_history_1?project=operations")
    await expect(page.getByRole("heading", { name: "Conversation 1", exact: true })).toBeVisible()
    await expect(page.getByText("The first conversation.")).toBeVisible()
    await expect(list).toBeHidden()
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0)
    if (capture) await page.waitForTimeout(1300)

    await page.getByRole("link", { name: "Conversations", exact: true }).click()
    await expect(page).toHaveURL("/inbox?project=operations")
    await expect(list.getByRole("row")).toHaveCount(loadedRows)
    await expect(older).toBeInViewport()
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(y)
    expect(inboxRequests).toEqual([null, expect.any(String)])
    if (capture) await page.waitForTimeout(1500)
  })
})

test("Wide inbox keeps the list beside details and restores URL filters", async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 1000 })
  await page.goto("/inbox")
  const list = conversations(page)
  const release = list.getByRole("link", { name: "Release preparation", exact: true })
  const watch = list.getByTitle("h_example_watch")
  const slack = list.getByTitle("h_example_slack")
  const search = page.getByRole("searchbox", { name: "Search inbox" })
  const sort = page.getByRole("combobox", { name: "Sort conversations" })
  const filters = page.getByRole("button", { name: "Filters", exact: true })

  await release.click()
  const detail = page.getByRole("region", { name: "Release preparation", exact: true })
  await expect(detail).toBeVisible()
  await expect(list).toBeVisible()
  const listBox = await list.boundingBox()
  const detailBox = await detail.boundingBox()
  expect(listBox).not.toBeNull()
  expect(detailBox).not.toBeNull()
  expect(detailBox!.x).toBeGreaterThanOrEqual(listBox!.x + listBox!.width)
  await expect(release).toHaveAttribute("aria-current", "page")

  await watch.click()
  await expect(page).toHaveURL("/inbox/h_example_watch")
  await expect(watch).toHaveAttribute("aria-current", "page")
  await expect(detail).toBeHidden()
  await expect(list).toBeVisible()

  await sort.selectOption("oldest")
  await expect(page).toHaveURL("/inbox/h_example_watch?sort=oldest")
  await expect(list.getByRole("rowgroup").last().getByRole("link").first()).toHaveText("Conversation 1")
  await search.fill("Draft the agenda")
  await search.press("Enter")
  await expect(slack).toBeVisible()
  await expect(watch).toHaveCount(0)
  await expect(release).toHaveCount(0)
  await expect(page).toHaveURL("/inbox/h_example_watch?sort=oldest&q=Draft+the+agenda")

  await filters.click()
  const fields = page.getByRole("dialog", { name: "Inbox filters" })
  const delivery = fields.getByLabel("Delivery")
  for (const status of ["stored", "notified", "notify_failed", "no_owner_surface"]) {
    await expect(delivery.locator(`option[value="${status}"]`)).toHaveCount(1)
  }
  const readState = fields.getByLabel("Read state")
  await expect(readState.getByRole("option", { name: "Unread", exact: true })).toHaveAttribute("value", "1")
  await expect(readState.getByRole("option", { name: "Read", exact: true })).toHaveAttribute("value", "0")
  await fields.getByLabel("Sender").fill("Jamie")
  await fields.getByLabel("Recipient").fill("grok-bot")
  await fields.getByLabel("Block").fill("slack")
  await fields.getByLabel("Channel").fill("#planning")
  await fields.getByLabel("Project").fill("community")
  await fields.getByLabel("Kind").selectOption("message")
  await delivery.selectOption("sent")
  const filteredResponse = page.waitForResponse((response) => {
    const url = new URL(response.url())
    return url.pathname === "/api/inbox" && url.searchParams.has("sender")
  })
  await fields.getByRole("button", { name: "Apply", exact: true }).click()
  const result = await filteredResponse
  const params = { sort: "oldest", q: "Draft the agenda", sender: "Jamie", recipient: "grok-bot",
    block: "slack", channel: "#planning", project: "community", kind: "message", status: "sent" }
  expect(result.ok()).toBe(true)
  expect(Object.fromEntries(new URL(result.url()).searchParams)).toEqual(params)
  await expect(slack).toBeVisible()

  await slack.click()
  await expect(page.getByText("Draft the agenda for our planning meeting.")).toBeVisible()
  expect(new URL(page.url()).pathname).toBe("/inbox/h_example_slack")
  expect(Object.fromEntries(new URL(page.url()).searchParams)).toEqual(params)
  await page.goBack()
  await expect(page).toHaveURL((url) => url.pathname === "/inbox/h_example_watch")
  expect(Object.fromEntries(new URL(page.url()).searchParams)).toEqual(params)
  await page.reload()
  await expect(slack).toBeVisible()
  await expect(search).toHaveValue("Draft the agenda")
  await expect(sort).toHaveValue("oldest")
  await filters.click()
  await expect(fields.getByLabel("Project")).toHaveValue("community")
  await expect(fields.getByLabel("Channel")).toHaveValue("#planning")
  await expect(delivery).toHaveValue("sent")
  await delivery.selectOption("notified")
  await fields.getByRole("button", { name: "Apply", exact: true }).click()
  await expect(list.getByText("0 conversations", { exact: true })).toBeVisible()
})
