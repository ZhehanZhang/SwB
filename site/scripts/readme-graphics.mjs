import { readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const site = join(dirname(fileURLToPath(import.meta.url)), '..');
const out = join(site, '..', 'docs', 'assets');
const require = createRequire(join(site, 'package.json'));
const fontkit = require('fontkit');

const css = readFileSync(join(site, 'app', 'console-tokens.css'), 'utf8') + readFileSync(join(site, 'app', 'global.css'), 'utf8');
const block = (selector) => {
  const body = css.match(new RegExp('(?:^|\\n)' + selector.replaceAll('.', '\\.') + '\\s*\\{([^}]*)\\}'))[1];
  return Object.fromEntries([...body.matchAll(/--([\w-]+):\s*([^;]+);/g)].map(([, k, v]) => [k, v.trim()]));
};
const light = { ...block(':root'), ...block('.swb-board') };
const dark = { ...light, ...block('.dark'), ...block('.dark .swb-board') };
const palette = (t) => ({
  background: t.background,
  muted: t['surface-sunken'],
  surface: t.surface,
  border: t.border,
  foreground: t.foreground,
  mutedForeground: t['muted-foreground'],
  subtle: t['subtle-foreground'],
  spoke: t['swb-spoke'],
  spokeActive: t['swb-spoke-active'],
  slack: t['logo-slack'],
});
const RADIUS = { md: 6, lg: 8 };
const STROKE = 1.5;

const font = (pkg, file) => fontkit.create(readFileSync(require.resolve('@fontsource-variable/' + pkg + '/files/' + file)));
const faces = {
  sans: font('inter', 'inter-latin-wght-normal.woff2'),
  display: font('inter-tight', 'inter-tight-latin-wght-normal.woff2'),
  mono: font('jetbrains-mono', 'jetbrains-mono-latin-wght-normal.woff2'),
};
const instances = new Map();
const face = (family, weight) => {
  const key = family + weight;
  if (!instances.has(key)) instances.set(key, faces[family].getVariation({ wght: weight }));
  return instances.get(key);
};

const n = (v) => +v.toFixed(2);
function shape(text, { size, family = 'sans', weight = 400, tracking = 0 }) {
  const f = face(family, weight);
  const run = f.layout(text);
  const s = size / f.unitsPerEm;
  let pen = 0;
  const glyphs = run.glyphs.map((glyph, i) => {
    const at = pen + run.positions[i].xOffset * s;
    pen += run.positions[i].xAdvance * s + tracking * size;
    return { glyph, at };
  });
  return { glyphs, width: pen - tracking * size, s };
}
const measure = (text, style) => shape(text, style).width;
const COMMANDS = { moveTo: 'M', lineTo: 'L', quadraticCurveTo: 'Q', bezierCurveTo: 'C', closePath: 'Z' };
function text(str, x, y, { fill, anchor = 'start', ...style }) {
  const { glyphs, width, s } = shape(str, style);
  const x0 = anchor === 'middle' ? x - width / 2 : anchor === 'end' ? x - width : x;
  let d = '';
  for (const { glyph, at } of glyphs) {
    for (const { command, args } of glyph.path.commands) {
      const p = [];
      for (let i = 0; i < args.length; i += 2) p.push(n(x0 + at + args[i] * s), n(y - args[i + 1] * s));
      d += COMMANDS[command] + p.join(' ');
    }
  }
  return '<path fill="' + fill + '" d="' + d + '"/>';
}

const asset = (path) => readFileSync(join(site, 'public', path));
const inner = (svg) => svg.replace(/^<svg[^>]*>|<\/svg>\s*$/g, '').replace(/<title>.*?<\/title>/, '');
const avatars = {
  claude: 'avatars/claude.png', dots: 'avatars/dots.png', grok: 'avatars/grok.png', 'grok-bot': 'avatars/grok-bot.png',
  muse: 'avatars/muse.png', 'claude-code': 'avatars/claude-code.png', codex: 'avatars/codex.png', capy: 'marks/captain.webp',
};
const glyphColor = { slack: 'slack', github: 'foreground' };

function canvas(width, height, c) {
  const defs = new Map();
  const use = (id, body) => {
    if (!defs.has(id)) defs.set(id, body);
    return '#' + id;
  };
  const tile = (kind, x, y, size) => {
    const file = avatars[kind];
    if (file) {
      const href = use('a-' + kind, '<symbol id="a-' + kind + '" viewBox="0 0 1 1"><clipPath id="c-' + kind + '"><circle cx=".5" cy=".5" r=".5"/></clipPath><image href="data:image/' + file.split('.').pop() + ';base64,' + asset(file).toString('base64') + '" width="1" height="1" clip-path="url(#c-' + kind + ')" preserveAspectRatio="xMidYMid slice"/></symbol>');
      return '<use href="' + href + '" x="' + x + '" y="' + y + '" width="' + size + '" height="' + size + '"/><circle cx="' + (x + size / 2) + '" cy="' + (y + size / 2) + '" r="' + (size / 2 - 0.5) + '" stroke="' + c.border + '"/>';
    }
    const svg = asset('marks/' + kind + '.svg').toString().replace('currentColor', c[glyphColor[kind]]);
    const href = use('m-' + kind, '<symbol id="m-' + kind + '" viewBox="0 0 24 24">' + inner(svg) + '</symbol>');
    const g = size * 0.62;
    const r = size >= 48 ? 10 : size >= 36 ? RADIUS.lg : RADIUS.md;
    return '<rect x="' + (x + 0.5) + '" y="' + (y + 0.5) + '" width="' + (size - 1) + '" height="' + (size - 1) + '" rx="' + r + '" fill="' + c.surface + '" stroke="' + c.border + '"/><use href="' + href + '" x="' + n(x + (size - g) / 2) + '" y="' + n(y + (size - g) / 2) + '" width="' + n(g) + '" height="' + n(g) + '"/>';
  };
  const mark = (x, y, size, mode) => {
    const href = use('swb-' + mode, '<symbol id="swb-' + mode + '" viewBox="0 0 32 32">' + inner(asset(mode === 'dark' ? 'brand/swb-reversed.svg' : 'brand/swb.svg').toString()) + '</symbol>');
    return '<use href="' + href + '" x="' + x + '" y="' + y + '" width="' + size + '" height="' + size + '"/>';
  };
  const render = (title, body) => '<svg xmlns="http://www.w3.org/2000/svg" width="' + width + '" height="' + height + '" viewBox="0 0 ' + width + ' ' + height + '" fill="none" role="img"><title>' + title + '</title><defs>' + [...defs.values()].join('') + '</defs><rect x=".5" y=".5" width="' + (width - 1) + '" height="' + (height - 1) + '" rx="' + RADIUS.lg + '" fill="' + c.background + '" stroke="' + c.border + '"/>' + body + '</svg>\n';
  return { tile, mark, render };
}

const line = (d, stroke, width = STROKE) => '<path d="' + d + '" stroke="' + stroke + '" stroke-width="' + width + '" stroke-linecap="round" stroke-linejoin="round"/>';
const dot = (x, y, r, fill) => '<circle cx="' + n(x) + '" cy="' + n(y) + '" r="' + r + '" fill="' + fill + '"/>';
const lucide = {
  plug: ['M12 22v-5', 'M9 8V2', 'M15 8V2', 'M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z'],
  terminal: ['m4 17 6-6-6-6', 'M12 19h8'],
  send: ['M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z', 'm21.854 2.147-10.94 10.939'],
};
const icon = (name, x, y, size, stroke) => '<g transform="translate(' + x + ' ' + y + ') scale(' + n(size / 24) + ')">' + lucide[name].map((d) => line(d, stroke)).join('') + '</g>';

function hero(c, mode) {
  const W = 1280, H = 360;
  const { tile, mark, render } = canvas(W, H, c);
  let b = mark(96, 100, 76, mode);
  b += text('SwB', 196, 166, { size: 80, family: 'display', weight: 800, tracking: -0.025, fill: c.foreground });
  b += text('First-party agents working together', 96, 252, { size: 28, weight: 500, tracking: -0.01, fill: c.foreground });
  const size = 44, gap = 12, inset = 32, pw = 8 * size + 7 * gap + 2 * inset, ph = 200, px = W - 96 - pw, py = (H - ph) / 2;
  b += '<rect x="' + px + '" y="' + py + '" width="' + pw + '" height="' + ph + '" rx="' + RADIUS.lg + '" fill="' + c.muted + '"/>';
  ['slack', 'discord', 'telegram', 'github'].forEach((k, i) => { b += tile(k, px + inset + i * (size + gap), py + 32, size); });
  b += line('M' + (px + inset) + ' ' + (py + ph / 2) + 'H' + (px + pw - inset), c.border, 1);
  ['claude', 'dots', 'capy', 'grok', 'grok-bot', 'muse', 'claude-code', 'codex'].forEach((k, i) => { b += tile(k, px + inset + i * (size + gap), py + ph - 32 - size, size); });
  return render('SwB. First-party agents working together.', b);
}

function connected(c) {
  const W = 1280, H = 520, cy = H / 2;
  const { tile, render } = canvas(W, H, c);
  let b = '';
  const channels = [['slack', '#launch'], ['discord', '#general'], ['telegram', 'Team chat'], ['github', '3 repos']];
  const step = 64, c0 = cy - (step * (channels.length - 1)) / 2;
  const leftBus = 408, card = { x: 472, y: cy - 104, w: 336, h: 208 }, rightBus = 872;
  channels.forEach(([k, label], i) => {
    const y = c0 + i * step;
    b += tile(k, 96, y - 18, 36);
    b += text(label, 148, y + 5, { size: 15, weight: 500, fill: c.foreground });
    b += line('M280 ' + y + 'H' + leftBus, c.spoke) + dot(280, y, 3, c.spokeActive);
  });
  b += line('M' + leftBus + ' ' + c0 + 'V' + (c0 + step * (channels.length - 1)), c.spoke);
  b += line('M' + leftBus + ' ' + cy + 'H' + card.x, c.spoke);
  b += '<rect x="' + card.x + '" y="' + card.y + '" width="' + card.w + '" height="' + card.h + '" rx="' + RADIUS.lg + '" fill="' + c.muted + '"/>';
  b += text('Launch site', card.x + 24, card.y + 40, { size: 18, weight: 600, fill: c.foreground });
  b += text('launch-site', card.x + 24, card.y + 62, { size: 12, family: 'mono', fill: c.subtle });
  [['Trust', 'Owner acts'], ['Turn cap', '8'], ['Memory', '31 notes'], ['Open', '4 conversations']].forEach(([k, v], i) => {
    const y = card.y + 104 + i * 28;
    b += text(k, card.x + 24, y, { size: 14, fill: c.mutedForeground });
    b += text(v, card.x + card.w - 24, y, { size: 14, weight: 500, fill: c.foreground, anchor: 'end' });
  });
  const agents = [
    ['claude', 'research', 'Claude Project'], ['dots', 'dots', 'ChatGPT dots'], ['capy', 'Atlas', 'Capy Captain'],
    ['grok', 'grok', 'Grok'], ['grok-bot', 'scout', 'Grok Bot'], ['muse', 'muse', 'Muse'],
    ['claude-code', 'Claude Code', 'Session'], ['codex', 'Codex', 'Session'],
  ];
  const astep = 48, a0 = cy - (astep * (agents.length - 1)) / 2;
  b += line('M' + (card.x + card.w) + ' ' + cy + 'H' + rightBus, c.spoke);
  b += line('M' + rightBus + ' ' + a0 + 'V' + (a0 + astep * (agents.length - 1)), c.spoke);
  agents.forEach(([k, name, kind], i) => {
    const y = a0 + i * astep;
    b += line('M' + rightBus + ' ' + y + 'H928', c.spoke) + dot(928, y, 3, c.spokeActive);
    b += tile(k, 944, y - 16, 32);
    b += text(name, 988, y + 5, { size: 15, weight: 600, fill: c.foreground });
    b += text(kind, 988 + measure(name, { size: 15, weight: 600 }) + 8, y + 5, { size: 14, fill: c.mutedForeground });
  });
  return render('A SwB project connects Slack, Discord, Telegram and GitHub to agents', b);
}

function flowMessage(c, mode) {
  const W = 1280, H = 520;
  const { tile, mark, render } = canvas(W, H, c);
  const lanes = [['slack', 'You in Slack', 200], ['swb', 'SwB', 500], ['dots', 'ChatGPT dot', 800], ['claude', 'Claude Project', 1100]];
  let b = '';
  for (const [k, label, x] of lanes) {
    const x0 = x - (38 + measure(label, { size: 15, weight: 600 })) / 2;
    b += k === 'swb' ? mark(x0, 42, 28, mode) : tile(k, x0, 42, 28);
    b += text(label, x0 + 38, 61, { size: 15, weight: 600, fill: c.foreground });
    b += line('M' + x + ' 96V' + (H - 40), c.border, 1);
  }
  const L = Object.fromEntries(lanes.map(([k, , x]) => [k, x]));
  const rows = [
    ['slack', 'swb', '@swb dots, compare the two pricing drafts'],
    ['swb', null, 'Slack user id marks the owner'],
    ['swb', 'dots', 'MCP event wakes the dot'],
    ['dots', 'swb', 'get_message, then memory_write', 'mono'],
    ['dots', 'swb', 'reply', 'mono'],
    ['swb', 'slack', 'Answer in the thread as the dot'],
    ['claude', 'swb', 'memory_search', 'mono'],
    ['swb', 'claude', 'The dot\u2019s note with its source'],
  ];
  rows.forEach(([from, to, label, family = 'sans'], i) => {
    const y = 140 + i * 44;
    const style = { size: family === 'mono' ? 13 : 14, family, fill: c.foreground };
    if (!to) {
      b += dot(L[from], y, 4.5, c.foreground) + text(label, L[from] + 16, y + 5, { ...style, fill: c.mutedForeground });
      return;
    }
    b += line('M' + L[from] + ' ' + y + 'H' + L[to], c.spokeActive) + dot(L[from], y, 2.5, c.spokeActive) + dot(L[to], y, 4.5, c.foreground);
    b += text(label, (L[from] + L[to]) / 2, y - 11, { ...style, anchor: 'middle' });
  });
  return render('SwB carries a Slack message to a ChatGPT dot and returns the answer to Slack', b);
}

function flowArchitecture(c) {
  const W = 1280, X = 64, colW = (W - 2 * X) / 5, itemW = colW - 28, rowH = 30, top = 140;
  const agents = [
    ['claude', 'Claude Projects'], ['dots', 'ChatGPT dots'], ['capy', 'Capy Captains'], ['grok', 'Grok'], ['grok-bot', 'Grok Bot'],
    ['muse', 'Muse'], ['claude-code', 'Claude Code'], ['codex', 'Codex'], ['slack', 'People in Slack'], ['discord', 'Discord'], ['telegram', 'Telegram'],
  ];
  const H = top + agents.length * rowH + 112;
  const { tile, render } = canvas(W, H, c);
  const cols = [
    ['Agents and people', agents],
    ['Identity and trust', ['Platform user ids', 'OAuth 2.1 and keys', 'Trust per sender', 'Others\u2019 text wrapped']],
    ['Targets and routes', ['Targets by slug', 'Routes per agent', 'Routes per project', 'Block addresses']],
    ['Conversations and mailboxes', ['A handle per send', 'Agent mailboxes', 'Platform threads']],
    ['Wakes and deliveries', [['plug', 'MCP events'], ['terminal', 'Routine fires'], ['capy', 'Capy threads'], ['muse', 'Muse hook'], ['send', 'Thread replies']]],
  ];
  let b = line('M' + X + ' 64H' + (X + 4 * colW), c.spoke);
  cols.forEach(([head, items], i) => {
    const x = X + i * colW;
    b += dot(x, 64, 4.5, c.foreground);
    b += text(head, x, 108, { size: 15, weight: 600, fill: c.foreground });
    items.forEach((item, j) => {
      const y = top + j * rowH;
      const [kind, label] = Array.isArray(item) ? item : [null, item];
      if (kind) b += lucide[kind] ? icon(kind, x + 1, y - 1, 18, c.mutedForeground) : tile(kind, x, y - 2, 20);
      b += text(label, kind ? x + 30 : x, y + 13, { size: 14, fill: c.foreground });
      if (j < items.length - 1) b += line('M' + x + ' ' + (y + rowH - 7) + 'H' + (x + itemW), c.border, 1);
    });
  });
  const by = H - 88, bh = 60;
  b += '<rect x="' + X + '" y="' + by + '" width="' + (W - 2 * X) + '" height="' + bh + '" rx="' + RADIUS.lg + '" fill="' + c.muted + '"/>';
  b += text('Projects and scoped memory', X + 20, by + 26, { size: 15, weight: 600, fill: c.foreground });
  b += text('project:<slug> and general', X + 20, by + 45, { size: 12, family: 'mono', fill: c.mutedForeground });
  [['Visibility', 'Private until shared'], ['Turn cap', 'Per conversation'], ['Audit', 'Every action']].forEach(([k, v], i) => {
    const x = X + (i + 2) * colW;
    b += text(k, x, by + 26, { size: 14, weight: 600, fill: c.foreground });
    b += text(v, x, by + 45, { size: 13, fill: c.mutedForeground });
  });
  return render('Agents and people pass through identity and trust, targets and routes, and conversations and mailboxes, out to wakes and deliveries', b);
}

for (const [mode, tokens] of [['light', light], ['dark', dark]]) {
  const c = palette(tokens);
  for (const [name, draw] of [['hero', hero], ['connected', connected], ['flow-message', flowMessage], ['flow-architecture', flowArchitecture]]) {
    writeFileSync(join(out, name + '-' + mode + '.svg'), draw(c, mode));
  }
}
