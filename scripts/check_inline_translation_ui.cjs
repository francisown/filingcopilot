// Contract check for the on-demand answer translation overlay.
const assert = require('node:assert/strict');
const fs = require('node:fs');

const app = fs.readFileSync('src/filingcopilot/static/app.js', 'utf8');
const css = fs.readFileSync('src/filingcopilot/static/style.css', 'utf8');

assert.match(app, /function attachInlineTranslation\(/);
assert.match(app, /attachInlineTranslation\(root,result,head\)/);
assert.match(app, /api\('\/api\/translate'/);
assert.match(app, /target:'zh'/);
assert.match(app, /inline-translation-panel/);
assert.match(app, /dataset\.i18nSkip='true'/);
assert.match(app, /result\.language==='zh'/);
assert.match(css, /\.inline-translation-panel\{position:fixed/);
assert.match(css, /@media\(max-width:700px\)\{\.inline-translation-panel/);
assert.doesNotMatch(app, /FilingI18n\.setLanguage[\s\S]{0,100}attachInlineTranslation/);

console.log(JSON.stringify({
  scope: 'static UI contract; no provider or model call',
  checks: [
    'translation action is attached to rendered results',
    'translation uses the on-demand /api/translate endpoint',
    'panel is marked outside the global i18n pass',
    'Chinese-origin results do not get a redundant action',
    'panel is fixed and responsive',
  ],
}));
