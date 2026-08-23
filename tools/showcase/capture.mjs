import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "../..");
const BASE_URL = (process.env.INFOMANCER_SHOWCASE_URL || "http://127.0.0.1:8787").replace(/\/$/, "");
const USERNAME = process.env.INFOMANCER_SHOWCASE_USERNAME || "";
const PASSWORD = process.env.INFOMANCER_SHOWCASE_PASSWORD || "";
const OUTPUT_DIR = path.resolve(
  REPO_ROOT,
  process.env.INFOMANCER_SHOWCASE_OUTPUT || "showcase/screenshots",
);
const HEADLESS = process.env.INFOMANCER_SHOWCASE_HEADLESS !== "0";
const REDACT_SELECTORS = process.env.INFOMANCER_SHOWCASE_REDACT_SELECTORS || "";
const DEVICE_SCALE = Number.parseFloat(process.env.INFOMANCER_SHOWCASE_SCALE || "2");
const ONLY = new Set(
  (process.env.INFOMANCER_SHOWCASE_ONLY || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean),
);
const VARIANT_FILTER = new Set(
  (process.env.INFOMANCER_SHOWCASE_VARIANTS || "desktop,social,mobile")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean),
);

const variants = [
  { name: "desktop", width: 1440, height: 900, isMobile: false, hasTouch: false },
  { name: "social", width: 1200, height: 675, isMobile: false, hasTouch: false },
  { name: "mobile", width: 390, height: 844, isMobile: true, hasTouch: true },
].filter((variant) => VARIANT_FILTER.has(variant.name));

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const absolute = (pathname) => new URL(pathname, `${BASE_URL}/`).href;

async function waitForStableFrame(page) {
  await page.waitForLoadState("domcontentloaded");
  await page.waitForLoadState("networkidle", { timeout: 2500 }).catch(() => {});
  await page.evaluate(async () => {
    if (document.fonts?.ready) await document.fonts.ready;
    const pending = [...document.images]
      .filter((image) => !image.complete)
      .map((image) => new Promise((resolve) => {
        image.addEventListener("load", resolve, { once: true });
        image.addEventListener("error", resolve, { once: true });
      }));
    await Promise.race([
      Promise.all(pending),
      new Promise((resolve) => setTimeout(resolve, 2200)),
    ]);
    window.scrollTo(0, 0);
  });
  await sleep(180);
}

async function dismissShowcaseOverlays(page) {
  const announcement = page.locator("#announcement-popup");
  const dismiss = page.locator("#announcement-dismiss");
  if (await dismiss.isVisible().catch(() => false)) {
    await dismiss.click();
    await announcement.waitFor({ state: "detached", timeout: 3000 }).catch(async () => {
      await announcement.waitFor({ state: "hidden", timeout: 1000 }).catch(() => {});
    });
    await sleep(120);
  }
}

async function signInIfNeeded(page) {
  await page.goto(absolute("/"), { waitUntil: "domcontentloaded" });
  const identity = page.locator('input[name="identity"]');
  if (await identity.count()) {
    if (!USERNAME || !PASSWORD) {
      throw new Error(
        "InfoMancer requires a login. Set INFOMANCER_SHOWCASE_USERNAME and " +
        "INFOMANCER_SHOWCASE_PASSWORD for this capture run. The values are not written to disk.",
      );
    }
    await identity.fill(USERNAME);
    await page.locator('input[name="password"]').fill(PASSWORD);
    await Promise.all([
      page.waitForLoadState("domcontentloaded"),
      page.locator("button.auth-submit").click(),
    ]);
  }
  if (new URL(page.url()).pathname.startsWith("/setup")) {
    throw new Error("This InfoMancer instance is still in first-run setup. Complete setup before capturing showcase screenshots.");
  }
  await waitForStableFrame(page);
  await dismissShowcaseOverlays(page);
}

async function prepareLibrary(page) {
  await page.goto(absolute("/library"), { waitUntil: "domcontentloaded" });
  await waitForStableFrame(page);
  await dismissShowcaseOverlays(page);
  const covers = page.locator("#cover-library");
  if (!(await covers.count())) return false;
  return true;
}

async function openInspector(page) {
  if (!(await prepareLibrary(page))) return false;
  const card = page.locator(".cover-card:visible, .library-title-row:visible").first();
  if (!(await card.count())) return false;
  await card.click();
  const inspector = page.locator("#workspace-inspector");
  await inspector.waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
  await page.locator(".workspace-inspector-state.loading").waitFor({ state: "detached", timeout: 5000 }).catch(() => {});
  await waitForStableFrame(page);
  return await inspector.isVisible().catch(() => false);
}

