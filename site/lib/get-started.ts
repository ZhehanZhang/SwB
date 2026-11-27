import { setup } from './setup';
import { setupChoices } from './setup-picker';

export const runLocations = ['local', 'server', 'cloud'] as const;
export type RunLocation = (typeof runLocations)[number];

const aliases = { dots: 'chatgpt-dots', 'capy-captain': 'capy', 'gemini-cli': 'gemini' } as const;

export const getStartedAgents = setupChoices.flatMap((choice) => {
  if (choice.id === 'companion') return [];
  const id = aliases[choice.id as keyof typeof aliases] ?? choice.id;
  const details = choice.id === 'claude'
    ? { open: ['claude'] as const, docs: '/docs/setup/claude' }
    : setup[choice.mark]!;
  return [{ id, name: choice.name, mark: choice.mark, prompt: choice.prompt, open: details.open, docs: details.docs }];
});

export type GetStartedAgent = (typeof getStartedAgents)[number]['id'];
export const getStartedAgentIds = getStartedAgents.map(({ id }) => id);
export const getStartedStyleId = 'swb-get-started-initial';
export const getStartedStorageKey = 'swb-get-started-address';

export function validSwbOrigin(value: string | null) {
  if (!value) return null;
  try {
    const address = new URL(value.trim());
    return (address.protocol === 'http:' || address.protocol === 'https:') && !address.username && !address.password
      ? address.origin
      : null;
  } catch {
    return null;
  }
}

export function personalizePrompt(prompt: string, address: string | null) {
  return address ? prompt.replace(/(?:https:\/\/)?swb\.example\.com/g, () => address) : prompt;
}

function parseSelection<Id extends string>(
  search: string, ids: readonly Id[], replacements: Readonly<Record<string, string>>,
): { run: RunLocation; agents: Id[] } {
  const params = new URLSearchParams(search);
  const run = params.get('run');
  const agents = (params.get('agents') ?? '').split(',').flatMap((id) => {
    const match = ids.find((allowed) => allowed === (replacements[id] ?? id));
    return match ? [match] : [];
  });
  return { run: run === 'server' || run === 'cloud' ? run : 'local', agents: [...new Set(agents)] };
}

export function readGetStartedSelection(search: string) {
  return parseSelection(search, getStartedAgentIds, aliases);
}

export function initialGetStartedScript() {
  return `(() => {
    const { run, agents } = (${parseSelection.toString()})(location.search, ${JSON.stringify(getStartedAgentIds)}, ${JSON.stringify(aliases)});
    const sheet = document.getElementById(${JSON.stringify(getStartedStyleId)})?.sheet;
    if (!sheet) return;
    sheet.insertRule('[data-get-started] [data-start-agent] { display: none; }');
    agents.forEach((id, index) => sheet.insertRule('[data-get-started] [data-start-agent="' + id + '"] { display: grid; order: ' + index + '; }', sheet.cssRules.length));
    sheet.insertRule('[data-get-started] [data-start-run] { visibility: hidden; grid-template-rows: 0fr; }', sheet.cssRules.length);
    sheet.insertRule('[data-get-started] [data-start-run="' + run + '"] { visibility: visible; grid-template-rows: 1fr; }', sheet.cssRules.length);
  })();`;
}

export const computerCommand = 'uvx swb-one up';
export const pipxCommand = 'pipx install swb-one\nswb up';
export const serverCommand = String.raw`git clone --branch release/1.0 https://github.com/ZhehanZhang/SwB.git
cd SwB
(umask 077; cp .env.example .env)
{
  printf 'SWB_UID=%s\n' "$(id -u)"
  printf 'SWB_KEY_OWNER='
  openssl rand -hex 32
} >> .env
printf 'SWB_PUBLIC_URL=http://localhost:8870\n' >> .env
mkdir -p data
docker compose up -d --build --wait
curl -fsS http://127.0.0.1:8870/healthz
docker compose exec -T switchboard swb login-link --url http://127.0.0.1:8000`;
