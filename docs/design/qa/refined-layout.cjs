const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const { chromium } = require('playwright');
const sha = p => crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
(async () => {
  const output = process.env.FLYJI_LAYOUT_OUTPUT;
  if (!output) throw new Error('Set FLYJI_LAYOUT_OUTPUT to a new result file');
  const versions = [
    { name: 'R04c', source: 'docs/design/flyji-refresh.html', preview: '/tmp/flyji-refined-r04-preview.html' },
    { name: 'R05a', source: 'docs/design/qa/revisions/refined-r05a.html', preview: '/tmp/flyji-refined-r05a-preview.html' },
    { name: 'R05b', source: 'docs/design/qa/revisions/refined-r05b.html', preview: '/tmp/flyji-refined-r05b-preview.html' },
    { name: 'R05c', source: 'docs/design/flyji-refined.html', preview: '/tmp/flyji-refined-preview.html' }
  ];
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const results = [];
  for (const version of versions) {
    for (const width of [320, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 } });
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.goto('file://' + version.preview);
      const frame = page.frames().find(f => f !== page.mainFrame());
      await frame.waitForSelector('.viz-carousel-controls', { timeout: 90000 });
      await frame.evaluate(() => document.fonts.ready);
      for (const [direction, label] of [['air', 'Airlight'], ['canvas', 'Journey canvas'], ['orbit', 'Orbit studio']]) {
        for (let i = 0; i < 3 && await frame.locator('.viz-carousel').getAttribute('data-active-variant') !== label; i++) {
          await frame.locator('.viz-carousel-next').focus(); await page.keyboard.press('Enter');
          await page.waitForTimeout(200);
        }
        await page.waitForTimeout(250);
        const geometry = await frame.locator(`.fr-app[data-direction=${direction}]`).evaluate(app => {
          const bounds = app.getBoundingClientRect(), box = selector => {
            const node = app.querySelector(selector); if (!node) return null;
            const r = node.getBoundingClientRect(); return { top: r.top - bounds.top, height: r.height };
          };
          const active = app.dataset.mobileView === 'time' ? '.fr-time-chart' : '.fr-map-stage';
          return {
            actual_app_width_px: bounds.width,
            shared_stage_height_px: bounds.height,
            content_bottom_px: app.querySelector('.fr-body').getBoundingClientRect().bottom - bounds.top,
            active_view: app.dataset.mobileView,
            first_graphic: box(active),
            local_day_summary: box('.fr-detail'),
            departure_days: box('.fr-week')
          };
        });
        results.push({ version: version.name, source_sha256: sha(version.source), viewport_width_px: width, direction, ...geometry });
      }
      await page.close();
    }
  }
  const result = {
    date: '2026-10-13', browser: await browser.version(), headless: true,
    script_sha256: sha(__filename), results,
    scope: 'Default fixture and mobile views, native carousel activation; CSS pixel distances within each application. Preview gutter reduces actual application width. Stage includes shared carousel height; content bottom excludes footer/remaining stage whitespace.',
    limitation: 'Author layout geometry, not observed scrolling, task effort, preference, accessibility conformance, performance or user behavior.'
  };
  fs.writeFileSync(path.resolve(output), JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify(results.map(r => ({ version: r.version, viewport: r.viewport_width_px, direction: r.direction, content_bottom_px: r.content_bottom_px, first_graphic_top_px: r.first_graphic.top, summary_height_px: r.local_day_summary.height }))));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
