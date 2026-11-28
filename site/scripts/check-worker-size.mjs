import { readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';

const bundle = readFileSync(process.argv[2] ?? '.open-next/size-check/index.js');
const compressed = gzipSync(bundle).length;
const limit = 3 * 1024 * 1024;
console.log(`Worker bundle ${(bundle.length / 1024).toFixed(2)} KiB, gzip ${(compressed / 1024).toFixed(2)} KiB.`);
if (compressed >= limit) throw new Error('The compressed Worker bundle must stay under 3 MiB.');
