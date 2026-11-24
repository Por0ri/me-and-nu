// Separate ignored copy; fixture entry points never enter production app/public.
const fs = require('node:fs'), path = require('node:path');
const source = path.resolve(__dirname, '..');
const target = path.resolve(source, '../.preview-pivot/admin-notices-1118/frontend');
fs.mkdirSync(target, { recursive: true });
fs.cpSync(source, target, { recursive: true, filter: file => {
  const name = path.basename(file);
  return !['node_modules', '.next', 'dev'].includes(name) && !name.startsWith('.env') && !name.endsWith('.tsbuildinfo');
} });
if (!fs.existsSync(path.join(target, 'node_modules'))) fs.symlinkSync(path.join(source, 'node_modules'), path.join(target, 'node_modules'), 'junction');
for (const [route, component, module] of [
  ['notices/page.tsx', 'FixtureNoticesPage', 'admin-notices-page'],
  ['reports/page.tsx', 'FixtureReportsPage', 'admin-reports-page'],
  ['users/page.tsx', 'FixtureMembersPage', 'admin-members-pages'],
  ['users/[memberId]/page.tsx', 'FixtureMemberDetailPage', 'admin-members-pages'],
]) fs.writeFileSync(path.join(target, 'app/admin', route), `import { Suspense } from 'react';\nimport { ${component} } from '@/tests/fixtures/${module}';\nexport default function Page() { return <Suspense><${component} /></Suspense>; }\n`);
fs.copyFileSync(path.join(source, 'tests/fixtures/assets/report-cover.png'), path.join(target, 'public/admin/report-fixture-cover.png'));
console.log(target);
