import assert from 'node:assert/strict';
import { test } from 'node:test';
import { exchangeScenes as scenes } from '../../lib/exchange-copy.ts';
import { locales } from '../../lib/i18n.ts';

test('each exchange scene uses distinct lines in every locale', () => {
  assert.ok(scenes?.length);
  for (const scene of scenes) {
    for (const locale of locales) {
      const lines = scene.messages.map(([, text]) => text[locale]);
      assert.equal(new Set(lines).size, lines.length, `${scene.app} repeats a line in ${locale}`);
    }
  }
});
