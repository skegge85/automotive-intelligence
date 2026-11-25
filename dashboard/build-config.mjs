import { writeFileSync } from 'node:fs';

const url = new URL(process.env.SUPABASE_URL);
const key = process.env.SUPABASE_PUBLISHABLE_KEY || '';
if (url.protocol !== 'https:' || !url.hostname.endsWith('.supabase.co') ||
    !key.startsWith('sb_publishable_')) {
  throw new Error('Set the project URL and its sb_publishable_ key before deploying.');
}
writeFileSync(new URL('./config.js', import.meta.url),
  'window.INTELLIGENCE_CONFIG = ' + JSON.stringify({ url: url.origin, key }) + ';\n');
