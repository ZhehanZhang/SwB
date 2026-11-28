import { Check } from 'lucide-react';
import { MarkTile, type MarkKind } from './mark-tile';
import styles from './app-picker.module.css';

type App<Id extends string> = { id: Id; name: string; mark: MarkKind };

type Props<Id extends string> = {
  apps: readonly App<Id>[];
  selected: readonly Id[];
  onChange: (id: Id, selected: boolean) => void;
} & (
  | { mode: 'toggle'; labelledBy: string }
  | { mode: 'checkbox'; label: string }
);

export function AppPicker<Id extends string>(props: Props<Id>) {
  const tiles = props.apps.map(({ id, name, mark }) => {
    const selected = props.selected.includes(id);
    const content = (
      <>
        <MarkTile kind={mark} className={styles.mark} />
        <span data-app-picker-name>{name}</span>
        <Check className={styles.check} size={14} aria-hidden="true" />
      </>
    );

    if (props.mode === 'toggle') {
      return (
        <button key={id} type="button" className={styles.tile} data-selected={selected}
          aria-pressed={selected} aria-controls={id} onClick={() => props.onChange(id, !selected)}>
          {content}
        </button>
      );
    }

    return (
      <label key={id} className={styles.tile} data-selected={selected}>
        <input className={styles.checkbox} type="checkbox" checked={selected}
          onChange={({ currentTarget }) => props.onChange(id, currentTarget.checked)} />
        {content}
      </label>
    );
  });

  return props.mode === 'toggle' ? (
    <div className={styles.grid} data-app-picker={props.mode} role="group" aria-labelledby={props.labelledBy}>
      {tiles}
    </div>
  ) : (
    <fieldset className={styles.grid} data-app-picker={props.mode}>
      <legend className="sr-only">{props.label}</legend>
      {tiles}
    </fieldset>
  );
}
