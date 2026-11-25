const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { root, origin, variants } = require('./config.cjs');
const { tree } = require('./utils.cjs');
const ts = require(path.join(root, 'A/frontend/node_modules/typescript'));
if(fs.existsSync(path.join(root,'results/source-prepared.json'))) throw new Error('Already patched; refusing a second patch');
const report = {}, prepared = {};
function replace(file, before, after) {
  const value = fs.readFileSync(file, 'utf8').replaceAll('\r\n', '\n'); assert.ok(value.includes(before), 'Missing patch location in ' + file);
  fs.writeFileSync(file, value.replace(before, after));
}
function functionBodies(file, bodies) {
  const value = fs.readFileSync(file, 'utf8').replaceAll('\r\n', '\n');
  const ast = ts.createSourceFile(file, value, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX), edits = [];
  function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name && bodies[node.name.text]) edits.push({ start: node.body.getStart(ast), end: node.body.end, text: bodies[node.name.text] });
    else ts.forEachChild(node, visit);
  }
  visit(ast); assert.equal(edits.length, Object.keys(bodies).length);
  let output = value; for (const edit of edits.sort((a,b) => b.start-a.start)) output = output.slice(0,edit.start)+edit.text+output.slice(edit.end);
  fs.writeFileSync(file, output);
}
for (const [name] of Object.entries(variants)) {
  const frontend = path.join(root, name, 'frontend'), file = relative => path.join(frontend, relative);
  replace(file('lib/api.ts'), 'export const apiBaseUrl = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\\/$/, "");',
    '// Isolation experiment only: no production API fallback.\nexport const apiBaseUrl = ' + JSON.stringify(origin) + ';');
  let services = fs.readFileSync(file('lib/pivot/services.ts'), 'utf8').replaceAll('\r\n','\n');
  const start = services.indexOf('const approvedRemoteApis = new Map(['), end = services.indexOf('\n]);',start)+4;
  assert.ok(start > 0 && end > start);
  services = services.slice(0,start)+services.slice(end);
  services = services.replace('    if (approvedRemoteApis.get(browser.origin) === api.origin) return true;\n    return browser.protocol === api.protocol && isLocalApiAllowed(localEnabled, browser.hostname, baseUrl);',
    '    return localEnabled && browser.origin === ' + JSON.stringify(origin) + ' && api.origin === browser.origin;');
  assert.ok(!services.includes('approvedRemoteApis')); fs.writeFileSync(file('lib/pivot/services.ts'),services);
  replace(file('components/pivot/home-screen.tsx'), '<section aria-label={label} className={styles.feed}>', '<section aria-label={label} className={styles.feed} data-lab-received-cards={cards.length}>');
  if (name === 'C') replace(file('components/pivot/home-screen.tsx'), '<ul>{cards.map((card, index)', '<ul>{cards.slice(0, 1).map((card, index)');
  if (name === 'B') {
    functionBodies(file('components/pivot/content-carousel.tsx'), {
      requestProtectedImage: '{ publish({ status: "error", message: "이미지 제외 실험" }); return () => {}; }',
      ProtectedSlideImage: '{ return <span className={styles.imageFrame} data-lab-image-disabled="protected" role="img" aria-label="이미지 제외 실험" />; }',
      SlideImage: '{ return <span className={styles.imageState} data-lab-image-disabled="slide" role="img" aria-label="이미지 제외 실험" />; }',
    });
    fs.writeFileSync(file('components/pivot/home-entry-image.tsx'), '"use client";\nimport { useEffect, type ReactNode } from "react";\n// Experiment B deliberately bypasses image readiness; it is not production behavior.\nexport function HomeEntryImage({children,onReady}:{children:ReactNode;onReady?:()=>void}) { useEffect(()=>{onReady?.();},[onReady]); return <>{children}</>; }\n');
    replace(file('lib/pivot/feed-warmup.ts'), 'preload: url => new Promise(resolve => { const image = new Image(); image.onload = image.onerror = () => resolve(); image.src = url; }),', 'preload: async () => {}, // Experiment B: no Image construction or preload.');
    const warm = file('lib/pivot/feed-warmup.ts');
    let warmValue = fs.readFileSync(warm, 'utf8');
    const p0 = warmValue.indexOf('      const pictures ='), p1 = warmValue.indexOf('      await Promise.all(pictures.map(deps.preload));',p0);
    assert.ok(p0 > 0 && p1 > p0); warmValue = warmValue.slice(0,p0) + '      void page; // Retain identical feed request, exclude all preload calls.\n' + warmValue.slice(p1+'      await Promise.all(pictures.map(deps.preload));'.length); fs.writeFileSync(warm,warmValue);
    const placeholder = 'import type { CSSProperties } from "react";\n// Test-only placeholder. Does not forward src/srcSet/ref/style image URLs or SVG children.\nexport function LabNoImage(props: any) { const style: CSSProperties = { display:"inline-block", width: props.width, height: props.height }; return <span className={props.className} style={style} hidden={props.hidden} aria-hidden={props["aria-hidden"]} aria-label={props.alt || props["aria-label"] || "이미지 제외"} data-lab-image-disabled="element" />; }\n';
    fs.writeFileSync(file('components/pivot/lab-no-image.tsx'),placeholder);
    const transformed = [];
    function walk(dir) { for (const entry of fs.readdirSync(dir,{withFileTypes:true})) {
      const full=path.join(dir,entry.name); if(entry.isDirectory()) walk(full);
      else if(entry.name.endsWith('.tsx') && !entry.name.includes('.test.') && entry.name !== 'lab-no-image.tsx') {
        const value=fs.readFileSync(full,'utf8').replaceAll('\r\n','\n'), ast=ts.createSourceFile(full,value,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX), edits=[];
        const importedImages = new Set();
        for(const statement of ast.statements) if(ts.isImportDeclaration(statement) && statement.moduleSpecifier.text==='next/image' && statement.importClause?.name) importedImages.add(statement.importClause.name.text);
        function visit(node) {
          const opening=ts.isJsxElement(node)?node.openingElement:ts.isJsxSelfClosingElement(node)?node:null;
          if(opening) {
            const tag=opening.tagName.getText(ast), attrs=opening.attributes.properties.filter(p=>!ts.isJsxAttribute(p)||(!p.name.getText(ast).startsWith('on')&&p.name.getText(ast)!=='ref')).map(p=>p.getText(ast)).join(' ');
            if(tag==='img'||tag==='svg'||tag==='image'||importedImages.has(tag)) { edits.push({start:node.getStart(ast),end:node.end,text:'<LabNoImage '+attrs+' />'}); return; }
            if(tag==='link' && /as\s*=\s*["']image["']/.test(attrs)) { edits.push({start:node.getStart(ast),end:node.end,text:'<></>'}); return; }
          }
          ts.forEachChild(node,visit);
        }
        visit(ast); if(edits.length) {
          let output=value; for(const edit of edits.sort((a,b)=>b.start-a.start)) output=output.slice(0,edit.start)+edit.text+output.slice(edit.end);
          output += '\nimport { LabNoImage } from "@/components/pivot/lab-no-image";\n'; fs.writeFileSync(full,output); transformed.push({file:path.relative(frontend,full).replaceAll('\\','/'),elements:edits.length});
        }
      } else if(entry.name.endsWith('.css')) {
        const value=fs.readFileSync(full,'utf8'); const output=value.replace(/url\(([^)]*)\)/g,(all,url)=>/\.(woff2?|ttf|otf)(["']|\?|$)/i.test(url)?all:'none');
        if(output!==value) {fs.writeFileSync(full,output); transformed.push({file:path.relative(frontend,full).replaceAll('\\','/'),cssImageUrlsRemoved:true});}
      }
    }}
    walk(file('components')); walk(file('app')); report.imageElementsRemoved = transformed;
  }
  prepared[name] = tree(frontend); delete prepared[name]['node_modules'];
  // Keep source manifests independent from build output and dependency content.
  prepared[name] = Object.fromEntries(Object.entries(prepared[name]).filter(([p])=>!p.startsWith('node_modules/')));
  console.log('Patched isolation snapshot ' + name);
}
fs.writeFileSync(path.join(root,'results/source-prepared.json'),JSON.stringify(prepared));
fs.writeFileSync(path.join(root,'results/patch-report.json'),JSON.stringify(report,null,2));
