/* 先启动 tests/browser_fixture.py；测试账号与真实学习数据隔离。 */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const base = process.env.CMASTER_QA_URL || 'http://127.0.0.1:8792';
  const out = path.resolve('.tmpcheck');
  fs.mkdirSync(out, {recursive: true});
  const browser = await chromium.launch({headless: true, channel: process.env.CMASTER_BROWSER || 'msedge'});
  const context = await browser.newContext({acceptDownloads: true});
  const page = await context.newPage();
  const errors = [];
  const checks = [];
  const runId = Date.now().toString(36);
  const accountA = 'notes.a-' + runId;
  const accountB = 'notes.b-' + runId;
  const track = target => target.on('pageerror', error => errors.push(error.message));
  track(page);
  const check = (name, condition) => { assert.ok(condition, name); checks.push(name); };
  const login = (ctx, username, register = false) => ctx.request.post(base + '/login', {
    form: {action: register ? 'register' : 'login', username, password: 'isolated-test-pass'}
  });
  const setNote = (target, text) => target.locator('#study-note-text').fill(text);
  const save = async target => {
    const response = target.waitForResponse(r => r.request().method() === 'POST' && r.url().includes('/api/notebook/'));
    await target.locator('#study-note-save').click();
    const result = await response;
    await target.locator('#study-note-form[aria-busy="false"]').waitFor();
    return result;
  };

  await login(context, accountA, true);
  await page.goto(base + '/notebook');
  check('new notebook has an actionable empty state', await page.getByText('从一个值得记住的知识点开始').isVisible());
  await page.goto(base + '/chapter/7/1#my-study-notes');
  check('notebook deep link opens note editor', await page.locator('#my-study-notes').getAttribute('open') !== null);
  await page.locator('#study-bookmark').click();
  await page.locator('#study-bookmark[aria-pressed="true"]:enabled').waitFor();
  check('lesson can be bookmarked', true);
  await setNote(page, '指针保存地址，解引用访问它指向的对象。');
  await page.reload();
  check('unsaved note survives an immediate reload', await page.locator('#study-note-text').inputValue() === '指针保存地址，解引用访问它指向的对象。');
  check('unsaved note is identified clearly', (await page.locator('#study-note-status').textContent()).includes('草稿'));
  await save(page);
  check('note saves to the account', (await page.locator('#study-note-status').textContent()).includes('已保存到账号'));

  const fresh = await browser.newContext();
  await login(fresh, accountA);
  const freshPage = await fresh.newPage();
  track(freshPage);
  await freshPage.goto(base + '/chapter/7/1#my-study-notes');
  check('fresh browser restores saved note', (await freshPage.locator('#study-note-text').inputValue()).includes('指针保存地址'));
  check('fresh browser restores bookmark', await freshPage.locator('#study-bookmark').getAttribute('aria-pressed') === 'true');
  await fresh.close();

  await page.goto(base + '/problem/c001#my-study-notes');
  const codeBefore = await page.evaluate(() => document.querySelector('.CodeMirror').CodeMirror.getValue());
  let codeSubmits = 0;
  page.on('request', request => { if (request.url().endsWith('/api/submit')) codeSubmits++; });
  await setNote(page, '输出格式要一致，别忘了换行。\n第二次复习先自己写。');
  const keyboardSave = page.waitForResponse(r => r.request().method() === 'POST' && r.url().includes('/api/notebook/'));
  await page.locator('#study-note-text').press('Control+s');
  await keyboardSave;
  await page.locator('#study-note-form[aria-busy="false"]').waitFor();
  check('Ctrl+S in note saves without submitting code', codeSubmits === 0);
  check('problem note leaves editor code intact', await page.evaluate(() => document.querySelector('.CodeMirror').CodeMirror.getValue()) === codeBefore);
  await page.locator('#study-bookmark').click();
  await page.locator('#study-bookmark[aria-pressed="true"]:enabled').waitFor();

  await page.goto(base + '/notebook');
  check('lesson and problem appear together in notebook', await page.locator('[data-notebook-ref]').count() === 2);
  await page.locator('#notebook-query').fill('指针');
  await page.locator('#notebook-kind').selectOption('lesson');
  await page.locator('#notebook-view').selectOption('notes');
  await page.locator('#notebook-chapter').selectOption('7');
  await Promise.all([page.waitForURL('**/notebook?*'), page.getByRole('button', {name: '筛选', exact: true}).click()]);
  check('combined filters find matching note', await page.locator('[data-notebook-ref="lesson:7:1"]').count() === 1 && await page.locator('[data-notebook-ref]').count() === 1);
  await page.goto(base + '/notebook?q=no-such-note');
  check('empty search keeps a clear recovery path', await page.getByText('没有找到符合条件的资料').isVisible());
  await page.getByRole('link', {name: '查看全部资料', exact: true}).click();
  await page.waitForURL(base + '/notebook');

  for (const [label, suffix] of [['导出笔记 .md', 'md'], ['导出学习记录 .json', 'json']]) {
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('link', {name: label, exact: true}).click();
    const download = await downloadPromise;
    check(`${suffix} download completes`, await download.failure() === null);
    const output = path.join(out, 'notebook-export.' + suffix);
    await download.saveAs(output);
    const text = fs.readFileSync(output, 'utf8');
    check(`${suffix} export contains saved note`, text.includes('指针保存地址'));
    if (suffix === 'json') check('progress export has versioned schema', JSON.parse(text).schema === 'cmaster.progress');
    check(`${suffix} download leaves page usable`, !(await page.locator('body').getAttribute('class') || '').includes('cm-leaving'));
  }

  await page.goto(base + '/chapter/7/1#my-study-notes');
  const stale = await context.newPage();
  track(stale);
  await stale.goto(base + '/chapter/7/1#my-study-notes');
  await setNote(stale, '另一标签页尚未提交的独立草稿。');
  await setNote(page, '主页面保存的新理解：指针类型决定解引用方式。');
  await save(page);
  const conflictReply = await save(stale);
  check('outdated tab receives a version conflict instead of overwriting', conflictReply.status() === 409);
  check('version conflict retains the unsaved note in editor', await stale.locator('#study-note-text').inputValue() === '另一标签页尚未提交的独立草稿。');
  await stale.reload();
  check('per-tab draft survives another tab saving', await stale.locator('#study-note-text').inputValue() === '另一标签页尚未提交的独立草稿。');
  check('outdated local draft exposes conflict before overwriting', await stale.locator('#study-note-conflict').isVisible());
  check('conflict shows server note as plain text', (await stale.locator('#study-note-server').textContent()).includes('主页面保存的新理解'));
  await stale.locator('#study-note-load').click();
  check('saved version can be loaded', (await stale.locator('#study-note-text').inputValue()).includes('主页面保存的新理解'));
  await stale.locator('#study-note-undo').click();
  check('loading server note can be undone', await stale.locator('#study-note-text').inputValue() === '另一标签页尚未提交的独立草稿。');
  const overwriteResponse = stale.waitForResponse(r => r.request().method() === 'POST' && r.url().includes('/api/notebook/'));
  await stale.locator('#study-note-overwrite').click();
  await overwriteResponse;
  await stale.locator('#study-note-form[aria-busy="false"]').waitFor();
  check('explicit conflict choice saves the chosen draft', await stale.locator('#study-note-conflict').isHidden());
  await stale.close();
  await page.reload();
  check('chosen version is visible after reloading another tab', (await page.locator('#study-note-text').inputValue()).includes('独立草稿'));

  const oldAccountPage = await context.newPage();
  track(oldAccountPage);
  await oldAccountPage.goto(base + '/problem/c001#my-study-notes');
  await context.request.get(base + '/logout');
  await login(context, accountB, true);
  await setNote(oldAccountPage, 'A 账号旧页面的新草稿');
  const staleReply = await save(oldAccountPage);
  check('stale account note save is rejected', staleReply.status() === 409);
  check('stale account retains its draft', await oldAccountPage.locator('#study-note-text').inputValue() === 'A 账号旧页面的新草稿');
  await page.goto(base + '/notebook');
  check('second account sees its own empty notebook', await page.locator('[data-notebook-ref]').count() === 0);
  await page.goto(base + '/problem/c001#my-study-notes');
  check('second account does not see first account draft', await page.locator('#study-note-text').inputValue() === '');
  await oldAccountPage.close();
  await context.request.get(base + '/logout');
  await login(context, accountA);
  await page.goto(base + '/chapter/1/1#my-study-notes');
  await setNote(page, '断网时也要保留的草稿。');
  await page.route('**/api/notebook/**', route => route.abort());
  await page.locator('#study-note-save').click();
  await page.getByText('暂时无法保存到账号，本地草稿已保留，可以重试。', {exact:true}).waitFor();
  await page.unroute('**/api/notebook/**');
  await page.reload();
  check('network failure preserves draft through refresh', await page.locator('#study-note-text').inputValue() === '断网时也要保留的草稿。');
  await page.goto(base + '/notebook');

  for (const width of [320, 390, 768, 1024, 1440]) {
    await page.setViewportSize({width, height: 1000});
    for (const route of ['/notebook', '/chapter/7/1#my-study-notes', '/problem/c001#my-study-notes']) {
      await page.goto(base + route, {waitUntil: 'networkidle'});
      check(`${width}px ${route} has no page overflow`, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      if (route === '/notebook' && (width === 390 || width === 1440)) {
        await page.screenshot({path: path.join(out, `notebook-${width}.png`), fullPage: true});
      }
    }
  }
  check('no JavaScript errors', errors.length === 0);
  fs.writeFileSync(path.join(out, 'notebook-browser.json'), JSON.stringify({checks, errors}, null, 2));
  console.log(JSON.stringify({passed: checks.length, errors}, null, 2));
  await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
