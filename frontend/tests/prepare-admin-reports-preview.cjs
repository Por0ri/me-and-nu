// Separate ignored checkout: fixture imports/assets never enter production app/public.
const fs = require('node:fs');
const path = require('node:path');
const source = path.resolve(__dirname, '..');
const target = path.resolve(source, '../.preview-pivot/admin-reports-1118/frontend');
fs.mkdirSync(target, { recursive: true });
fs.cpSync(source, target, { recursive: true, filter: file => {
  const name = path.basename(file);
  return !['node_modules', '.next', 'dev'].includes(name) && !name.startsWith('.env') && !name.endsWith('.tsbuildinfo');
} });
if (!fs.existsSync(path.join(target, 'node_modules'))) fs.symlinkSync(path.join(source, 'node_modules'), path.join(target, 'node_modules'), 'junction');
fs.writeFileSync(path.join(target, 'app/admin/reports/page.tsx'), `import { Suspense } from 'react';\nimport { FixtureReportsPage } from '@/tests/fixtures/admin-reports-page';\nexport default function Page() { return <Suspense><FixtureReportsPage /></Suspense>; }\n`);
fs.copyFileSync(path.join(source, 'tests/fixtures/assets/report-cover.png'), path.join(target, 'public/admin/report-fixture-cover.png'));
for (const [file, component] of [['page.tsx', 'FixtureMembersPage'], ['[memberId]/page.tsx', 'FixtureMemberDetailPage']]) {
  fs.writeFileSync(path.join(target, 'app/admin/users', file), `import { Suspense } from 'react';\nimport { ${component} } from '@/tests/fixtures/admin-members-pages';\nexport default function Page() { return <Suspense><${component} /></Suspense>; }\n`);
}
console.log(target);
