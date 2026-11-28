import { test } from 'node:test';
import assert from 'node:assert/strict';
import { askContext } from '../../lib/ask-context.ts';

const root = { url: '/zh-Hant/docs', root: true, text: 'SwB '.repeat(100) };
const config = { url: '/zh-Hant/docs/config', root: false, text: '連接 '.repeat(100) };
const install = { url: '/zh-Hant/docs/install', root: false, text: '安裝 '.repeat(100) };

test('Ask AI scores terms, the root and the reader location', () => {
  assert.equal(askContext([root, install, config], '如何連接', install.url, 10_000),
    [install.text, config.text, root.text].join('\n\n---\n\n'));
});

test('Ask AI truncates context to the budget and omits short fragments', () => {
  assert.equal(askContext([root, install, config], '', undefined, 625),
    [root.text, install.text.slice(0, 225)].join('\n\n---\n\n'));
  assert.equal(askContext([root, install, config], '', undefined, 550), root.text);
});
