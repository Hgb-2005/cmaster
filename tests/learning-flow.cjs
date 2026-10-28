/* NODE_PATH 指向已安装的 Playwright；先启动 tests/browser_fixture.py。 */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const base = process.env.CMASTER_QA_URL || 'http://127.0.0.1:8792';
  const browser = await chromium.launch({headless: true, channel: process.env.CMASTER_BROWSER || 'msedge'});
  const context = await browser.newContext();
  const errors = [];
  const checks = [];
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  const check = (name, condition) => { assert.ok(condition, name); checks.push(name); };
  const register = async name => {
    const response = await context.request.post(base + '/login', {
      form: { action: 'register', username: name, password: 'isolated-test-pass' }
    });
    assert.equal(response.status(), 200);
  };
  const getCode = () => page.evaluate(() => document.querySelector('.CodeMirror').CodeMirror.getValue());
  const setCode = code => page.evaluate(value => document.querySelector('.CodeMirror').CodeMirror.setValue(value), code);
  const submit = async (code, verdict) => {
    await setCode(code);
    await page.locator('#btn-submit').click();
    await page.locator('.verdict.' + verdict).waitFor();
    await page.waitForFunction(() => !document.querySelector('#btn-submit').disabled);
  };
  const out = path.resolve('.tmpcheck');
  fs.mkdirSync(out, { recursive: true });

  await register('browser.a');
  await page.goto(base + '/chapter/7/2');
  await page.goto(base + '/');
  check('continue learning returns to last lesson', await page.locator('#continue-learning').getAttribute('href') === '/chapter/7/2');
  check('three learning tasks are visible', await page.locator('[data-study]').count() === 3);
  const related = await page.locator('[data-study="practice"]').getAttribute('href');
  await page.goto(base + related);
  check('practice is from the same chapter', (await page.locator('.crumb').textContent()).includes('第 7 章'));

  await page.goto(base + '/problem/c001');
  const accepted = '#include <stdio.h>\nint main(void) { puts("Hello, C"); return 0; }\n/* QA_ACCEPT own solution */';
  await submit(accepted, 'AC');
  check('accepted solution can be restored', await page.locator('#restore-accepted').isVisible());
  await page.reload();
  check('accepted code survives reload', await getCode() === accepted);
  const draft = '/* a later draft without a verdict marker */';
  await setCode(draft);
  await page.reload();
  check('immediate reload preserves latest input', await getCode() === draft);
  await page.locator('#restore-accepted').click();
  check('restore accepted brings back own solution', await getCode() === accepted);
  await page.locator('#undo-restore').click();
  check('undo restoration recovers current draft', await getCode() === draft);
  await submit('/* another attempt */', 'WA');
  check('history has two submissions', await page.locator('#submission-history option').count() === 2);
  await page.locator('#submission-history').selectOption({index: 1});
  await page.locator('#restore-version').click();
  check('older history entry restores exact code', await getCode() === accepted);
  await page.locator('#undo-restore').click();
  await submit('/* QA_SYSTEM preserved during outage */', 'SE');
  check('system error offers retry', await page.locator('#retry-submit').isVisible());
  check('system error explains statistics', (await page.locator('#result-area').textContent()).includes('未计入尝试次数'));
  await setCode('/* account A private draft */');
  await page.goto(base + '/logout');
  await register('browser?a');
  await page.goto(base + '/problem/c001');
  check('switching account isolates code', !(await getCode()).includes('account A private draft'));
  check('switching account isolates history', await page.locator('#submission-controls').isHidden());
  await setCode('/* account B private draft */');
  await page.goto(base + '/logout');
  await context.request.post(base + '/login', {form: {username: 'browser.a', password: 'isolated-test-pass'}});
  await page.goto(base + '/problem/c001');
  check('switching back restores the original draft', await getCode() === '/* account A private draft */');

  const fresh = await browser.newContext();
  await fresh.request.post(base + '/login', {form: {username: 'browser.a', password: 'isolated-test-pass'}});
  const freshPage = await fresh.newPage();
  await freshPage.goto(base + '/problem/c001');
  const serverCode = await freshPage.evaluate(() => document.querySelector('.CodeMirror').CodeMirror.getValue());
  check('new browser recovers last submitted code from server', serverCode.includes('QA_SYSTEM'));
  check('new browser still offers accepted solution', await freshPage.locator('#restore-accepted').isVisible());
  await fresh.close();

  const guestContext = await browser.newContext();
  const guestPage = await guestContext.newPage();
  await guestPage.goto(base + '/problem/c001');
  await guestPage.evaluate(() => document.querySelector('.CodeMirror').CodeMirror.setValue('/* guest draft before signing in */'));
  await guestPage.locator('#btn-submit').click();
  await guestPage.waitForURL('**/login?next=*');
  await guestContext.request.post(base + '/login', {form: {username: 'browser.a', password: 'isolated-test-pass'}});
  await guestPage.goto(base + '/problem/c001');
  check('guest draft survives the sign-in flow', await guestPage.evaluate(() => document.querySelector('.CodeMirror').CodeMirror.getValue()) === '/* guest draft before signing in */');
  await guestContext.close();

  await page.goto(base + '/training');
  await page.locator('#tr-loading').waitFor({state: 'hidden'});
  check('training card renders after fresh checkout', await page.locator('#tr-stage').textContent() !== '');

  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const route of ['/', '/problem/c001', '/training']) {
      await page.goto(base + route, {waitUntil: 'networkidle'});
      check(`${width}px ${route} has no page overflow`, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      if (route === '/') check(`${width}px learning tasks remain visible`, await page.locator('[data-study="review"]').isVisible());
      if (width === 390 || width === 1440) {
        await page.screenshot({path: path.join(out, `learning-${width}-${route.replaceAll('/', '_')}.png`), fullPage: true});
      }
    }
  }
  await page.emulateMedia({reducedMotion: 'reduce'});
  await page.goto(base + '/');
  await page.locator('[data-study="learn"]').focus();
  check('keyboard navigation has a visible focus outline', await page.locator('[data-study="learn"]').evaluate(el => getComputedStyle(el).outlineStyle !== 'none'));
  check('no JavaScript errors', errors.length === 0);
  fs.writeFileSync(path.join(out, 'learning-flow-browser.json'), JSON.stringify({checks, errors, judge: 'controlled fixture'}, null, 2));
  console.log(JSON.stringify({passed: checks.length, errors}, null, 2));
  await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
