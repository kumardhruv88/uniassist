// Screenshots for docs/UNIASSIST_EXPLAINED.md (headless). BASE_URL defaults to the main demo UI.
import path from 'node:path'
import { chromium } from 'playwright-core'
const BASE = process.env.BASE_URL ?? 'http://127.0.0.1:5173'
const OUT = path.resolve('../docs/explained')
const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 }, deviceScaleFactor: 2 })
await page.goto(BASE)
await page.locator('#rail-student option[value="S1002"]').waitFor({ state: 'attached', timeout: 20000 })
await page.selectOption('#rail-student', 'S1002')
const box = page.getByLabel('Your question')
const done = page.waitForResponse((r) => r.url().endsWith('/ask') && r.request().method() === 'POST', { timeout: 180000 })
await box.fill('Am I eligible for the CS201 end-semester exam?')
await box.press('Enter')
await done
await page.locator('article.file .stamp-word').first().waitFor()
await page.waitForTimeout(1200)
await page.locator('article.file').first().screenshot({ path: path.join(OUT, 'answer-calculated.png') })
const ledger = page.getByText('How this was decided').first()
const box2 = await page.locator('article.file').first().boundingBox()
const lb = await ledger.boundingBox()
await page.screenshot({ path: path.join(OUT, 'how-decided.png'), clip: { x: box2.x, y: lb.y - 8, width: box2.width, height: 488 }, fullPage: true })
console.log('ok')
await browser.close()
