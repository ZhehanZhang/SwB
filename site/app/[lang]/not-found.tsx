'use client';

import { House } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { isLocale, localize } from '@/lib/i18n';

const copy = {
  en: { title: 'Page missing', home: 'Home' },
  'zh-Hans': { title: '页面失效', home: '首页' },
  'zh-Hant': { title: '頁面失效', home: '首頁' },
  es: { title: 'Página no disponible', home: 'Inicio' },
  fr: { title: 'Page indisponible', home: 'Accueil' },
  de: { title: 'Seite nicht verfügbar', home: 'Startseite' },
  ko: { title: '페이지를 찾을 수 없습니다', home: '홈' },
  ja: { title: 'ページが見つかりません', home: 'ホーム' },
};

export default function NotFound() {
  const params = useParams<{ lang: string }>();
  const lang = params.lang && isLocale(params.lang) ? params.lang : 'en';
  return (
    <main className="mx-auto flex min-h-dvh max-w-3xl flex-col items-start justify-center gap-4 px-5">
      <h1 className="text-display-xl">{copy[lang].title}</h1>
      <Link href={localize(lang, '/')} className="swb-button swb-button-outline"><House aria-hidden="true" />{copy[lang].home}</Link>
    </main>
  );
}
