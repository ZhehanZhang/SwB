# SwB writing style

This guide covers every public text: the docs, the website, the README, the UI, release notes and promo material.

## Words

Write in ASD-STE100 Simplified Technical English. Use one word for one meaning, and use the same word each time. Define each technical term once, in plain words, where it first appears. Use common words in place of jargon.

## Sentences

Write short positive sentences. A procedure sentence has 20 words at most. A description sentence has 25 words at most. Give one instruction in each sentence. Write steps in the imperative and in the active voice.

## Banned forms

Use none of these in prose. Negations such as no, not, never, without and doesn't. Colons. Semicolons. Em dashes. The middle dot divider. Bulleted lists and numbered lists. Eyebrows, which are small labels above a heading. Secondary lines under a heading. Text that repeats what the layout already shows.

Code blocks, inline code, commands, config keys and URLs keep their own syntax.

## Structure

Carry structure with headings, short paragraphs and tables. Use tabs for alternatives such as install methods. Use a table for a set of parallel facts.

## Chinese

The Simplified Chinese and Traditional Chinese docs follow the same rules. Write them natively, with short plain sentences and one idea per sentence. Keep the terms consistent across pages. Simplified Chinese uses mainland terms. Traditional Chinese uses Taiwan terms. Use full-width punctuation, and use none of the full-width colon, semicolon or dash in prose.

## Check

Run `node site/scripts/check-style.mjs` before you open a pull request. The script flags the banned forms in prose.
