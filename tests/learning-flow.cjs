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
  check('project journey is the first navigation entry', await page.locator('.nav a').first().textContent() === '项目闯关');
  check('home names today project task', (await page.locator('.project-home-copy h1').textContent()).includes('今天写第 1 天'));
  check('continue learning returns to last lesson', await page.locator('#continue-learning').getAttribute('href') === '/chapter/7/2');
  check('three learning tasks are visible', await page.locator('[data-study]').count() === 3);

  await page.goto(base + '/journey/day/1', {waitUntil: 'networkidle'});
  check('journey workbench loads its local editor', await page.locator('.journey-editor-pane .CodeMirror').isVisible());
  check('journey starts with one attempt step', (await page.locator('#journey-attempts').textContent()).trim() === '0');
  await page.locator('#journey-next-hint').click();
  await page.locator('#journey-next-hint').click();
  check('journey hints unlock in stages', await page.locator('#journey-hints li:not([hidden])').count() === 2);
  check('journey hint control locks after reveal', await page.locator('#journey-next-hint').isDisabled());

  await setCode('/* journey WA */');
  await page.locator('#journey-submit').click();
  await page.locator('.verdict.WA').waitFor();
  await page.waitForFunction(() => !document.querySelector('#journey-submit').disabled);
  check('journey WA keeps the day open', await page.locator('#journey-completion-actions').isHidden());
  check('journey WA counts one real attempt', (await page.locator('#journey-attempts').textContent()).trim() === '1');

  const journeyAccepted = '/* QA_ACCEPT first delivery */';
  await setCode(journeyAccepted);
  await page.locator('#journey-submit').click();
  await page.locator('.verdict.AC').waitFor();
  await page.waitForFunction(() => !document.querySelector('#journey-submit').disabled);
  check('journey AC opens the next delivery', await page.locator('#journey-completion-actions').isVisible());
  check('journey AC points to the next day', await page.locator('#journey-next-link').getAttribute('href') === '/journey/day/2');
  check('journey AC updates the day state', (await page.locator('.journey-day-state').textContent()).includes('已交付'));
  await page.reload({waitUntil: 'networkidle'});
  check('journey completion survives reload', (await page.locator('.journey-day-state').textContent()).includes('已交付'));
  check('journey accepted draft survives reload', await getCode() === journeyAccepted);

  await page.goto(base + '/journey/day/2', {waitUntil: 'networkidle'});
  check('journey AC unlocks the following workbench', await page.locator('.journey-editor-pane').isVisible());
  await setCode('/* QA_SYSTEM journey outage */');
  await page.locator('#journey-submit').click();
  await page.locator('.verdict.SE').waitFor();
  await page.waitForFunction(() => !document.querySelector('#journey-submit').disabled);
  check('journey system failure does not count an attempt', (await page.locator('#journey-attempts').textContent()).trim() === '0');
  check('journey system failure keeps the day open', await page.locator('#journey-completion-actions').isHidden());
  await page.goto(base + '/');
  check('home advances to the next project day', (await page.locator('.project-home-copy h1').textContent()).includes('第 2 天'));

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

  const journeyGuestContext = await browser.newContext();
  const journeyGuestPage = await journeyGuestContext.newPage();
  await journeyGuestPage.goto(base + '/journey/day/1', {waitUntil: 'networkidle'});
  const journeyGuestSetCode = code => journeyGuestPage.evaluate(
    value => document.querySelector('.CodeMirror').CodeMirror.setValue(value),
    code
  );
  const journeyGuestCode = () => journeyGuestPage.evaluate(
    () => document.querySelector('.CodeMirror').CodeMirror.getValue()
  );
  await journeyGuestSetCode('/* guest visible journey run */');
  await journeyGuestPage.locator('#journey-run').click();
  await journeyGuestPage.locator('.verdict.WA').waitFor();
  await journeyGuestPage.waitForFunction(() => !document.querySelector('#journey-run').disabled);
  check('journey guests can run visible cases', await journeyGuestPage.locator('#journey-result .verdict').isVisible());
  check('journey guest run hides boundary cases', !(await journeyGuestPage.locator('#journey-result').textContent()).includes('隐藏用例'));
  const guestJourneyDraft = '/* QA_ACCEPT guest draft before login */';
  await journeyGuestSetCode(guestJourneyDraft);
  await journeyGuestPage.locator('#journey-submit').click();
  await journeyGuestPage.waitForURL('**/login?next=*');
  await journeyGuestContext.request.post(base + '/login', {
    form: {action: 'register', username: 'journey-guest', password: 'isolated-test-pass'}
  });
  await journeyGuestPage.goto(base + '/journey/day/1', {waitUntil: 'networkidle'});
  check('journey guest draft survives the sign-in flow', await journeyGuestCode() === guestJourneyDraft);
  await journeyGuestContext.close();

  await page.goto(base + '/labs', {waitUntil: 'networkidle'});
  check('lab hub lists all fixes', await page.locator('.lab-card').count() === 6);
  check('lab hub is in the primary navigation', await page.locator('.nav a.on').textContent() === '实验室');
  await page.goto(base + '/lab/loop-boundary', {waitUntil: 'networkidle'});
  const labStarter = await getCode();
  await page.locator('#lab-next-hint').click();
  await page.locator('#lab-next-hint').click();
  check('lab hints unlock in stages', await page.locator('#lab-hints li:not([hidden])').count() === 2);
  check('lab hint control locks after reveal', await page.locator('#lab-next-hint').isDisabled());
  const multiLineLabEdit = labStarter.replace(
    '    return 0;\n}',
    '    /* fixture extra one */\n    /* fixture extra two */\n    return 0;\n}'
  );
  await setCode(multiLineLabEdit);
  await page.locator('#btn-submit').click();
  await page.locator('.lab-restriction-error').waitFor();
  check('server rejects multi-line lab edits', (await page.locator('.lab-restriction-error').textContent()).includes('改动超预算'));
  const fixedLab = labStarter.replace('i < n', 'i <= n /* QA_ACCEPT */');
  await setCode(fixedLab);
  await page.locator('#btn-submit').click();
  await page.locator('.verdict.AC').waitFor();
  await page.waitForFunction(() => !document.querySelector('#btn-submit').disabled);
  check('one-line lab fix passes hidden cases', await page.locator('.lab-complete-note').isVisible());
  const labResultText = await page.locator('#lab-result').textContent();
  check('hidden lab inputs stay server-side', !labResultText.includes('10\n') && !labResultText.includes('5050'));
  await page.reload();
  check('accepted lab state survives reload', await page.locator('#lab-record').textContent().then(text => text.includes('已修复')));
  check('accepted lab code is restored', await getCode() === fixedLab);

  await setCode('/* account A private lab draft */');
  await page.goto(base + '/logout');
  await register('browser-lab?b');
  await page.goto(base + '/lab/loop-boundary');
  check('lab drafts isolate account A from account B', !(await getCode()).includes('account A private lab draft'));
  await setCode('/* account B private lab draft */');
  await page.goto(base + '/logout');
  await context.request.post(base + '/login', {form: {username: 'browser.a', password: 'isolated-test-pass'}});
  await page.goto(base + '/lab/loop-boundary');
  check('lab draft returns with its owning account', await getCode() === '/* account A private lab draft */');

  await page.goto(base + '/training');
  await page.locator('#tr-loading').waitFor({state: 'hidden'});
  check('training card renders after fresh checkout', await page.locator('#tr-stage').textContent() !== '');

  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const route of ['/', '/journey', '/journey/day/1', '/journey/day/7',
                         '/problems', '/problem/c001', '/labs', '/lab/loop-boundary', '/training']) {
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
