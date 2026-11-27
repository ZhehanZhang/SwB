import type { Locale } from '@/lib/i18n';
import { readPrompt } from '@/lib/prompts';
import { setupSourceIds, type SetupSources } from '@/lib/setup-picker';
import { setupPickerCopy } from '@/lib/setup-picker-copy';
import { SetupPickerControls } from './setup-picker-controls';

export function SetupPicker({ lang, modal = false }: { lang: Locale; modal?: boolean }) {
  const sources = Object.fromEntries(setupSourceIds.map((id) => [id, readPrompt(id)])) as SetupSources;
  return <SetupPickerControls sources={sources} labels={setupPickerCopy[lang]} modal={modal} />;
}