async function openFirstTitle(page) {
  if (!(await prepareLibrary(page))) return false;
  const titleLink = page.locator(".cover-card-link:visible, .title-link:visible").first();
  if (!(await titleLink.count())) return false;
  const href = await titleLink.getAttribute("href");
  if (!href) return false;
  await page.goto(new URL(href, BASE_URL).href, { waitUntil: "domcontentloaded" });
  await waitForStableFrame(page);
  await dismissShowcaseOverlays(page);
  return true;
}

const states = [
  {
    slug: "dashboard",
    label: "Dashboard",
    prepare: async (page) => {
      await page.goto(absolute("/"), { waitUntil: "domcontentloaded" });
      await waitForStableFrame(page);
      await dismissShowcaseOverlays(page);
      return true;
    },
  },
  { slug: "library", label: "Library Covers", prepare: prepareLibrary },
  { slug: "library-inspector", label: "Library Inspector", prepare: openInspector },
  { slug: "title-detail", label: "Title Detail", prepare: openFirstTitle },
  {
    slug: "review",
    label: "Review Workspace",
    prepare: async (page) => {
      await page.goto(absolute("/review"), { waitUntil: "domcontentloaded" });
      await waitForStableFrame(page);
      await dismissShowcaseOverlays(page);
      return true;
    },
  },
].filter((state) => !ONLY.size || ONLY.has(state.slug));

async function capture() {
  if (!variants.length) throw new Error("No screenshot variants selected.");
  if (!Number.isFinite(DEVICE_SCALE) || DEVICE_SCALE <= 0) {
    throw new Error("INFOMANCER_SHOWCASE_SCALE must be a positive number.");
  }
  await mkdir(OUTPUT_DIR, { recursive: true });
  const browser = await chromium.launch({ headless: HEADLESS });
  const manifest = {
    generated_at: new Date().toISOString(),
    base_url: BASE_URL,
    device_scale: DEVICE_SCALE,
    files: [],
    skipped: [],
  };

  try {
    for (const variant of variants) {
      const context = await browser.newContext({
        viewport: { width: variant.width, height: variant.height },
        deviceScaleFactor: DEVICE_SCALE,
        isMobile: variant.isMobile,
        hasTouch: variant.hasTouch,
        colorScheme: "dark",
        reducedMotion: "reduce",
      });
      await context.addCookies([{
        name: "infomancer_library_view",
        value: "covers",
        url: `${BASE_URL}/`,
        httpOnly: false,
        sameSite: "Lax",
      }]);
      const page = await context.newPage();
      page.setDefaultTimeout(8000);
      await signInIfNeeded(page);

      for (let index = 0; index < states.length; index += 1) {
        const state = states[index];
        let ready = false;
        try {
          ready = await state.prepare(page);
        } catch (error) {
          console.warn(`[showcase] ${state.label} could not be prepared: ${error.message}`);
        }
        if (!ready) {
          manifest.skipped.push({ variant: variant.name, state: state.slug });
          continue;
        }

        const filename = `${String(index + 1).padStart(2, "0")}-${state.slug}-${variant.name}.png`;
        const target = path.join(OUTPUT_DIR, filename);
        const screenshotOptions = {
          path: target,
          fullPage: false,
          animations: "disabled",
          caret: "hide",
          scale: "device",
        };
        if (REDACT_SELECTORS) {
          screenshotOptions.mask = [page.locator(REDACT_SELECTORS)];
          screenshotOptions.maskColor = "#101820";
        }
        await page.screenshot(screenshotOptions);
        manifest.files.push({
          filename,
          variant: variant.name,
          state: state.slug,
          width: Math.round(variant.width * DEVICE_SCALE),
          height: Math.round(variant.height * DEVICE_SCALE),
          logical_width: variant.width,
          logical_height: variant.height,
          page: new URL(page.url()).pathname,
        });
        console.log(`[showcase] wrote ${path.relative(REPO_ROOT, target)}`);
      }
      await context.close();
    }
  } finally {
    await browser.close();
  }

  await writeFile(
    path.join(OUTPUT_DIR, "manifest.json"),
    `${JSON.stringify(manifest, null, 2)}\n`,
    "utf8",
  );
  console.log(`[showcase] ${manifest.files.length} screenshot(s) captured at ${DEVICE_SCALE}x scale.`);
  if (manifest.skipped.length) console.log(`[showcase] ${manifest.skipped.length} state(s) skipped because content was unavailable.`);
}

capture().catch((error) => {
  console.error(`[showcase] ${error.stack || error.message || error}`);
  process.exitCode = 1;
});
