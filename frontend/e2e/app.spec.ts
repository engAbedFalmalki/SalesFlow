import { expect, test, type Page } from "@playwright/test";

const API = process.env.E2E_API_URL ?? "http://127.0.0.1:8000";

async function apiGet<T>(page: Page, path: string): Promise<T> {
  const r = await page.request.get(`${API}${path}`);
  expect(r.ok()).toBeTruthy();
  return (await r.json()) as T;
}

function grouped(v: string): string {
  const [i, f] = v.replace("-", "").split(".");
  return `${i.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${f}`;
}

test("overview KPIs equal the API for the selected period", async ({ page }) => {
  await page.goto("/?from=2026-09-01&to=2026-09-30");
  const ov = await apiGet<{ metrics: Record<string, { value: string }> }>(page, "/api/overview?date_from=2026-09-01&date_to=2026-09-30");
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.getByText("Sales, by order date", { exact: true })).toBeVisible();
  await expect(page.getByText("Collections, by transaction date", { exact: true })).toBeVisible();
  // Full precision value is in the title attribute of each KPI figure.
  await expect(page.locator(`[title="SAR ${grouped(ov.metrics.active_ordered_value.value)}"]`).first()).toBeVisible();
  await expect(page.locator(`[title="SAR ${grouped(ov.metrics.net_collections.value)}"]`).first()).toBeVisible();
  await expect(page.getByText(/Changes compare with the previous 30 days/)).toBeVisible();
  await expect(page.getByRole("img", { name: /Line chart of active ordered value/ })).toBeVisible();
});

test("overview period preset changes the data and the URL", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Sales, by order date")).toBeVisible();
  await page.getByLabel("Date preset").selectOption("7d");
  await expect(page).toHaveURL(/from=.*&to=/);
  await expect(page.getByText(/^7 days, Asia\/Riyadh dates/)).toBeVisible();
  await page.getByRole("radio", { name: "Week" }).click();
  await expect(page).toHaveURL(/grain=week/);
});

test("sales explorer filters, sorts, paginates and opens order 1001", async ({ page }) => {
  await page.goto("/sales?from=2026-08-01&to=2026-08-31");
  const api = await apiGet<{ total: number }>(page, "/api/orders?date_from=2026-08-01&date_to=2026-08-31&status=cancelled");
  await page.getByRole("button", { name: "Cancelled", pressed: false }).click();
  await expect(page.getByText(new RegExp(`^${api.total.toLocaleString("en-US")} orders?,`))).toBeVisible();
  const rows = page.locator("tbody tr");
  await expect(rows.first().getByText("Cancelled")).toBeVisible();

  await page.getByRole("button", { name: "Clear filters" }).click();
  await page.getByRole("button", { name: /Ordered value/ }).click();
  await expect(page).toHaveURL(/sort=ordered_value/);
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.getByText(/Page 2 of/)).toBeVisible();

  await page.getByLabel("Order number").fill("1001");
  await expect(page).toHaveURL(/q=1001/);
  await expect(rows).toHaveCount(1);
  await expect(page.locator("p", { hasText: "net collections SAR 400.00" })).toBeVisible();
  await page.getByRole("button", { name: "#1001" }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText("Order #1001")).toBeVisible();
  const value = (label: string) => drawer.locator("dt", { hasText: label }).locator("xpath=following-sibling::dd[1]");
  await expect(value("Gross merchandise value")).toHaveText("SAR 500.00");
  await expect(value("Successful payments")).toHaveText("SAR 500.00");
  await expect(value("Successful refunds")).toHaveText("−SAR 100.00");
  await expect(drawer.getByText("SAR 400.00")).toBeVisible();
  await drawer.getByRole("button", { name: "Close" }).click();
  await expect(drawer).toBeHidden();
});

