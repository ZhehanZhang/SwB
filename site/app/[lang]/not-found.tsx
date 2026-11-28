'use client';

import { House } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { SiteHeader } from '@/components/site-header';
import { isLocale, localize } from '@/lib/i18n';
import { repository } from '@/lib/urls';

const copy = {
  en: { title: 'Page missing', home: 'Home' },
  'zh-Hans': { title: '页面不存在', home: '返回首页' },
  'zh-Hant': { title: '找不到頁面', home: '返回首頁' },
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
    <>
      <SiteHeader lang={lang} repository={repository} />
      <main className="mx-auto flex min-h-dvh max-w-3xl flex-col items-start justify-center gap-4 px-5">
        <h1 className="text-display-xl">{copy[lang].title}</h1>
        <Link href={localize(lang, '/')} className="swb-button swb-button-outline"><House aria-hidden="true" />{copy[lang].home}</Link>
      </main>
    </>
  );
}
