import localFont from 'next/font/local';

export const swbInter = localFont({
  src: '../node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2',
  weight: '100 900',
  variable: '--font-inter',
  adjustFontFallback: 'Arial',
  display: 'optional',
});

export const interTight = localFont({
  src: '../node_modules/@fontsource-variable/inter-tight/files/inter-tight-latin-wght-normal.woff2',
  weight: '100 900',
  variable: '--font-inter-tight',
  adjustFontFallback: 'Arial',
});

export const jetbrainsMono = localFont({
  src: '../node_modules/@fontsource-variable/jetbrains-mono/files/jetbrains-mono-latin-wght-normal.woff2',
  weight: '100 800',
  variable: '--font-jetbrains',
  adjustFontFallback: false,
  display: 'optional',
});