test("excel export link carries the active filters and language", async ({ page }) => {
  await page.goto("/sales?from=2026-09-01&to=2026-09-30&status=pending");
  // One export button only; CSV stays available through the API.
  await expect(page.getByRole("link", { name: "CSV", exact: true })).toHaveCount(0);
  const xlsx = page.getByRole("link", { name: "Export to Excel" });
  await expect(xlsx).toHaveAttribute("href", /export\.xlsx\?date_from=2026-09-01&date_to=2026-09-30&status=pending.*lang=en/);
  const xResp = await page.request.get((await xlsx.getAttribute("href"))!);
  expect(xResp.ok()).toBeTruthy();
  expect(xResp.headers()["content-type"]).toContain("spreadsheetml");
  expect((await xResp.body()).subarray(0, 2).toString()).toBe("PK"); // a real .xlsx (zip) file
});

test("region and city filters narrow the orders and the Excel export", async ({ page }) => {
  await page.goto("/sales?from=2026-09-01&to=2026-09-30");
  const all = await apiGet<{ total: number }>(page, "/api/orders?date_from=2026-09-01&date_to=2026-09-30");
  await page.getByLabel("Region").selectOption("Eastern Province");
  await expect(page).toHaveURL(/region=Eastern\+Province/);
  const cities = await page.locator("#city option").allTextContents();
  expect(cities).toContain("Dammam");
  expect(cities).not.toContain("Jeddah");
  await page.getByLabel("City").selectOption("Dammam");
  await expect(page).toHaveURL(/city=Dammam/);
  const api = await apiGet<{ total: number; items: { customer_city: string }[] }>(page,
    "/api/orders?date_from=2026-09-01&date_to=2026-09-30&region=Eastern%20Province&city=Dammam&page_size=100");
  expect(api.total).toBeGreaterThan(0);
  expect(api.total).toBeLessThan(all.total);
  await expect(page.getByText(new RegExp(`^${api.total} orders?`))).toBeVisible();
  await expect(page.locator("tbody").getByText(/· Dammam$/).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Export to Excel" })).toHaveAttribute("href", /region=Eastern\+Province&city=Dammam/);
  // Picking another region clears the city, which may not belong to it.
  await page.getByLabel("Region").selectOption("Asir");
  await expect(page).not.toHaveURL(/city=/);
});

test("pipeline page lists runs and shows stage details from the backend", async ({ page }) => {
  const runs = await apiGet<{ items: { run_id: number; status: string }[] }>(page, "/api/pipeline/runs?page_size=1");
  await page.goto("/pipeline");
  await expect(page.getByRole("heading", { name: "Pipeline runs" })).toBeVisible();
  const id = runs.items[0].run_id;
  await page.getByRole("button", { name: `#${id}`, exact: true }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText(`Run #${id}`)).toBeVisible();
  await expect(drawer.getByText("Extract operational database").or(drawer.getByText("Stage, normalise and validate")).first()).toBeVisible();
  await expect(drawer.getByText("Publish (commit) and advance watermarks")).toBeVisible();
});

test("data quality page filters quarantine records and opens a record", async ({ page }) => {
  const q = await apiGet<{ total: number; items: { source_record_id: string }[] }>(page, "/api/quality/quarantine?status=open&page_size=1");
  await page.goto("/quality");
  await expect(page.getByRole("heading", { name: "Data quality" })).toBeVisible();
  if (q.total === 0) {
    await expect(page.getByText("Nothing in quarantine")).toBeVisible();
    return;
  }
  await page.getByLabel("Entity").selectOption("payment_txn");
  await expect(page).toHaveURL(/entity=payment_txn/);
  await page.getByLabel("Entity").selectOption("");
  await page.getByRole("button", { name: q.items[0].source_record_id, exact: true }).first().click();
  await expect(page.getByRole("dialog").getByText("Why it was rejected")).toBeVisible();
  await expect(page.getByRole("dialog").getByText("Original payload")).toBeVisible();
});

test("reprocess reports what it changed and refreshes the open list", async ({ page }) => {
  test.setTimeout(330_000);
  const before = await apiGet<{ total: number }>(page, "/api/quality/quarantine?status=open&page_size=1");
  test.skip(before.total === 0, "nothing in quarantine");
  await page.goto("/quality");
  await page.getByRole("button", { name: "Reprocess open records" }).click();
  await page.getByRole("button", { name: "Queue reprocess run" }).click();
  const outcome = page.getByText(/^Run #\d+ (finished|failed)/);
  await expect(outcome).toBeVisible({ timeout: 300_000 });
  const after = await apiGet<{ total: number }>(page, "/api/quality/quarantine?status=open&page_size=1");
  if (after.total === before.total) await expect(outcome).toContainText("none of the");
  await expect(page.getByRole("link", { name: "View run" })).toHaveAttribute("href", /\/pipeline\?run=\d+/);
});

test("reconciliation shows no unresolved differences and expands a row", async ({ page }) => {
  await page.goto("/reconciliation");
  await expect(page.getByRole("heading", { name: "Reconciliation" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Row counts" })).toBeVisible();
  const unresolved = page.locator("p", { hasText: "A difference that quarantine does not explain" }).locator("xpath=preceding-sibling::p[1]");
  await expect(unresolved).toHaveText("0");
  await page.getByRole("button", { name: "Orders (rows)" }).click();
  await expect(page.getByText(/Source rows \(updated_at <=/).first()).toBeVisible();
});

test("architecture page renders definitions from the API", async ({ page }) => {
  await page.goto("/architecture");
  await expect(page.getByRole("img", { name: "SalesFlow architecture" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Net collections", exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: "dw.FactSalesLine" })).toBeVisible();
  await expect(page.getByText(/SYNTHETIC DATA/)).toBeVisible();
});

test("smooth scrolling is active, scroll-triggered sections reveal, drawers scroll natively", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("html")).toHaveClass(/lenis/);
  await expect(page.locator("html")).toHaveAttribute("data-smooth-scroll", "on");
  const status = page.getByRole("region", { name: "Order status" });
  await status.scrollIntoViewIfNeeded();
  // AnimatedContent ends fully opaque once its ScrollTrigger fires.
  await expect.poll(async () => status.evaluate((el) => getComputedStyle(el.firstElementChild!).opacity)).toBe("1");
  // KPI counters land on the exact compact value.
  const kpi = page.locator("[title^='SAR ']").first();
  const title = await kpi.getAttribute("title");
  await expect.poll(async () => (await kpi.textContent())?.trim()).toMatch(/^SAR [\d.,]+[MK]?$/);
  expect(title).toMatch(/^SAR [\d,]+\.\d\d$/);
  // A drawer's own scroll is not hijacked by Lenis.
  await page.goto("/pipeline?run=1");
  const body = page.getByRole("dialog").locator(".overflow-y-auto");
  await expect(page.getByRole("dialog").getByText("Rows by entity")).toBeVisible();
  await body.hover();
  await page.mouse.wheel(0, 600);
  await expect.poll(async () => body.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
});

test("reduced motion: no smoothing, content shown without animation", async ({ browser }) => {
  const context = await browser.newContext({ reducedMotion: "reduce", viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-smooth-scroll", "off");
  const status = page.getByRole("region", { name: "Order status" });
  await expect.poll(async () => status.evaluate((el) => getComputedStyle(el.firstElementChild!).opacity)).toBe("1");
  await context.close();
});

test("mobile navigation works", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  await page.getByRole("button", { name: "Open menu" }).click();
  await page.getByRole("link", { name: "Data quality" }).click();
  await expect(page).toHaveURL(/\/quality/);
  await expect(page.getByRole("heading", { name: "Data quality" })).toBeVisible();
});

test("arabic: whole UI switches to RTL Arabic, digits stay Latin, choice persists", async ({ page }) => {
  await page.goto("/?from=2026-09-01&to=2026-09-30");
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.getByRole("radio", { name: "العربية" }).first().click();
  const html = page.locator("html");
  await expect(html).toHaveAttribute("dir", "rtl");
  await expect(html).toHaveAttribute("lang", "ar");
  await expect(page.getByRole("heading", { name: "نظرة عامة" })).toBeVisible();
  // Server-rendered in Arabic after a reload (cookie), not just client state.
  await page.reload();
  await expect(html).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("heading", { name: "نظرة عامة" })).toBeVisible();
  const ov = await apiGet<{ metrics: Record<string, { value: string }> }>(page, "/api/overview?date_from=2026-09-01&date_to=2026-09-30");
  await expect(page.locator(`[title="${grouped(ov.metrics.net_collections.value)} ر.س"]`).first()).toBeVisible();
  for (const path of ["/", "/sales?q=1001", "/pipeline", "/quality", "/reconciliation", "/architecture"]) {
    await page.goto(path);
    await expect(page.locator("main")).not.toBeEmpty();
    await page.waitForLoadState("networkidle");
    const text = await page.locator("body").innerText();
    expect(text, `Arabic-Indic digits on ${path}`).not.toMatch(/[٠-٩۰-۹]/);
    expect(text, `Arabic text on ${path}`).toMatch(/[ء-ي]/);
  }
  const xlsx = page.getByRole("link", { name: "تصدير إلى Excel" });
  await page.goto("/sales");
  await expect(xlsx).toHaveAttribute("href", /lang=ar/);
  await page.getByRole("radio", { name: "English" }).first().click();
  await expect(html).toHaveAttribute("dir", "ltr");
});

test("date fields accept typed YYYY-MM-DD dates with Latin digits", async ({ page }) => {
  await page.goto("/sales?from=2026-08-01&to=2026-08-31");
  const from = page.getByLabel("From", { exact: true });
  await expect(from).toHaveValue("2026-08-01");
  await from.fill("2026-08-10");
  await from.press("Enter");
  await expect(page).toHaveURL(/from=2026-08-10/);
  // An Arabic keyboard's digits are converted to Latin as you type.
  await from.fill("٢٠٢٦-٠٨-١٥");
  await expect(from).toHaveValue("2026-08-15");
  await from.press("Enter");
  await expect(page).toHaveURL(/from=2026-08-15/);
});

test("test purchase: created in the sources, loaded by the pipeline, shown in the order drawer", async ({ page }) => {
  test.setTimeout(330_000); // the load can wait for SQL Server memory on a busy machine
  const opts = await apiGet<{ products: { sku: string; list_price: string; is_active: boolean }[] }>(page, "/api/filters");
  const product = opts.products.find((p) => p.is_active)!;
  const expected = (Number(product.list_price.replace(".", "")) * 2);
  const total = `SAR ${grouped(`${Math.floor(expected / 100)}.${String(expected % 100).padStart(2, "0")}`)}`;

  await page.goto("/sales");
  await page.getByRole("button", { name: "Add test purchase" }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText("Add a test purchase")).toBeVisible();
  await drawer.getByRole("button", { name: "Create purchase" }).click();
  await expect(drawer.getByRole("alert")).toHaveText("Choose a customer or enter a new one.");

  await drawer.getByLabel("Search by name or code").fill("C00003");
  await drawer.getByRole("button", { name: /C00003/ }).click();
  await drawer.getByRole("combobox", { name: /^Product/ }).selectOption(product.sku);
  await drawer.getByLabel("Quantity").fill("2");
  await expect(drawer.getByText(total, { exact: true })).toBeVisible();
  await drawer.getByRole("button", { name: "Create purchase" }).click();

  const created = drawer.getByText(/^Order #\d+ created in the sources$/);
  await expect(created).toBeVisible();
  const id = (await created.textContent())!.match(/#(\d+)/)![1];
  await expect(drawer.getByText(/Payment file payments_manual_\d+\.json delivered/)).toBeVisible();
  await expect(drawer.getByText("Loaded into the warehouse.")).toBeVisible({ timeout: 300_000 });
  await drawer.getByRole("button", { name: "Open the order" }).click();

  const order = page.getByRole("dialog");
  await expect(order.getByText(`Order #${id}`)).toBeVisible();
  const value = (label: string) => order.locator("dt", { hasText: label }).locator("xpath=following-sibling::dd[1]");
  await expect(value("Gross merchandise value")).toHaveText(total);
  await expect(value("Successful payments")).toHaveText(total);
});
