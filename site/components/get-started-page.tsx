import type { Locale } from '@/lib/i18n';
import { getStartedAgents, getStartedStyleId, initialGetStartedScript } from '@/lib/get-started';
import { readPromptSource } from '@/lib/prompts';
import { DeployProviders } from './deploy-providers';
import { GetStarted } from './get-started';

export function GetStartedPage({ lang }: { lang: Locale }) {
  const prompts = Object.fromEntries([...new Set(getStartedAgents.map(({ prompt }) => prompt))]
    .map((name) => [name, readPromptSource(name)]));

  return (
    <>
      <style id={getStartedStyleId} />
      <script dangerouslySetInnerHTML={{ __html: initialGetStartedScript() }} />
      <GetStarted lang={lang} prompts={prompts} cloud={<DeployProviders lang={lang} />} />
    </>
  );
}
